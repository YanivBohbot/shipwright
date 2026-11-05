"""The `create_deep_agent` call: arguments, and the REAL graph driven by a scripted model.

The real-graph tests use public APIs only (ainvoke / Command(resume=...) / aget_state).
"""

import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command

import shipwright.agent.orchestrator as orch
from shipwright.agent.backend import PERMISSIONS, POLICIES_STORE_KEY
from shipwright.agent.hitl import INTERRUPT_ON, review_gate_resume
from shipwright.agent.orchestrator import MEMORY, build_shipwright_agent
from shipwright.agent.state import ShipwrightContext, ShipwrightState
from shipwright.integrations.github import mcp_github as gh
from shipwright.integrations.github.mcp_github import McpGitHub
from shipwright.integrations.jira import mcp_jira as jr
from shipwright.integrations.jira.mcp_jira import McpJira
from tests.fakes import RecordingChatModel, ScriptedChatModel
from tests.unit.agent.conftest import fake_deps

ROOT = Path(__file__).resolve().parents[3]
ORCHESTRATOR_TOOLS = [
    "read_jira_ticket",
    "update_jira_ticket",
    "create_jira_subtasks",
    "show_workspace_diff",
    "request_human_review",
    "publish_pull_request",
]
RAW_MCP_TOOL_NAMES = {
    gh.TOOL_CREATE_BRANCH,
    gh.TOOL_PUSH_FILES,
    gh.TOOL_DELETE_FILE,
    gh.TOOL_CREATE_PULL_REQUEST,
    gh.TOOL_LIST_BRANCHES,
    gh.TOOL_LIST_PULL_REQUESTS,
    "update_pull_request",
    jr.TOOL_GET_ISSUE,
    jr.TOOL_ADD_COMMENT,
    jr.TOOL_GET_TRANSITIONS,
    jr.TOOL_TRANSITION_ISSUE,
    jr.TOOL_CREATE_ISSUE,
}
REVIEW_ARGS = {
    "ticket_key": "SHIP-1",
    "summary": "Fix a",
    "test_report": "3 passed",
    "review_report": "Verdict: pass",
}
PUBLISH_ARGS = {
    "ticket_key": "SHIP-1",
    "title": "Fix a",
    "body": "Body",
    "changelog_entry": "Fix a (SHIP-1)",
}


@pytest.fixture
def spy(monkeypatch):
    """Record the kwargs given to create_deep_agent, then call the real one."""
    calls: list[dict[str, Any]] = []
    real = orch.create_deep_agent

    def recorder(**kwargs: Any):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(orch, "create_deep_agent", recorder)
    return calls


def _call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _names(tools: Any) -> list[str]:
    return [t.name for t in tools]


def _config() -> dict[str, Any]:
    return {"configurable": {"thread_id": uuid.uuid4().hex}}


async def test_create_deep_agent_arguments(spy, local_settings, local_ws):
    deps = fake_deps(local_settings, local_ws)
    assert build_shipwright_agent(deps) is not None and len(spy) == 1
    kw = spy[0]
    assert kw["name"] == "shipwright" and kw["model"] == local_settings.model_strong
    assert kw["skills"] == ["/skills/"]
    assert kw["memory"] == ["/memories/preferences.md", "/policies/POLICIES.md"] == MEMORY
    assert kw["permissions"] == PERMISSIONS and kw["interrupt_on"] == INTERRUPT_ON
    assert kw["state_schema"] is ShipwrightState and kw["context_schema"] is ShipwrightContext
    assert kw["checkpointer"] is deps.checkpointer and kw["store"] is deps.store
    assert _names(kw["tools"]) == ORCHESTRATOR_TOOLS
    names = [s["name"] for s in kw["subagents"]]
    assert names == ["general-purpose", "researcher", "code_analyst", "developer", "reviewer"]
    prompt = kw["system_prompt"]
    assert "https://jira.local.invalid/browse/" in prompt  # local-mode link recipe
    assert f"at most **{local_settings.max_review_cycles}**" in prompt
    assert local_ws.shell_repo_dir in prompt and "/workspace/repo" in prompt
    assert "{" not in prompt and "}" not in prompt
    dev = next(s for s in kw["subagents"] if s["name"] == "developer")
    assert local_ws.shell_repo_dir in dev["system_prompt"]
    assert any(type(m).__name__ == "TodoListMiddleware" for m in kw["middleware"])
    gp = kw["subagents"][0]
    assert gp["tools"] == [] and type(gp["middleware"][0]).__name__ == "ReadOnlyToolsMiddleware"


