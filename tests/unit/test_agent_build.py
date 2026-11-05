"""Agent assembly, graph factory and langgraph.json (offline: fake chat model, fakes, no network).

`test_real_agent_review_gate_and_policy_denial` drives the REAL `create_deep_agent` graph with a
scripted fake chat model through public APIs only (ainvoke / Command(resume=...) / aget_state).
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends import LocalShellBackend
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

import shipwright.agent as agent_mod
import shipwright.graph as graph_mod
from shipwright.agent import MEMORY_SOURCES, ORCHESTRATOR_TOOL_NAMES, Dependencies, build_agent
from shipwright.cli import review_gate_resume
from shipwright.context import ShipwrightContext, ShipwrightState
from shipwright.integrations import github as gh
from shipwright.integrations import jira as jr
from shipwright.knowledge.search import KnowledgeSearcher
from shipwright.middleware.audit import MemoryAuditSink
from shipwright.permissions import PERMISSIONS
from shipwright.policies import POLICIES_STORE_KEY, seed_policies
from shipwright.tools import HITL_INTERRUPTS
from shipwright.workspace import BASE_SHA_FILE, REPO_DIR, Workspace, clean_shell_env
from tests.fakes import (
    FakeGitHubGateway,
    FakeJiraGateway,
    FakeSandbox,
    FakeVectorStore,
    settings_stub,
)

ROOT = Path(__file__).resolve().parents[2]

RAW_MCP_TOOL_NAMES = {
    gh.TOOL_CREATE_BRANCH,
    gh.TOOL_PUSH_FILES,
    gh.TOOL_DELETE_FILE,
    gh.TOOL_CREATE_PULL_REQUEST,
    gh.TOOL_LIST_BRANCHES,
    gh.TOOL_LIST_PULL_REQUESTS,
    gh.TOOL_UPDATE_PULL_REQUEST,
    jr.TOOL_GET_ISSUE,
    jr.TOOL_ADD_COMMENT,
    jr.TOOL_GET_TRANSITIONS,
    jr.TOOL_TRANSITION_ISSUE,
    jr.TOOL_CREATE_ISSUE,
}


class ScriptedChatModel(GenericFakeChatModel):
    """GenericFakeChatModel that accepts `bind_tools` (returns itself)."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:  # type: ignore[override]
        return self


def _tool_stub(name: str) -> StructuredTool:
    return StructuredTool.from_function(func=lambda: "x", name=name, description=f"stub {name}")


@pytest.fixture(autouse=True)
def _offline_models(monkeypatch):
    # Subagent model strings are resolved at build time (no network, but a key is required).
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-tests")
    monkeypatch.setattr(
        "langchain.agents.middleware.model_fallback.init_chat_model",
        lambda name: GenericFakeChatModel(messages=iter([])),
    )


@pytest.fixture
def spy(monkeypatch):
    """Record the kwargs given to create_deep_agent, then call the real one."""
    calls: list[dict[str, Any]] = []
    real = agent_mod.create_deep_agent

    def recorder(**kwargs: Any):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(agent_mod, "create_deep_agent", recorder)
    return calls


