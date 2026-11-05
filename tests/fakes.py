"""Test doubles live in `shipwright.evaluation.fakes` (shared with the evals); re-exported here."""

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
