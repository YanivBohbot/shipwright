"""Drive a Shipwright run to completion with scripted human decisions, and build the offline
scenario environment (fake Jira/GitHub/web, local workspace on a copy of the demo repo).

Shared by the integration tests (`tests/integration/helpers.py` re-exports it) and the
evaluation harness (`evals/harness.py`); importable without the `tests` package.
"""

from __future__ import annotations

import inspect
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import Command

ROOT = Path(__file__).resolve().parents[3]
DEMO_REPO = ROOT / "demo" / "taskboard-api"
KNOWLEDGE_DIR = ROOT / "knowledge"
IGNORE = shutil.ignore_patterns(".git", ".venv", ".pytest_cache", "__pycache__")
MAX_PAUSES = 12
_PASSED_RE = re.compile(r"\b([1-9]\d*) passed\b")
_FAILED_RE = re.compile(r"\b([1-9]\d*) (failed|errors?)\b")


@dataclass
class RunOutcome:
    interrupts: list[dict[str, Any]] = field(default_factory=list)  # every pause, in order
    subagents_called: list[str] = field(default_factory=list)  # `task` subagent_type, in order
    tool_calls: list[str] = field(default_factory=list)  # every orchestrator tool call name
    final_message: str = ""


def is_green(test_report: str) -> bool:
    """pytest counts: at least one passed, no non-zero failed/error count, no failing status."""
    return (
        bool(_PASSED_RE.search(test_report))
        and not _FAILED_RE.search(test_report)
        and "TESTS_FAILING" not in test_report
    )


def extract_outcome(messages: list[Any], interrupts: list[dict[str, Any]]) -> RunOutcome:
    """Summarise the orchestrator's messages (subagent-internal calls are not visible here)."""
    outcome = RunOutcome(interrupts=list(interrupts))
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for call in msg.tool_calls:
            outcome.tool_calls.append(call["name"])
            if call["name"] == "task":
                subagent = (call.get("args") or {}).get("subagent_type")
                if subagent:
                    outcome.subagents_called.append(str(subagent))
    final = next(
        (m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls), None
    )
    outcome.final_message = final.text if final is not None else ""
    return outcome


async def drive(
    agent: Any,
    ticket_key: str,
    *,
    decide: Callable[[dict[str, Any]], Any],
    config: dict[str, Any],
    context: Any,
    max_pauses: int = MAX_PAUSES,
) -> RunOutcome:
    """Invoke the agent on a ticket, resolving every interrupt with `decide` until it ends.

    `decide` receives an interrupt value and returns the resume value for it. Raises
    RuntimeError after `max_pauses` pauses (a runaway loop).
    """
    run_input: Any = {
        "messages": [{"role": "user", "content": f"Process Jira ticket {ticket_key}."}]
    }
    interrupts: list[dict[str, Any]] = []
    for _ in range(max_pauses + 1):
        await agent.ainvoke(run_input, config, context=context)
        state = await agent.aget_state(config)
        if not state.interrupts:
            return extract_outcome(state.values.get("messages", []), interrupts)
        answers: dict[str, Any] = {}
        for pending in state.interrupts:
            interrupts.append(pending.value)
            answer = decide(pending.value)
            answers[pending.id] = await answer if inspect.isawaitable(answer) else answer
        run_input = Command(resume=next(iter(answers.values())) if len(answers) == 1 else answers)
    raise RuntimeError(f"Run still paused after {max_pauses} pauses")


# ------------------------------------------------------------------ scenario environment


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


