"""Who may write the code and run commands: the developer only."""

from types import SimpleNamespace

from shipwright.agent.backend import PERMISSIONS, SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware, build_middleware
from tests.fakes import make_settings


def denied_paths(permissions):
    return {p for rule in permissions if rule.mode == "deny" for p in rule.paths}


def test_orchestrator_cannot_write_the_code_but_can_write_memories():
    assert "/workspace/**" in denied_paths(PERMISSIONS)
    assert "/memories/**" not in denied_paths(PERMISSIONS)


def test_subagents_may_write_the_code_only_through_the_developer():
    # Subagents share these permissions; the read-only ones also lose the write tools.
    assert "/workspace/**" not in denied_paths(SUBAGENT_PERMISSIONS)
    assert "/memories/**" in denied_paths(SUBAGENT_PERMISSIONS)


def test_orchestrator_has_no_execute():
    guards = [
        m for m in build_middleware(make_settings()) if isinstance(m, ReadOnlyToolsMiddleware)
    ]
    assert [g.blocked for g in guards] == [frozenset({"execute"})]


async def test_blocked_tool_is_hidden_and_refused():
    guard = ReadOnlyToolsMiddleware(blocked={"execute"})
    seen = {}

    class Req:
        tools = [SimpleNamespace(name="execute"), SimpleNamespace(name="read_file")]

        def override(self, tools):
            seen["tools"] = [t.name for t in tools]
            return self

    async def handler(request):
        return "called"

    await guard.awrap_model_call(Req(), handler)
    assert seen["tools"] == ["read_file"]
    call = SimpleNamespace(tool_call={"name": "execute", "id": "c1"})
    refused = await guard.awrap_tool_call(call, handler)
    assert refused.status == "error"
