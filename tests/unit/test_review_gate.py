"""Review gate: interrupt() inside a one-node StateGraph(ShipwrightState) with InMemorySaver."""

import hashlib
import uuid

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from shipwright.context import ShipwrightState
from shipwright.tools.review_gate import make_review_gate_tool, parse_review_decision
from shipwright.workspace import WorkspaceError
from tests.fakes import FakeWorkspace

DIFF = "diff --git a/app/a.py b/app/a.py\n+fixed\n"
H = hashlib.sha256(DIFF.encode()).hexdigest()
ARGS = {"summary": "Fix 500 on empty title", "test_report": "12 passed", "review_report": "pass"}


def _graph(tools: list):
    builder = StateGraph(ShipwrightState)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile(checkpointer=InMemorySaver())


def _call(name: str, args: dict, call_id: str = "c1") -> dict:
    msg = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])
    return {"messages": [msg]}


def _cfg() -> dict:
    return {"configurable": {"thread_id": uuid.uuid4().hex}}


async def _start(ws: FakeWorkspace, seed: dict | None = None):
    graph, cfg = _graph([make_review_gate_tool(ws)]), _cfg()
    result = await graph.ainvoke({**_call("request_human_review", ARGS), **(seed or {})}, cfg)
    return graph, cfg, result


def _last_tool_message(result: dict) -> ToolMessage:
    msg = result["messages"][-1]
    assert isinstance(msg, ToolMessage) and msg.tool_call_id == "c1"
    return msg


async def test_review_gate_refuses_empty_diff():
    graph, cfg, result = await _start(FakeWorkspace(diff="   \n"))
    assert "__interrupt__" not in result
    assert not (await graph.aget_state(cfg)).interrupts
    assert "Nothing to review" in _last_tool_message(result).content


async def test_review_gate_interrupt_payload():
    _, _, result = await _start(FakeWorkspace(diff=DIFF))
    (intr,) = result["__interrupt__"]
    assert intr.value == {
        "kind": "review_gate",
        "summary": ARGS["summary"],
        "diff": DIFF,
        "diff_hash": H,
        "test_report": "12 passed",
        "review_report": "pass",
        "allowed": ["approve", "feedback", "reject"],
    }


async def test_review_gate_approve_sets_hash():
    graph, cfg, _ = await _start(FakeWorkspace(diff=DIFF))
    result = await graph.ainvoke(Command(resume={"type": "approve"}), cfg)
    assert result["approved_diff_hash"] == H
    assert (await graph.aget_state(cfg)).values["approved_diff_hash"] == H
    assert "APPROVED" in _last_tool_message(result).content


async def test_review_gate_feedback_clears_hash_and_relays_message():
    graph, cfg, _ = await _start(FakeWorkspace(diff=DIFF), seed={"approved_diff_hash": "old"})
    result = await graph.ainvoke(
        Command(resume={"type": "feedback", "message": "Use dataclasses, not dicts"}), cfg
    )
    assert result["approved_diff_hash"] is None
    content = _last_tool_message(result).content
    assert content.startswith("CHANGES REQUESTED by human: Use dataclasses, not dicts.")
    assert "/memories/preferences.md" in content


async def test_review_gate_reject_clears_hash():
    graph, cfg, _ = await _start(FakeWorkspace(diff=DIFF), seed={"approved_diff_hash": "old"})
    result = await graph.ainvoke(Command(resume={"type": "reject", "message": "Wrong fix"}), cfg)
    assert result["approved_diff_hash"] is None
    assert _last_tool_message(result).content == (
        "REJECTED by human: Wrong fix. Stop working on this ticket and report."
    )


async def test_review_gate_accepts_hitl_decisions_wrapper():
    graph, cfg, _ = await _start(FakeWorkspace(diff=DIFF))
    result = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)
    assert result["approved_diff_hash"] == H


async def test_review_gate_invalid_resume_payload_raises():
    graph, cfg, _ = await _start(FakeWorkspace(diff=DIFF))
    with pytest.raises(ValueError):
        await graph.ainvoke(Command(resume={"type": "merge"}), cfg)


async def test_review_gate_approve_refused_when_echoed_hash_differs():
    graph, cfg, _ = await _start(FakeWorkspace(diff=DIFF))
    result = await graph.ainvoke(Command(resume={"type": "approve", "diff_hash": "stale"}), cfg)
    assert result.get("approved_diff_hash") is None
    assert "changed" in _last_tool_message(result).content.lower()


async def test_review_gate_reports_workspace_error_as_text():
    class Broken(FakeWorkspace):
        async def diff_and_hash(self):
            raise WorkspaceError("No valid base sha recorded")

    _, _, result = await _start(Broken())
    assert "__interrupt__" not in result
    assert "No valid base sha recorded" in _last_tool_message(result).content


@pytest.mark.parametrize(
    "payload",
    [
        "approve",
        None,
        {"type": "merge"},
        {"type": "feedback"},
        {"type": "feedback", "message": "   "},
        {"type": "reject", "message": 3},
        {"decisions": []},
        {"decisions": [{"type": "approve"}, {"type": "approve"}]},
        [],
    ],
)
def test_parse_review_decision_rejects_invalid(payload):
    with pytest.raises(ValueError):
        parse_review_decision(payload)


def test_parse_review_decision_shapes():
    assert parse_review_decision({"type": "approve"}) == ("approve", "", None)
    assert parse_review_decision([{"type": "reject", "message": "no"}]) == ("reject", "no", None)
    assert parse_review_decision({"decisions": [{"type": "feedback", "message": " x "}]}) == (
        "feedback",
        "x",
        None,
    )
    assert parse_review_decision({"type": "approve", "diff_hash": "h"}) == ("approve", "", "h")