async def make_local_workspace(
    tmp_path: Path, repo: str = "acme/taskboard-api", *, source: Path = DEMO_REPO
) -> Any:
    """Copy `source` to `tmp_path/origin` (git init + commit) and clone it into a local-provider
    workspace via `Workspace(clone_url=...)`. Tests/evals only; production always clones
    https://github.com/{repo}.git."""
    from deepagents.backends import LocalShellBackend

    from shipwright.workspace import CLONE_TIMEOUT, Workspace, clean_shell_env

    origin = tmp_path / "origin"
    shutil.copytree(source, origin, ignore=IGNORE)
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "core.autocrlf", "false")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "init")
    root = tmp_path / "ws"
    root.mkdir()
    backend = LocalShellBackend(
        root_dir=root, virtual_mode=True, env=clean_shell_env(root), timeout=CLONE_TIMEOUT
    )
    workspace = Workspace(
        backend,
        repo,
        shell_repo_dir=str(root / "workspace" / "repo"),
        posix_shell=os.name != "nt",
        mkdir_parents=False,
        clone_url=origin.as_uri(),
    )
    await workspace.ensure_cloned()
    return workspace


def scenario_ticket(scenario: int, key: str) -> Any:
    """The demo ticket of `scenario` as a Jira `Ticket` with the given key."""
    from shipwright.demo_tickets import load_demo_tickets
    from shipwright.tickets import Ticket, extract_acceptance_criteria, extract_spec_refs

    demo = next(t for t in load_demo_tickets() if t.scenario == scenario)
    return Ticket(
        key=key,
        summary=demo.summary,
        description=demo.description,
        status="To Do",
        acceptance_criteria=extract_acceptance_criteria(demo.description),
        spec_refs=extract_spec_refs(demo.description),
        issue_type=demo.issue_type,
    )


async def knowledge_store() -> Any:
    """FakeVectorStore holding the repo's `knowledge/` markdown (PDFs skipped: no LlamaParse)."""
    from shipwright.evaluation.fakes import FakeVectorStore, settings_stub
    from shipwright.knowledge.ingest import ingest_directory

    store = FakeVectorStore()
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(
            KNOWLEDGE_DIR, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.pdf")
        )
        await ingest_directory(Path(tmp), settings_stub(), vector_store=store)
    return store


@dataclass
class ScenarioEnv:
    agent: Any
    jira: Any
    github: Any
    web: Any
    store: Any
    workspace: Any
    settings: Any
    deps: Any
    key: str
    context: Any

    def new_config(self) -> dict[str, Any]:
        return {"configurable": {"thread_id": f"it-{uuid.uuid4().hex[:8]}"}}


async def build_scenario_env(
    tmp_path: Path,
    scenario: int,
    *,
    user_id: str = "alice",
    model: Any = None,
    settings: Any = None,
) -> ScenarioEnv:
    """Fake Jira/GitHub/web, local workspace on a copy of the demo repo.

    Settings and model come from `.env` unless `settings` / `model` are given (unit tests pass
    a settings stub and a scripted model).
    """
    from dotenv import load_dotenv
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from shipwright.agent import Dependencies, build_agent
    from shipwright.config import get_settings
    from shipwright.context import ShipwrightContext
    from shipwright.evaluation.fakes import FakeGitHubGateway, FakeJiraGateway, FakeWebTool
    from shipwright.knowledge.search import KnowledgeSearcher
    from shipwright.middleware.audit import MemoryAuditSink
    from shipwright.policies import seed_policies

    if settings is None:
        load_dotenv()
        settings = get_settings().model_copy(update={"shipwright_sandbox_provider": "local"})
    workspace = await make_local_workspace(tmp_path, settings.shipwright_repo)
    key = f"{settings.jira_project}-{scenario}"
    jira = FakeJiraGateway({key: scenario_ticket(scenario, key)})
    github = FakeGitHubGateway(branch_shas={"main": await workspace.base_sha()})
    web = FakeWebTool()
    deps = Dependencies(
        jira=jira,
        github=github,
        searcher=KnowledgeSearcher(await knowledge_store()),
        web_tool=web.tool,
        audit_sink=MemoryAuditSink(),
    )
    store = InMemoryStore()
    await seed_policies(store, settings)
    agent = build_agent(
        settings, deps, workspace, checkpointer=InMemorySaver(), store=store, model=model
    )
    return ScenarioEnv(
        agent=agent,
        jira=jira,
        github=github,
        web=web,
        store=store,
        workspace=workspace,
        settings=settings,
        deps=deps,
        key=key,
        context=ShipwrightContext(user_id=user_id),
    )
