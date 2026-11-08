"""Who may write the code and run commands: the developer only."""

from types import SimpleNamespace

import pytest

import shipwright.sandbox.workspace as ws
from shipwright.agent.backend import PERMISSIONS, SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware, build_middleware
from shipwright.agent.orchestrator import build_shipwright_agent
from tests.fakes import ScriptedChatModel, fake_mcp, make_settings


def denied_paths(permissions):
    return {p for rule in permissions if rule.mode == "deny" for p in rule.paths}


def test_permissions_only_target_the_routed_stores():
    # deepagents refuses permissions on the sandbox route (it has `execute`).
    assert denied_paths(PERMISSIONS) == {"/policies/**", "/skills/**"}
    assert denied_paths(SUBAGENT_PERMISSIONS) == {"/policies/**", "/skills/**", "/memories/**"}


async def test_the_agent_builds_on_a_real_sandbox_backend(tmp_path, monkeypatch):
    """Regression: a permission on `/workspace/**` made create_deep_agent raise here."""
    monkeypatch.setattr(ws, "LOCAL_ROOT", tmp_path)
    sandbox = await ws.open_sandbox(make_settings(sandbox_provider="local"), "build-check")
    agent = build_shipwright_agent(
        make_settings(), mcp=fake_mcp(), sandbox=sandbox, model=ScriptedChatModel(messages=iter([]))
    )
    assert agent is not None


def orchestrator_guard():
    (guard,) = [
        m for m in build_middleware(make_settings()) if isinstance(m, ReadOnlyToolsMiddleware)
    ]
    return guard


async def handler(request):
    return "ran"


def call(name, **args):
    return SimpleNamespace(tool_call={"name": name, "id": "c1", "args": args})


async def test_orchestrator_sees_no_execute_nor_delete():
    seen = {}

    class Req:
        tools = [SimpleNamespace(name=n) for n in ("execute", "delete", "write_file", "read_file")]

        def override(self, tools):
            seen["tools"] = [t.name for t in tools]
            return self

    await orchestrator_guard().awrap_model_call(Req(), handler)
    assert seen["tools"] == ["write_file", "read_file"]


@pytest.mark.parametrize(
    ("name", "path", "allowed"),
    [
        ("edit_file", "/memories/preferences.md", True),
        ("write_file", "/memories/preferences.md", True),
        ("write_file", "/workspace/repo/app/models.py", False),
        ("edit_file", "/workspace/repo/app/models.py", False),
        ("execute", "", False),
        ("read_file", "/workspace/repo/app/models.py", True),
    ],
)
async def test_orchestrator_writes_only_its_users_memory(name, path, allowed):
    result = await orchestrator_guard().awrap_tool_call(call(name, file_path=path), handler)
    assert (result == "ran") is allowed


async def test_read_only_subagent_guard_refuses_every_write():
    guard = ReadOnlyToolsMiddleware()
    for name in ("write_file", "edit_file", "delete", "execute"):
        result = await guard.awrap_tool_call(call(name, file_path="/memories/x.md"), handler)
        assert result.status == "error"
