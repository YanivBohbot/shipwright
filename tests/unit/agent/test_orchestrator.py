"""The deep agent end to end with a scripted model and fake MCP tools (no network).

One scripted model plays every agent, in call order: the orchestrator, then the subagent its
`task` call starts, then the orchestrator again. Checks: each subagent has exactly its own
tools; a write inside a subagent pauses the WHOLE run; an approved write runs once; a rejected
write never runs and its reason reaches the subagent.
"""

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from shipwright.agent.orchestrator import build_shipwright_agent
from shipwright.agent.state import ShipwrightContext
from tests.fakes import FakeSandbox, ScriptedChatModel, fake_mcp, make_settings

CONFIG = {"configurable": {"thread_id": "t1"}}
CONTEXT = ShipwrightContext(user_id="alice")
START = {"messages": [{"role": "user", "content": "Process Jira ticket SHIP-4."}]}
COMMENT = {"issue_key": "SHIP-4", "body": "1. Which format?"}


def call(name, args, i):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}])


def delegate(subagent, i):
    return call("task", {"subagent_type": subagent, "description": "do it"}, i)


SEEN: list[str] = []  # the text of every message list the model was called with


class Recording(ScriptedChatModel):
    def _generate(self, messages, *args, **kwargs):
        SEEN.append(" ".join(str(m.content) for m in messages))
        return super()._generate(messages, *args, **kwargs)


def build(script, calls):
    return build_shipwright_agent(
        make_settings(),
        mcp=fake_mcp(calls),
        sandbox=FakeSandbox(),
        model=Recording(messages=iter(script)),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
    )


async def run(agent, payload):
    await agent.ainvoke(payload, CONFIG, context=CONTEXT)
    return await agent.aget_state(CONFIG)


async def test_a_write_in_a_subagent_pauses_the_run_and_runs_once_approved():
    calls = []
    script = [
        call("jira_get_issue", {"issue_key": "SHIP-4"}, 1),  # orchestrator
        delegate("jira_agent", 2),
        call("jira_add_comment", COMMENT, 3),  # jira_agent
        AIMessage(content="Comment posted."),
        AIMessage(content="Questions asked; stopping."),  # orchestrator
    ]
    agent = build(script, calls)
    state = await run(agent, START)
    assert calls == [("jira_get_issue", {"issue_key": "SHIP-4"})]  # the write waits
    (pending,) = state.interrupts
    (action,) = pending.value["action_requests"]
    assert (action["name"], action["args"]) == ("jira_add_comment", COMMENT)

    state = await run(agent, Command(resume={"decisions": [{"type": "approve"}]}))
    assert not state.interrupts
    assert calls[1:] == [("jira_add_comment", COMMENT)]
    assert state.values["messages"][-1].content == "Questions asked; stopping."


async def test_a_rejected_write_never_runs_and_the_reason_reaches_the_subagent():
    calls = []
    SEEN.clear()
    push = {"owner": "acme", "repo": "taskboard-api", "branch": "shipwright/ship-4"}
    script = [
        delegate("github_publisher", 1),
        call("push_files", push, 2),  # github_publisher
        AIMessage(content="REJECTED: Add a regression test first."),
        AIMessage(content="Sending the feedback to the developer."),  # orchestrator
    ]
    agent = build(script, calls)
    await run(agent, START)
    reject = {"decisions": [{"type": "reject", "message": "Add a regression test first."}]}
    state = await run(agent, Command(resume=reject))
    assert calls == []
    assert any("Add a regression test first." in seen for seen in SEEN)
    task_result = [m for m in state.values["messages"] if m.type == "tool"][-1]
    assert task_result.content.startswith("REJECTED")


def test_each_subagent_has_its_own_tools(monkeypatch):
    import shipwright.agent.orchestrator as orchestrator

    captured = {}
    monkeypatch.setattr(orchestrator, "create_deep_agent", lambda **kw: captured.update(kw))
    build([], [])
    assert [t.name for t in captured["tools"]] == ["jira_get_issue"]
    tools = {s["name"]: [t.name for t in s["tools"]] for s in captured["subagents"]}
    assert tools == {
        "general-purpose": [],
        "researcher": ["knowledge_search", "web_search", "jira_get_issue", "jira_search"],
        "code_analyst": ["list_commits", "list_pull_requests", "search_code"],
        "developer": [],
        "reviewer": ["show_workspace_diff"],
        "github_publisher": [
            "get_changed_files",
            "list_branches",
            "create_branch",
            "push_files",
            "create_pull_request",
        ],
        "jira_agent": [
            "jira_get_issue",
            "jira_get_transitions",
            "jira_add_comment",
            "jira_transition_issue",
            "jira_create_issue",
        ],
    }
    # One approval rule for everyone: subagents do not override the inherited interrupt_on.
    assert all("interrupt_on" not in s for s in captured["subagents"])
    assert set(captured["interrupt_on"]) == {
        "create_branch",
        "push_files",
        "create_pull_request",
        "jira_add_comment",
        "jira_transition_issue",
        "jira_create_issue",
    }
