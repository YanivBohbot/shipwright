"""HITL resume values (pure builders) and the terminal prompts of `agent/hitl.py`."""

from types import SimpleNamespace

import pytest
from langgraph.types import Command

from shipwright.agent.hitl import INTERRUPT_ON, build_resume, hitl_resume, review_gate_resume
from shipwright.agent.tools.review_tools import parse_review_decision

GATE = {
    "kind": "review_gate",
    "ticket_key": "SHIP-1",
    "summary": "s",
    "diff": "diff --git a/x b/x\n",
    "diff_hash": "abc123",
    "test_report": "3 passed",
    "review_report": "pass",
    "allowed": ["approve", "feedback", "reject"],
}

HITL = {
    "action_requests": [
        {"name": "publish_pull_request", "args": {"ticket_key": "SHIP-1", "title": "t"}},
        {"name": "update_jira_ticket", "args": {"key": "SHIP-1", "comment": "c"}},
    ],
    "review_configs": [
        {"action_name": "publish_pull_request", "allowed_decisions": ["approve", "reject"]},
        {"action_name": "update_jira_ticket", "allowed_decisions": ["approve", "edit", "reject"]},
    ],
}


def _answers(*values: str):
    it = iter(values)
    return lambda prompt="": next(it)


# ------------------------------------------------------------------ review gate payloads


def test_review_gate_approve_echoes_diff_hash():
    resume = review_gate_resume(GATE, "approve")
    assert resume == {"type": "approve", "diff_hash": "abc123"}
    assert parse_review_decision(resume) == ("approve", "", "abc123")


def test_review_gate_feedback_and_reject_carry_message():
    assert review_gate_resume(GATE, "feedback", " Use dataclasses ") == {
        "type": "feedback",
        "message": "Use dataclasses",
    }
    assert review_gate_resume(GATE, "reject", "out of scope") == {
        "type": "reject",
        "message": "out of scope",
    }
    assert review_gate_resume(GATE, "reject") == {"type": "reject", "message": ""}
    for choice, msg in (("feedback", "x"), ("reject", "y")):
        parse_review_decision(review_gate_resume(GATE, choice, msg))  # accepted by the tool


def test_review_gate_rejects_invalid_input():
    with pytest.raises(ValueError):
        review_gate_resume(GATE, "feedback", "   ")  # empty feedback would crash the tool
    with pytest.raises(ValueError):
        review_gate_resume(GATE, "maybe")
    with pytest.raises(ValueError):
        review_gate_resume({**GATE, "diff_hash": ""}, "approve")


# ------------------------------------------------------------------ interrupt_on payloads


def test_hitl_resume_one_decision_per_action_in_order():
    resume = hitl_resume(HITL, ["approve", "reject"], messages=[None, "not now"])
    assert resume == {"decisions": [{"type": "approve"}, {"type": "reject", "message": "not now"}]}


def test_hitl_resume_edit_and_allowed_decisions():
    resume = hitl_resume(
        HITL, ["reject", "edit"], edited_args=[None, {"key": "SHIP-1", "comment": "better"}]
    )
    assert resume["decisions"][1] == {
        "type": "edit",
        "edited_action": {
            "name": "update_jira_ticket",
            "args": {"key": "SHIP-1", "comment": "better"},
        },
    }
    with pytest.raises(ValueError, match="not allowed"):
        hitl_resume(HITL, ["edit", "approve"])  # publish allows only approve/reject
    with pytest.raises(ValueError, match="one decision per action"):
        hitl_resume(HITL, ["approve"])
    with pytest.raises(ValueError):
        hitl_resume(HITL, ["approve", "edit"])  # edit without args


# ------------------------------------------------------------------ interactive dispatch


def _interrupt(value, id_="i1"):
    return SimpleNamespace(id=id_, value=value)


def test_build_resume_review_gate_interactive(capsys):
    cmd = build_resume([_interrupt(GATE)], ask=_answers("approve"))
    assert isinstance(cmd, Command)
    assert cmd.resume == {"type": "approve", "diff_hash": "abc123"}
    out = capsys.readouterr().out
    assert "diff --git a/x b/x" in out and "3 passed" in out

    cmd = build_resume([_interrupt(GATE)], ask=_answers("feedback", "Use dataclasses"))
    assert cmd.resume == {"type": "feedback", "message": "Use dataclasses"}


def test_build_resume_reprompts_on_invalid_choice():
    cmd = build_resume(
        [_interrupt(GATE)], ask=_answers("nope", "feedback", "", "feedback", "fix it")
    )
    assert cmd.resume == {"type": "feedback", "message": "fix it"}


def test_build_resume_hitl_interactive(capsys):
    cmd = build_resume(
        [_interrupt(HITL)],
        ask=_answers("approve", "edit", '{"key": "SHIP-1", "comment": "c2"}'),
    )
    assert cmd.resume["decisions"][0] == {"type": "approve"}
    assert cmd.resume["decisions"][1]["edited_action"]["args"]["comment"] == "c2"
    assert "publish_pull_request" in capsys.readouterr().out


def test_build_resume_multiple_interrupts_keyed_by_id():
    cmd = build_resume(
        [_interrupt(GATE, "a"), _interrupt(GATE, "b")], ask=_answers("approve", "reject", "")
    )
    assert cmd.resume == {
        "a": {"type": "approve", "diff_hash": "abc123"},
        "b": {"type": "reject", "message": ""},
    }


def test_interrupt_on_guards_every_external_write():
    assert set(INTERRUPT_ON) == {
        "publish_pull_request",
        "update_jira_ticket",
        "create_jira_subtasks",
    }
    assert INTERRUPT_ON["publish_pull_request"]["allowed_decisions"] == ["approve", "reject"]
