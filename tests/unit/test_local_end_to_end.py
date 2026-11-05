"""Local mode end to end through `main.run_ticket` (the CLI loop), with a scripted model.

Real pieces: LocalJira, LocalGitHub (bare remote), the local workspace (git), the agent graph,
the HITL prompts (answered through a patched `input`). Only the chat model is scripted.
"""

import builtins
import subprocess
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

import shipwright.agent.orchestrator as orch
import shipwright.core.config as config_mod
import shipwright.main as cli
from shipwright.integrations.jira.local_jira import LocalJira
from shipwright.sandbox.git_diff import git_blob_sha
from tests.fakes import ScriptedChatModel, make_settings
from tests.unit.agent.conftest import make_tiny_demo


def _call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


SCRIPT = [
    _call("read_jira_ticket", {"key": "SHIP-1"}, "c1"),
    _call("read_file", {"file_path": "/workspace/repo/app/a.py"}, "c2"),
    _call(
        "edit_file",
        {
            "file_path": "/workspace/repo/app/a.py",
            "old_string": "print('a')",
            "new_string": "print('fixed')",
        },
        "c3",
    ),
    _call(
        "request_human_review",
        {
            "ticket_key": "SHIP-1",
            "summary": "Fix a",
            "test_report": "1 passed",
            "review_report": "Verdict: pass",
        },
        "c4",
    ),
    _call(
        "publish_pull_request",
        {
            "ticket_key": "SHIP-1",
            "title": "Fix a",
            "body": "Fixes a.",
            "changelog_entry": "Fix a (SHIP-1)",
        },
        "c5",
    ),
    _call(
        "update_jira_ticket",
        {"key": "SHIP-1", "comment": "PR published.", "transition_to": "In Review"},
        "c6",
    ),
    AIMessage(content="SHIP-1 done: PR published."),
]


@pytest.fixture
def local_run(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-tests")
    monkeypatch.setattr(
        "langchain.agents.middleware.model_fallback.init_chat_model",
        lambda name: GenericFakeChatModel(messages=iter([])),
    )
    settings = make_settings(demo_dir=make_tiny_demo(tmp_path), tmp_dir=tmp_path / "tmp")
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    real_build = orch.build_shipwright_agent

    def with_scripted_model(deps):
        deps.model = ScriptedChatModel(messages=iter(SCRIPT))
        return real_build(deps)

    monkeypatch.setattr(orch, "build_shipwright_agent", with_scripted_model)
    answers = iter(["approve", "approve", "approve"])  # review gate, publish, Jira update
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    return settings


async def test_local_run_publishes_the_approved_diff_and_comments(local_run, capsys):
    settings = local_run
    await cli.run_ticket("SHIP-1", "alice", "e2e-1")
    out = capsys.readouterr().out
    assert "[orchestrator] -> request_human_review(" in out and "SHIP-1 done" in out
    assert out.count("-> publish_pull_request(") == 1  # not repeated after the HITL pause

    (pr,) = list(settings.local_prs_dir.rglob("*.md"))
    assert "[SHIP-1] Fix a" in pr.read_text(encoding="utf-8")

    def remote_blob(path: str) -> str:
        return subprocess.run(
            [
                "git",
                "--git-dir",
                str(settings.local_remote),
                "rev-parse",
                f"shipwright/ship-1:{path}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    approved = git_blob_sha(b"print('fixed')\n")
    assert remote_blob("app/a.py") == approved
    assert approved in out  # the blob id shown to the human in the full-index diff
    changelog = subprocess.run(
        ["git", "--git-dir", str(settings.local_remote), "show", "shipwright/ship-1:CHANGELOG.md"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "- Fix a (SHIP-1)" in changelog

    jira = LocalJira(settings.local_tickets, settings.local_jira_state, "SHIP")
    summary = jira.summary_text()
    assert "SHIP-1 [In Review]" in summary and "PR published." in summary
    assert Path(settings.tmp_dir / "audit.jsonl").read_text(encoding="utf-8").count("\n") == 3