def _deps(jira: FakeJiraGateway | None = None) -> Dependencies:
    return Dependencies(
        jira=jira or FakeJiraGateway(),
        github=FakeGitHubGateway(),
        searcher=KnowledgeSearcher(FakeVectorStore()),
        web_tool=_tool_stub("web_search"),
        audit_sink=MemoryAuditSink(),
    )


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
async def local_ws(tmp_path) -> Workspace:
    """Real git clone through LocalShellBackend (virtual mode, like provider `local`)."""
    origin = tmp_path / "origin"
    (origin / "app").mkdir(parents=True)
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "core.autocrlf", "false")
    (origin / "app" / "a.py").write_text("print('a')\n", newline="\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "init")
    root = tmp_path / "ws"
    root.mkdir()
    backend = LocalShellBackend(root_dir=root, virtual_mode=True, env=clean_shell_env(root))
    ws = Workspace(
        backend,
        "acme/taskboard-api",
        shell_repo_dir=str(root / "workspace" / "repo"),
        posix_shell=os.name != "nt",
        mkdir_parents=False,
        clone_url=origin.as_uri(),
    )
    await ws.ensure_cloned()  # also uploads the real skills under /skills/
    return ws


def _fake_sandbox_ws() -> Workspace:
    """A Workspace over FakeSandbox that looks already cloned (ensure_cloned only uploads)."""
    sandbox = FakeSandbox({f"{REPO_DIR}/.git/HEAD": b"ref: main", BASE_SHA_FILE: b"b" * 40})
    return Workspace(sandbox, "acme/taskboard-api")


def _tool_names(tools: Any) -> list[str]:
    return [t.name for t in tools]


# ------------------------------------------------------------------ build_agent


async def test_build_agent_compiles_with_fakes(spy, local_ws):
    settings = settings_stub(shipwright_sandbox_provider="local")
    checkpointer, store = InMemorySaver(), InMemoryStore()
    graph = build_agent(settings, _deps(), local_ws, checkpointer=checkpointer, store=store)

    assert graph is not None and len(spy) == 1
    kw = spy[0]
    assert kw["name"] == "shipwright"
    assert kw["model"] == settings.model_strong
    assert kw["skills"] == ["/skills/"]
    assert kw["memory"] == ["/memories/preferences.md", "/policies/POLICIES.md"] == MEMORY_SOURCES
    assert kw["permissions"] == PERMISSIONS
    assert kw["interrupt_on"] == HITL_INTERRUPTS
    assert kw["state_schema"] is ShipwrightState
    assert kw["context_schema"] is ShipwrightContext
    assert kw["checkpointer"] is checkpointer and kw["store"] is store
    assert (
        _tool_names(kw["tools"])
        == list(ORCHESTRATOR_TOOL_NAMES)
        == [
            "read_jira_ticket",
            "update_jira_ticket",
            "create_jira_subtasks",
            "show_workspace_diff",
            "request_human_review",
            "publish_pull_request",
        ]
    )
    names = [s["name"] for s in kw["subagents"]]
    assert set(names) >= {"researcher", "code_analyst", "developer", "reviewer"}
    # prompt formatted: Jira link recipe, limits and both paths, no leftover placeholder
    prompt = kw["system_prompt"]
    assert f"{settings.jira_url}/browse/" in prompt
    assert f"at most **{settings.max_review_cycles}**" in prompt
    assert local_ws.shell_repo_dir in prompt and REPO_DIR in prompt
    assert "{" not in prompt and "}" not in prompt
    # the developer gets the shell path of THIS workspace
    dev = next(s for s in kw["subagents"] if s["name"] == "developer")
    assert local_ws.shell_repo_dir in dev["system_prompt"]


async def test_orchestrator_has_write_todos_and_locked_general_purpose(spy):
    build_agent(settings_stub(), _deps(), _fake_sandbox_ws())
    kw = spy[0]
    assert any(type(m).__name__ == "TodoListMiddleware" for m in kw["middleware"])
    # The auto-added general-purpose subagent would inherit every orchestrator tool (incl.
    # publish_pull_request): Shipwright overrides it with a tool-less read-only spec.
    gp = next(s for s in kw["subagents"] if s["name"] == "general-purpose")
    assert gp["tools"] == []
    assert any(type(m).__name__ == "ExcludeToolsMiddleware" for m in gp["middleware"])


async def test_real_agent_review_gate_and_policy_denial(local_ws):
    """Carry-over #1: real create_deep_agent + scripted model, public API only."""
    settings = settings_stub(shipwright_sandbox_provider="local")
    store = InMemoryStore()
    await seed_policies(store, settings)
    policies_before = store.get(("org", settings.org_id), POLICIES_STORE_KEY).value["content"]
    repo = Path(local_ws.shell_repo_dir)
    (repo / "app" / "a.py").write_text("print('fixed')\n", newline="\n")

    review_args = {
        "ticket_key": "SHIP-1",
        "summary": "Fix a",
        "test_report": "3 passed",
        "review_report": "Verdict: pass",
    }
    model = ScriptedChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"file_path": "/policies/POLICIES.md", "content": "hacked"},
                            "id": "w1",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "request_human_review", "args": review_args, "id": "r1"}],
                ),
                AIMessage(content="Approved; done."),
            ]
        )
    )
    graph = build_agent(
        settings, _deps(), local_ws, checkpointer=InMemorySaver(), store=store, model=model
    )
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    context = ShipwrightContext(user_id="alice")

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "Process SHIP-1"}]}, config, context=context
    )

    # (b) the policies write was denied and the store is unchanged
    denied = next(
        m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "w1"
    )
    assert "denied" in str(denied.content).lower()
    item = store.get(("org", settings.org_id), POLICIES_STORE_KEY)
    assert item.value["content"] == policies_before and "hacked" not in item.value["content"]

    # (a) the review gate paused with the exact diff; approving (echoing diff_hash) lands in state
    state = await graph.aget_state(config)
    (pending,) = state.interrupts
    payload = pending.value
    assert payload["kind"] == "review_gate" and "print('fixed')" in payload["diff"]
    assert not state.values.get("approved_diff_hash")

    result = await graph.ainvoke(
        Command(resume=review_gate_resume(payload, "approve")), config, context=context
    )
    state = await graph.aget_state(config)
    assert state.values["approved_diff_hash"] == payload["diff_hash"]
    assert state.values["approved_ticket_key"] == "SHIP-1"
    assert not state.interrupts
    assert result["messages"][-1].content == "Approved; done."


