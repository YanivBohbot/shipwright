"""CLI: arguments, clean error messages (no stack trace) and the progress printer."""

import pytest
from langchain.agents.middleware import PIIDetectionError
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

import shipwright.main as cli
from shipwright.core.config import Settings
from shipwright.integrations.mcp import McpConnectionError


def test_parse_args():
    args = cli.parse_args(["run", "SHIP-1", "--user", "bob", "--thread", "t-1"])
    assert (args.ticket, args.user, args.thread) == ("SHIP-1", "bob", "t-1")
    args = cli.parse_args(["run", "SHIP-2"])
    assert args.user and args.thread is None


def _missing_openai_key() -> ValidationError:
    try:
        Settings(_env_file=None, openai_api_key=None)
    except ValidationError as e:
        return e
    raise AssertionError("expected a ValidationError")


@pytest.mark.parametrize(
    "exc, expected",
    [
        (PIIDetectionError("github_token", []), "PII guard"),
        (McpConnectionError("Cannot reach MCP server 'github'."), "Cannot reach MCP server"),
        (_missing_openai_key(), "OPENAI_API_KEY is missing"),
    ],
)
def test_main_prints_clean_errors(monkeypatch, capsys, exc, expected):
    async def boom(*args):
        raise exc

    monkeypatch.setattr(cli, "run_ticket", boom)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["run", "SHIP-1"]) == 1
    err = capsys.readouterr().err
    assert expected in err and "Traceback" not in err


def test_main_rejects_bad_ticket_and_unsafe_thread(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["run", "SHIP-1", "--thread", "../etc"]) == 2
    assert "thread" in capsys.readouterr().err.lower()

    async def bad_key(*args):
        raise cli.CliUsageError("Invalid ticket key 'x'")

    monkeypatch.setattr(cli, "run_ticket", bad_key)
    assert cli.main(["run", "x"]) == 2


def test_progress_labels_subagent_lines_with_their_name(capsys):
    progress = cli.Progress()
    call = {"name": "task", "args": {"subagent_type": "developer", "description": "fix"}}
    progress.on_task((), {"id": "abc", "name": "tools", "input": [{**call, "id": "c1"}]})
    progress.on_update(
        (),
        {"model": {"messages": [AIMessage(content="", tool_calls=[{**call, "id": "c1"}])]}},
    )
    progress.on_update(
        ("tools:abc", "model:1"),
        {"model": {"messages": [AIMessage(content="", tool_calls=[_execute()])]}},
    )
    progress.on_update(
        ("tools:zzz",),
        {"tools": {"messages": [ToolMessage("ok", tool_call_id="x", name="ls")]}},
    )
    progress.on_update((), {"model": {"todos": [{"status": "pending", "content": "Plan it"}]}})
    out = capsys.readouterr().out
    assert "[orchestrator] -> delegates to developer: fix" in out
    assert "[developer] -> execute(" in out
    assert "[subagent]    ls: ok" in out
    assert "[pending] Plan it" in out


def _execute() -> dict:
    return {"name": "execute", "args": {"command": "uv run pytest -q"}, "id": "e1"}