async def test_no_raw_mcp_tool_reaches_the_model(spy, local_settings, local_ws):
    """Live gateways built from raw MCP tools: only the wrappers are exposed, anywhere."""

    def stub(name: str) -> StructuredTool:
        return StructuredTool.from_function(func=lambda: "x", name=name, description=name)

    raw = {n: stub(n) for n in RAW_MCP_TOOL_NAMES}
    deps = fake_deps(local_settings, local_ws, jira=McpJira(raw), github=McpGitHub(raw))
    graph = build_shipwright_agent(deps)
    kw = spy[0]
    exposed = set(_names(kw["tools"]))
    for sub in kw["subagents"]:
        exposed |= set(_names(sub.get("tools") or []))
    assert not exposed & RAW_MCP_TOOL_NAMES
    node_tools = set(graph.nodes["tools"].bound.tools_by_name)
    assert set(ORCHESTRATOR_TOOLS) <= node_tools and {"write_todos", "task"} <= node_tools
    assert not node_tools & RAW_MCP_TOOL_NAMES


async def test_real_agent_gate_approval_policies_and_skills(local_settings, local_ws):
    """Carry-over #1: the gate's Command lands in state, /policies/ is read-only, skills and
    policies are really loaded into the system prompt."""
    (Path(local_ws.shell_repo_dir) / "app" / "a.py").write_text("print('fixed')\n", newline="\n")
    write_policy = {"file_path": "/policies/POLICIES.md", "content": "hacked"}
    write_skill = {"file_path": "/skills/pr-template/SKILL.md", "content": "hacked"}
    model = RecordingChatModel(
        messages=iter(
            [
                _call("write_file", write_policy, "w1"),
                _call("write_file", write_skill, "w2"),
                _call("request_human_review", REVIEW_ARGS, "r1"),
                AIMessage(content="Approved; done."),
            ]
        )
    )
    model.seen.clear()
    deps = fake_deps(local_settings, local_ws, model=model)
    graph, config = build_shipwright_agent(deps), _config()
    context = ShipwrightContext(user_id="alice")
    skill_before = (ROOT / "src/shipwright/agent/skills/pr-template/SKILL.md").read_text("utf-8")

    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "Process SHIP-1"}]}, config, context=context
    )
    system = str(next(m for m in model.seen[0] if isinstance(m, SystemMessage)).content)
    for skill in ("pr-template", "jira-workflow", "coding-conventions", "testing-guidelines"):
        assert skill in system
    assert "400 changed diff lines" in system  # POLICIES.md seeded, then loaded as memory

    for call_id in ("w1", "w2"):
        denied = next(m for m in result["messages"] if getattr(m, "tool_call_id", "") == call_id)
        assert "denied" in str(denied.content).lower()
    item = await deps.store.aget(("org", local_settings.org_id), POLICIES_STORE_KEY)
    assert "hacked" not in item.value["content"]
    assert (ROOT / "src/shipwright/agent/skills/pr-template/SKILL.md").read_text(
        "utf-8"
    ) == skill_before

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
    assert not state.interrupts and result["messages"][-1].content == "Approved; done."


async def test_publish_pauses_on_interrupt_on_before_any_gateway_call(local_settings, local_ws):
    model = ScriptedChatModel(
        messages=iter([_call("publish_pull_request", PUBLISH_ARGS, "p1"), AIMessage("unused")])
    )
    deps = fake_deps(local_settings, local_ws, model=model)
    graph, config = build_shipwright_agent(deps), _config()
    await graph.ainvoke(
        {"messages": [{"role": "user", "content": "Publish"}]},
        config,
        context=ShipwrightContext(user_id="alice"),
    )
    (pending,) = (await graph.aget_state(config)).interrupts
    assert [r["name"] for r in pending.value["action_requests"]] == ["publish_pull_request"]
    github = deps.github
    assert github.list_calls == 0 and github.branches == github.pushes == github.prs == []


async def test_run_input_cannot_seed_the_approval(local_settings, local_ws):
    """approved_* are OmitFromInput: even the REAL hash in the run input gains nothing."""
    (Path(local_ws.shell_repo_dir) / "app" / "a.py").write_text("print('x')\n", newline="\n")
    _, real_hash = await local_ws.diff_and_hash()
    model = ScriptedChatModel(
        messages=iter([_call("publish_pull_request", PUBLISH_ARGS, "p1"), AIMessage("done")])
    )
    deps = fake_deps(local_settings, local_ws, model=model)
    graph, config = build_shipwright_agent(deps), _config()
    context = ShipwrightContext(user_id="alice")
    assert "approved_diff_hash" not in graph.get_input_jsonschema().get("properties", {})
    await graph.ainvoke(
        {
            "messages": [{"role": "user", "content": "Publish SHIP-1"}],
            "approved_diff_hash": real_hash,
            "approved_ticket_key": "SHIP-1",
        },
        config,
        context=context,
    )
    assert (await graph.aget_state(config)).values.get("approved_diff_hash") is None
    result = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}), config, context=context
    )
    refused = next(
        m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "p1"
    )
    assert "not approved" in str(refused.content).lower()
    assert deps.github.pushes == [] and deps.github.prs == []