# ------------------------------------------------------------------ make_graph


def _raise(*args: Any, **kwargs: Any):
    raise AssertionError("schema-only graph must not touch sandboxes, MCP or Pinecone")


@pytest.fixture
def no_live(monkeypatch):
    monkeypatch.setattr(graph_mod, "get_settings", lambda: settings_stub())
    monkeypatch.setattr(graph_mod, "open_workspace", _raise)
    monkeypatch.setattr(graph_mod, "load_mcp_tools", _raise)
    monkeypatch.setattr(graph_mod, "build_vector_store", _raise)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"configurable": {}},
        {"configurable": {"thread_id": "t1", "__is_for_execution__": False}},
    ],
)
async def test_make_graph_without_thread_id_creates_no_sandbox(no_live, spy, config):
    graph = await graph_mod.make_graph(config)
    assert graph is not None
    kw = spy[0]
    assert kw["checkpointer"] is None and kw["store"] is None  # provided by the platform
    assert kw["state_schema"] is ShipwrightState and kw["skills"] == ["/skills/"]


async def test_make_graph_with_thread_builds_live_graph(monkeypatch, spy):
    settings = settings_stub()
    opened: list[dict[str, Any]] = []
    ws = _fake_sandbox_ws()

    async def fake_open(s, *, thread_id, repo, **kw):
        opened.append({"thread_id": thread_id, "repo": repo})
        return ws

    async def fake_mcp(s):
        return {
            "github": {n: _tool_stub(n) for n in RAW_MCP_TOOL_NAMES if not n.startswith("jira")},
            "atlassian": {n: _tool_stub(n) for n in RAW_MCP_TOOL_NAMES if n.startswith("jira")},
        }

    monkeypatch.setattr(graph_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(graph_mod, "open_workspace", fake_open)
    monkeypatch.setattr(graph_mod, "load_mcp_tools", fake_mcp)
    monkeypatch.setattr(graph_mod, "build_vector_store", lambda s: FakeVectorStore())

    await graph_mod.make_graph({"configurable": {"thread_id": "t-42", "repo": "acme/other"}})
    assert opened == [{"thread_id": "t-42", "repo": "acme/other"}]
    assert ws.backend.count("git clone") == 0  # already cloned; skills were (re)uploaded
    assert any(p.startswith("/skills/") for p in ws.backend.files)
    kw = spy[0]
    assert kw["checkpointer"] is None and kw["store"] is None
    audit = next(m for m in kw["middleware"] if type(m).__name__ == "AuditMiddleware")
    assert type(audit.sink).__name__ == "MongoAuditSink"
    assert audit.sink._db == settings.mongodb_db


async def test_make_graph_default_repo_from_settings(monkeypatch, spy):
    settings = settings_stub()
    opened: list[str] = []

    async def fake_open(s, *, thread_id, repo, **kw):
        opened.append(repo)
        return _fake_sandbox_ws()

    async def fake_mcp(s):
        return {"github": {}, "atlassian": {}}

    monkeypatch.setattr(graph_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(graph_mod, "open_workspace", fake_open)
    monkeypatch.setattr(graph_mod, "load_mcp_tools", fake_mcp)
    monkeypatch.setattr(graph_mod, "build_vector_store", lambda s: FakeVectorStore())
    await graph_mod.make_graph({"configurable": {"thread_id": "t-1"}})
    assert opened == [settings.shipwright_repo]


async def test_orchestrator_tools_exclude_raw_mcp_writes(monkeypatch, spy):
    """Carry-over #3: no raw MCP tool reaches the orchestrator, a subagent or a ToolNode."""
    settings = settings_stub()

    async def fake_open(s, *, thread_id, repo, **kw):
        return _fake_sandbox_ws()

    async def fake_mcp(s):
        return {
            "github": {n: _tool_stub(n) for n in RAW_MCP_TOOL_NAMES if not n.startswith("jira")},
            "atlassian": {n: _tool_stub(n) for n in RAW_MCP_TOOL_NAMES if n.startswith("jira")},
        }

    monkeypatch.setattr(graph_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(graph_mod, "open_workspace", fake_open)
    monkeypatch.setattr(graph_mod, "load_mcp_tools", fake_mcp)
    monkeypatch.setattr(graph_mod, "build_vector_store", lambda s: FakeVectorStore())

    graph = await graph_mod.make_graph({"configurable": {"thread_id": "t-3"}})
    kw = spy[0]
    exposed = set(_tool_names(kw["tools"]))
    for sub in kw["subagents"]:
        exposed |= set(_tool_names(sub.get("tools") or []))
    assert not exposed & RAW_MCP_TOOL_NAMES
    assert "jira_add_comment" not in exposed and "push_files" not in exposed
    # the compiled ToolNode of the orchestrator
    tool_node = graph.nodes["tools"].bound
    node_tools = set(tool_node.tools_by_name)
    assert set(ORCHESTRATOR_TOOL_NAMES) <= node_tools
    assert "write_todos" in node_tools and "task" in node_tools
    assert not node_tools & RAW_MCP_TOOL_NAMES


# ------------------------------------------------------------------ langgraph.json


def test_langgraph_json_points_to_make_graph():
    data = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    assert data["graphs"] == {"shipwright": "./src/shipwright/graph.py:make_graph"}
    assert data["dependencies"] == ["."]
    assert data["env"] == ".env"
    assert data["checkpointer"] == {"backend": "mongo"}
    assert data["python_version"] == "3.12"
    path, _, attr = data["graphs"]["shipwright"].partition(":")
    assert (ROOT / path).is_file() and attr == "make_graph"
    assert inspect.iscoroutinefunction(graph_mod.make_graph)
    # langgraph-api classifies a 1-param factory as "config" (not ServerRuntime)
    from langgraph_api._factory_utils import _classify_factory

    hook = _classify_factory(graph_mod.make_graph)
    assert hook({"configurable": {"thread_id": "x"}}, object()) == {
        "config": {"configurable": {"thread_id": "x"}}
    }


def test_pr_template_verdict_wording():
    text = (ROOT / "src/shipwright/skills/pr-template/SKILL.md").read_text(encoding="utf-8")
    assert "pass | changes_requested" in text
    assert "APPROVE | CHANGES REQUESTED" not in text
