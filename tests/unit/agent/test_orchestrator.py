"""The deep agent end to end with a scripted model and fake MCP tools (no network).

Checks the wiring that matters: the MCP tools reach the orchestrator, every write pauses for a
human, an approved write runs once, a rejected write never runs and its reason reaches the agent.
"""

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from shipwright.agent.orchestrator import build_shipwright_agent
from shipwright.agent.state import ShipwrightContext
from shipwright.integrations.mcp import ALLOWED_TOOLS, WRITE_TOOLS
from tests.fakes import (
    FakeSandbox,
    MemoryAuditSink,
    ScriptedChatModel,
    fake_mcp_tool,
    make_settings,
)

CONFIG = {"configurable": {"thread_id": "t1"}}
CONTEXT = ShipwrightContext(user_id="alice")
COMMENT = {"issue_key": "SHIP-4", "body": "1. Which format?"}


def call(name, args, i):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}])


def build(script, calls, audit=None):
    tools = [fake_mcp_tool(n, answer=f"{n} done", calls=calls) for n in ALLOWED_TOOLS]
    agent = build_shipwright_agent(
        make_settings(),
        mcp_tools=tools,
        sandbox=FakeSandbox(),
        model=ScriptedChatModel(messages=iter(script)),
        audit_sink=audit or MemoryAuditSink(),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
    )
    return agent


async def run(agent, payload):
    await agent.ainvoke(payload, CONFIG, context=CONTEXT)
    return await agent.aget_state(CONFIG)


START = {"messages": [{"role": "user", "content": "Process Jira ticket SHIP-4."}]}


async def test_read_runs_and_write_pauses_for_the_human():
    calls = []
    script = [
        call("jira_get_issue", {"issue_key": "SHIP-4"}, 1),
        call("jira_add_comment", COMMENT, 2),
    ]
    agent = build(script, calls)
    state = await run(agent, START)
    assert calls == [("jira_get_issue", {"issue_key": "SHIP-4"})]  # the write did not run
    (pending,) = state.interrupts
    (action,) = pending.value["action_requests"]
    assert (action["name"], action["args"]) == ("jira_add_comment", COMMENT)


async def test_approved_write_runs_once_and_is_audited():
    calls, audit = [], MemoryAuditSink()
    script = [call("jira_add_comment", COMMENT, 1), AIMessage(content="Questions posted.")]
    agent = build(script, calls, audit)
    await run(agent, START)
    state = await run(agent, Command(resume={"decisions": [{"type": "approve"}]}))
    assert not state.interrupts
    assert calls == [("jira_add_comment", COMMENT)]
    assert [(e.tool, e.outcome, e.user_id) for e in audit.events] == [
        ("jira_add_comment", "ok", "alice")
    ]


async def test_rejected_write_never_runs_and_the_reason_reaches_the_agent():
    calls = []
    script = [call("push_files", {"branch": "shipwright/ship-4"}, 1), AIMessage(content="ok")]
    agent = build(script, calls)
    await run(agent, START)
    reject = {"decisions": [{"type": "reject", "message": "Add a regression test first."}]}
    state = await run(agent, Command(resume=reject))
    assert calls == []
    tool_messages = [m for m in state.values["messages"] if m.type == "tool"]
    assert "Add a regression test first." in tool_messages[-1].content


def test_wiring_of_create_deep_agent(monkeypatch):
    """Only the orchestrator holds the MCP tools; every subagent lists its own tools."""
    import shipwright.agent.orchestrator as orchestrator

    captured = {}
    monkeypatch.setattr(orchestrator, "create_deep_agent", lambda **kw: captured.update(kw))
    build([], [])
    names = [getattr(t, "name", None) for t in captured["tools"]]
    assert names == [*ALLOWED_TOOLS, "show_workspace_diff", "get_changed_files"]
    subagents = {s["name"]: s for s in captured["subagents"]}
    expected = {"general-purpose", "researcher", "code_analyst", "developer", "reviewer"}
    assert set(subagents) == expected
    for spec in subagents.values():
        assert "tools" in spec, f"{spec['name']} would inherit every orchestrator tool"
        assert not {t.name for t in spec["tools"]} & set(ALLOWED_TOOLS)
    assert [t.name for t in subagents["researcher"]["tools"]] == ["knowledge_search", "web_search"]
    assert [t.name for t in subagents["reviewer"]["tools"]] == ["show_workspace_diff"]
    assert set(captured["interrupt_on"]) == set(WRITE_TOOLS)
