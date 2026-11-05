"""Shared fixtures for the agent tests: offline models, a real local workspace, fake deps."""

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from shipwright.agent.dependencies import Dependencies
from shipwright.integrations.github.local_github import ensure_bare_remote
from shipwright.knowledge.search import LocalKeywordSearcher
from shipwright.sandbox.providers import open_workspace
from shipwright.sandbox.workspace import Workspace
from tests.fakes import (
    FakeGitHubGateway,
    FakeJiraGateway,
    FakeWebTool,
    MemoryAuditSink,
    make_settings,
)
from tests.scenarios import git


@pytest.fixture(autouse=True)
def _offline_models(monkeypatch):
    # Subagent model strings are resolved at build time (no network, but a key is required).
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-tests")
    monkeypatch.setattr(
        "langchain.agents.middleware.model_fallback.init_chat_model",
        lambda name: GenericFakeChatModel(messages=iter([])),
    )


def make_tiny_demo(tmp_path: Path) -> Path:
    """`demo/` with tickets.yaml and a one-file git repo (`app/a.py`, `CHANGELOG.md`)."""
    demo = tmp_path / "demo"
    repo = demo / "taskboard-api"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "a.py").write_text("print('a')\n", newline="\n")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n", newline="\n")
    (demo / "tickets.yaml").write_text(
        "- scenario: 1\n  issue_type: Bug\n  expected: pr\n  summary: Fix a\n"
        "  description: |\n    Fix a.\n\n    ## Acceptance criteria\n    - [ ] a prints fixed\n",
        encoding="utf-8",
    )
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    return demo


@pytest.fixture
def local_settings(tmp_path):
    return make_settings(demo_dir=make_tiny_demo(tmp_path), tmp_dir=tmp_path / "tmp")


@pytest.fixture
async def local_ws(local_settings) -> Workspace:
    """A real clone of the tiny demo repo through LocalShellBackend (local mode)."""
    ensure_bare_remote(local_settings.local_source_repo, local_settings.local_remote)
    ws = await open_workspace(local_settings, thread_id="t-test")
    await ws.ensure_cloned()
    return ws


def fake_deps(
    settings: Any, workspace: Any, *, model: Any = None, **overrides: Any
) -> Dependencies:
    values: dict[str, Any] = {
        "settings": settings,
        "workspace": workspace,
        "jira": FakeJiraGateway(),
        "github": FakeGitHubGateway(),
        "searcher": LocalKeywordSearcher(settings.knowledge_dir),
        "web_tool": FakeWebTool().tool,
        "audit_sink": MemoryAuditSink(),
        "model": model if model is not None else settings.model_strong,
        "checkpointer": InMemorySaver(),
        "store": InMemoryStore(),
    }
    return Dependencies(**{**values, **overrides})
