"""Test-only doubles (no network).

The v1 doubles still live in `shipwright.evaluation.fakes` (the v1 eval runner imports them
from `src/`) and are re-exported here; they move into this file when phase B deletes v1.
New-layout doubles are defined below.
"""

from typing import Any

from shipwright.core import config as core_config
from shipwright.evaluation.fakes import *  # noqa: F403
from shipwright.evaluation.fakes import (  # noqa: F401
    FAKE_BASE_SHA,
    FAKE_PR_URL,
    FakeGitHubGateway,
    FakeJiraGateway,
    FakeSandbox,
    FakeSandboxClient,
    FakeSandboxInfo,
    FakeTool,
    FakeVectorStore,
    FakeWebTool,
    FakeWorkspace,
    RecordingChatModel,
    ScriptedChatModel,
    settings_stub,
)

_NO_SECRETS: dict[str, Any] = {
    "langsmith_api_key": None,
    "mongodb_uri": None,
    "pinecone_api_key": None,
    "llama_cloud_api_key": None,
    "tavily_api_key": None,
    "github_pat": None,
    "shipwright_repo": None,
    "jira_url": None,
    "jira_username": None,
    "jira_api_token": None,
}
_LIVE_FAKES: dict[str, Any] = {
    "langsmith_api_key": "ls-fake",
    "mongodb_uri": "mongodb://fake:27017",
    "github_pat": "ghp_fake",
    "shipwright_repo": "acme/taskboard-api",
    "jira_url": "https://acme.atlassian.net",
    "jira_username": "bot@acme.com",
    "jira_api_token": "jira-fake",
}


def make_settings(**overrides: Any) -> core_config.Settings:
    """New-layout `core.config.Settings` with fake values, ignoring `.env` and the environment.

    Local mode by default (every optional secret is None); `mode="live"` fills fake live secrets.
    """
    values: dict[str, Any] = {"openai_api_key": "sk-fake-openai", "mode": "local", **_NO_SECRETS}
    if overrides.get("mode") == "live":
        values.update(_LIVE_FAKES)
    return core_config.Settings(_env_file=None, **{**values, **overrides})


class MemoryAuditSink:
    """In-memory `AuditSink` recording events."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def record(self, event: Any) -> None:
        self.events.append(event)
