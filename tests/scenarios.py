"""Test/eval-only harness: drive a run with scripted human decisions, offline scenario setup.

Shared by `tests/unit/test_scenarios_harness.py`, the `llm` integration tests and `evals/`.
A scenario environment is the REAL agent (`build_shipwright_agent`) on a real local workspace
(a copy of demo/taskboard-api), with recording fakes for Jira / GitHub / web search.
"""

from __future__ import annotations

import inspect
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import Command

from shipwright.agent.hitl import hitl_resume, review_gate_resume

ROOT = Path(__file__).resolve().parents[1]
DEMO_REPO = ROOT / "demo" / "taskboard-api"
TICKETS = ROOT / "demo" / "tickets.yaml"
IGNORE = shutil.ignore_patterns(".git", ".venv", ".pytest_cache", "__pycache__")
MAX_PAUSES = 12
_PASSED_RE = re.compile(r"\b([1-9]\d*) passed\b")
_FAILED_RE = re.compile(r"\b([1-9]\d*) (failed|errors?)\b")

Decider = Callable[[dict[str, Any]], Any]
S6_FEEDBACK = "Use a dataclass for the health payload, not a dict"


# ------------------------------------------------------------------ driving a run


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
    outcome = RunOutcome(interrupts=list(interrupts))
    for msg in messages:
        for call in msg.tool_calls if isinstance(msg, AIMessage) else []:
            outcome.tool_calls.append(call["name"])
            if call["name"] == "task" and (sub := (call.get("args") or {}).get("subagent_type")):
                outcome.subagents_called.append(str(sub))
    final = next(
        (m for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls), None
    )
    outcome.final_message = final.text if final is not None else ""
    return outcome


async def drive(
    agent: Any,
    ticket_key: str,
    *,
    decide: Decider,
    config: dict[str, Any],
    context: Any,
    max_pauses: int = MAX_PAUSES,
) -> RunOutcome:
    """Run the agent on a ticket, answering every interrupt with `decide`, like main.py does."""
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


# ------------------------------------------------------------------ scripted human decisions


def scripted_decider(gate: Sequence[tuple[str, str]], hitl: str = "approve") -> Decider:
    """Answer review gates from `gate` in order (the last repeats); every `interrupt_on` pause
    with `hitl`. Stateful: one decider per run."""
    if not gate:
        raise ValueError("gate needs at least one (choice, message) step")
    steps, seen = list(gate), [0]

    def decide(value: dict[str, Any]) -> Any:
        if value.get("kind") == "review_gate":
            choice, message = steps[min(seen[0], len(steps) - 1)]
            seen[0] += 1
            return review_gate_resume(value, choice, message)
        if isinstance(value.get("action_requests"), list):
            return hitl_resume(value, [hitl] * len(value["action_requests"]))
        raise ValueError(f"Unsupported interrupt payload: {value!r}")

    return decide


def approve_all() -> Decider:
    return scripted_decider([("approve", "")])


SCENARIO_DECISIONS: dict[int, Callable[[], Decider]] = {
    1: approve_all,
    2: approve_all,
    3: approve_all,
    4: lambda: scripted_decider([("reject", "Not expected.")]),  # ambiguous: no gate expected
    5: approve_all,  # failing tests: approve anyway -> draft PR
    6: lambda: scripted_decider([("feedback", S6_FEEDBACK), ("approve", "")]),
    7: lambda: scripted_decider([("reject", "Not expected.")]),  # oversized: subtasks only
}


def decider_for(scenario: int) -> Decider:
    try:
        return SCENARIO_DECISIONS[scenario]()
    except KeyError:
        raise ValueError(f"No scripted decisions for scenario {scenario}") from None


# ------------------------------------------------------------------ offline environment


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def make_demo_dir(tmp_path: Path) -> Path:
    """A private `demo/`: tickets.yaml + a fresh git repo copied from demo/taskboard-api."""
    demo = tmp_path / "demo"
    source = demo / "taskboard-api"
    shutil.copytree(DEMO_REPO, source, ignore=IGNORE)
    shutil.copy(TICKETS, demo / "tickets.yaml")
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "core.autocrlf", "false")
    git(source, "add", ".")
    git(source, "commit", "-q", "-m", "init")
    return demo


def scenario_ticket(scenario: int, key: str) -> Any:
    from shipwright.domain.tickets import Ticket, extract_acceptance_criteria, extract_spec_refs
    from shipwright.integrations.jira.local_jira import load_demo_tickets

    demo = next(t for t in load_demo_tickets(TICKETS) if t.scenario == scenario)
    return Ticket(
        key=key,
        summary=demo.summary,
        description=demo.description,
        status="To Do",
        acceptance_criteria=extract_acceptance_criteria(demo.description),
        spec_refs=extract_spec_refs(demo.description),
        issue_type=demo.issue_type,
    )


@dataclass
class ScenarioEnv:
    agent: Any
    deps: Any
    jira: Any
    github: Any
    web: Any
    store: Any
    workspace: Any
    settings: Any
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
    """Real agent + local workspace on a demo copy; fake Jira/GitHub/web. `settings` default:
    `.env` (evals and `llm` tests); unit tests pass `make_settings()` and a scripted model."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from shipwright.agent.dependencies import Dependencies
    from shipwright.agent.orchestrator import build_shipwright_agent
    from shipwright.agent.state import ShipwrightContext
    from shipwright.core.config import Settings
    from shipwright.integrations.github.local_github import ensure_bare_remote
    from shipwright.knowledge.search import LocalKeywordSearcher
    from shipwright.sandbox.providers import open_workspace
    from tests.fakes import FakeGitHubGateway, FakeJiraGateway, FakeWebTool, MemoryAuditSink

    demo = make_demo_dir(tmp_path)
    base = settings or Settings(shipwright_mode="local")
    settings = base.model_copy(update={"demo_dir": demo, "tmp_dir": tmp_path / "tmp"})
    ensure_bare_remote(settings.local_source_repo, settings.local_remote)
    workspace = await open_workspace(settings, thread_id=f"s{scenario}-{uuid.uuid4().hex[:6]}")
    await workspace.ensure_cloned()
    key = f"{settings.jira_project}-{scenario}"
    jira = FakeJiraGateway({key: scenario_ticket(scenario, key)})
    github = FakeGitHubGateway(branch_shas={"main": await workspace.base_sha()})
    web, store = FakeWebTool(), InMemoryStore()
    deps = Dependencies(
        settings=settings,
        workspace=workspace,
        jira=jira,
        github=github,
        searcher=LocalKeywordSearcher(settings.knowledge_dir),
        web_tool=web.tool,
        audit_sink=MemoryAuditSink(),
        model=model if model is not None else settings.model_strong,
        checkpointer=InMemorySaver(),
        store=store,
    )
    return ScenarioEnv(
        agent=build_shipwright_agent(deps),
        deps=deps,
        jira=jira,
        github=github,
        web=web,
        store=store,
        workspace=workspace,
        settings=settings,
        key=key,
        context=ShipwrightContext(user_id=user_id),
    )
