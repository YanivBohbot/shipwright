"""Scenario 1 (bugfix) end to end with a REAL OpenAI model, fake Jira/GitHub, local provider.

Run: uv run pytest -m llm tests/integration/test_scenario_bugfix.py -v
Needs a `.env` with OPENAI_API_KEY / MODEL_* (other services are faked; no MCP, no Pinecone).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.llm

ROOT = Path(__file__).resolve().parents[2]
DEMO_REPO = ROOT / "demo" / "taskboard-api"
IGNORE = shutil.ignore_patterns(".git", ".venv", ".pytest_cache", "__pycache__")
MAX_PAUSES = 10
_PASSED_RE = re.compile(r"\b([1-9]\d*) passed\b")
_FAILED_RE = re.compile(r"\b([1-9]\d*) (failed|errors?)\b")


def assert_green(test_report: str) -> None:
    """pytest counts: at least one passed, no non-zero failed/error count, no failing status."""
    assert _PASSED_RE.search(test_report), test_report
    assert not _FAILED_RE.search(test_report), test_report
    assert "TESTS_FAILING" not in test_report, test_report


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _scenario_1_ticket():
    from shipwright.demo_tickets import load_demo_tickets
    from shipwright.tickets import Ticket, extract_acceptance_criteria, extract_spec_refs

    demo = next(t for t in load_demo_tickets() if t.scenario == 1)
    return Ticket(
        key="SHIP-1",
        summary=demo.summary,
        description=demo.description,
        status="To Do",
        acceptance_criteria=extract_acceptance_criteria(demo.description),
        spec_refs=extract_spec_refs(demo.description),
        issue_type=demo.issue_type,
    )


async def test_scenario_1_bugfix_reaches_review_gate(tmp_path):
    from deepagents.backends import LocalShellBackend
    from dotenv import load_dotenv
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore
    from langgraph.types import Command

    from shipwright.agent import Dependencies, build_agent
    from shipwright.cli import hitl_resume, review_gate_resume
    from shipwright.config import get_settings
    from shipwright.context import ShipwrightContext
    from shipwright.knowledge.search import KnowledgeSearcher
    from shipwright.middleware.audit import MemoryAuditSink
    from shipwright.policies import seed_policies
    from shipwright.workspace import CLONE_TIMEOUT, Workspace, clean_shell_env
    from tests.fakes import FakeGitHubGateway, FakeJiraGateway, FakeVectorStore

    load_dotenv()
    settings = get_settings().model_copy(update={"shipwright_sandbox_provider": "local"})

    # a git copy of the demo repo, cloned by the workspace like the real public repo
    origin = tmp_path / "origin"
    shutil.copytree(DEMO_REPO, origin, ignore=IGNORE)
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
        settings.shipwright_repo,
        shell_repo_dir=str(root / "workspace" / "repo"),
        posix_shell=os.name != "nt",
        mkdir_parents=False,
        clone_url=origin.as_uri(),
    )
    await workspace.ensure_cloned()

    jira = FakeJiraGateway({"SHIP-1": _scenario_1_ticket()})
    github = FakeGitHubGateway(branch_shas={"main": await workspace.base_sha()})
    deps = Dependencies(
        jira=jira,
        github=github,
        searcher=KnowledgeSearcher(FakeVectorStore()),
        web_tool=StructuredTool.from_function(
            func=lambda query: "No web results.", name="web_search", description="Web search."
        ),
        audit_sink=MemoryAuditSink(),
    )
    store = InMemoryStore()
    await seed_policies(store, settings)
    graph = build_agent(settings, deps, workspace, checkpointer=InMemorySaver(), store=store)
    config = {"configurable": {"thread_id": f"it-{uuid.uuid4().hex[:8]}"}}
    context = ShipwrightContext(user_id="alice")

    run_input: Any = {"messages": [{"role": "user", "content": "Process Jira ticket SHIP-1."}]}
    seen: list[str] = []
    for _ in range(MAX_PAUSES):
        await graph.ainvoke(run_input, config, context=context)
        state = await graph.aget_state(config)
        if not state.interrupts:
            break
        (pending,) = state.interrupts
        value = pending.value
        if value.get("kind") == "review_gate":
            if not seen:  # first pause: the review gate on a green, relevant diff
                assert_green(value["test_report"])
                files = [
                    line.split(" b/", 1)[-1]
                    for line in value["diff"].splitlines()
                    if line.startswith("diff --git ")
                ]
                assert any(f.startswith("app/") for f in files), files
                assert any(f.startswith("tests/") for f in files), files
            seen.append("review_gate")
            run_input = Command(resume=review_gate_resume(value, "approve"))
        else:
            names = [a["name"] for a in value["action_requests"]]
            seen.extend(names)
            run_input = Command(resume=hitl_resume(value, ["approve"] * len(names)))

    assert seen[0] == "review_gate"
    assert "publish_pull_request" in seen and "update_jira_ticket" in seen
    assert seen.index("publish_pull_request") < seen.index("update_jira_ticket")
    assert len(github.prs) == 1
    pr_url = next(iter(github.open_prs.values()))
    assert any(key == "SHIP-1" and pr_url in body for key, body in jira.comments)
    assert ("SHIP-1", "In Review") in jira.transitions
