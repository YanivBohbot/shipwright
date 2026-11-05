"""CLI: resume-payload construction, interrupt dispatch and clean error handling (offline)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware import PIIDetectionError
from langgraph.types import Command

import shipwright.cli as cli
from shipwright.cli import (
    build_resume_command,
    hitl_resume,
    parse_args,
    review_gate_resume,
)
from shipwright.integrations.mcp_client import McpConnectionError
from shipwright.tools.review_gate import parse_review_decision

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


def test_build_resume_command_review_gate_interactive(capsys):
    cmd = build_resume_command([_interrupt(GATE)], ask=_answers("approve"))
    assert isinstance(cmd, Command)
    assert cmd.resume == {"type": "approve", "diff_hash": "abc123"}
    out = capsys.readouterr().out
    assert "diff --git a/x b/x" in out and "3 passed" in out

    cmd = build_resume_command([_interrupt(GATE)], ask=_answers("feedback", "Use dataclasses"))
    assert cmd.resume == {"type": "feedback", "message": "Use dataclasses"}


def test_build_resume_command_reprompts_on_invalid_choice():
    cmd = build_resume_command(
        [_interrupt(GATE)], ask=_answers("nope", "feedback", "", "feedback", "fix it")
    )
    assert cmd.resume == {"type": "feedback", "message": "fix it"}


def test_build_resume_command_hitl_interactive(capsys):
    cmd = build_resume_command(
        [_interrupt(HITL)],
        ask=_answers("approve", "edit", '{"key": "SHIP-1", "comment": "c2"}'),
    )
    assert cmd.resume["decisions"][0] == {"type": "approve"}
    assert cmd.resume["decisions"][1]["edited_action"]["args"]["comment"] == "c2"
    assert "publish_pull_request" in capsys.readouterr().out


def test_build_resume_command_multiple_interrupts_keyed_by_id():
    cmd = build_resume_command(
        [_interrupt(GATE, "a"), _interrupt(GATE, "b")], ask=_answers("approve", "reject", "")
    )
    assert cmd.resume == {
        "a": {"type": "approve", "diff_hash": "abc123"},
        "b": {"type": "reject", "message": ""},
    }


# ------------------------------------------------------------------ arguments and errors


def test_parse_args():
    args = parse_args(["run", "SHIP-1", "--user", "bob", "--thread", "t-1"])
    assert (args.ticket, args.user, args.thread) == ("SHIP-1", "bob", "t-1")
    args = parse_args(["run", "SHIP-2"])
    assert args.user and args.thread is None


@pytest.mark.parametrize(
    "exc, expected",
    [
        (PIIDetectionError("github_token", []), "github_token"),
        (McpConnectionError("Cannot reach MCP server 'github'."), "Cannot reach MCP server"),
    ],
)
def test_main_prints_clean_error(monkeypatch, capsys, exc, expected):
    async def boom(args):
        raise exc

    monkeypatch.setattr(cli, "run_ticket", boom)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["run", "SHIP-1"]) == 1
    err = capsys.readouterr().err
    assert expected in err and "Traceback" not in err


def test_main_rejects_unsafe_thread(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["run", "SHIP-1", "--thread", "../etc"]) == 2
    assert "thread" in capsys.readouterr().err.lower()


def test_resume_mismatch_warning():
    gate = [_interrupt(GATE)]
    assert cli.paused_ticket_keys(gate) == {"SHIP-1"}
    assert cli.paused_ticket_keys([_interrupt(HITL)]) == {"SHIP-1"}
    assert cli.resume_mismatch_warning("SHIP-1", gate) is None
    assert cli.resume_mismatch_warning("ship-1", gate) is None
    warning = cli.resume_mismatch_warning("SHIP-9", gate)
    assert warning is not None and "SHIP-1" in warning and "SHIP-9" in warning
    assert cli.resume_mismatch_warning("SHIP-9", [_interrupt({"kind": "other"})]) is None


async def test_ask_resume_runs_prompts_off_the_event_loop(monkeypatch):
    import threading

    seen: list[str] = []
    main = threading.current_thread().name

    def fake_build(interrupts, ask=input):
        seen.append(threading.current_thread().name)
        return Command(resume={"type": "reject", "message": ""})

    monkeypatch.setattr(cli, "build_resume_command", fake_build)
    cmd = await cli.ask_resume([_interrupt(GATE)])
    assert cmd.resume["type"] == "reject"
    assert seen and seen[0] != main
