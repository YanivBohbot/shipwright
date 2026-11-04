from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from shipwright.middleware.tool_filter import READ_ONLY_EXCLUDED, ExcludeToolsMiddleware


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(func=lambda: "ok", name=name, description=name)


def _request(tools):
    from langchain.agents.middleware import ModelRequest

    return ModelRequest(model=None, messages=[], tools=tools)  # type: ignore[arg-type]


def test_excluded_names_are_real_builtin_names():
    assert READ_ONLY_EXCLUDED == frozenset({"write_file", "edit_file", "delete", "execute"})


async def test_exclude_tools_middleware_removes_write_tools():
    names = ["read_file", "write_file", "execute", "grep", "edit_file", "delete"]
    req = _request([_tool(n) for n in names])
    seen = {}

    async def handler(r):
        seen["req"] = r
        return "resp"

    out = await ExcludeToolsMiddleware(READ_ONLY_EXCLUDED).awrap_model_call(req, handler)
    assert out == "resp"
    assert [t.name for t in seen["req"].tools] == ["read_file", "grep"]
    assert len(req.tools) == 6  # original untouched


def test_sync_wrap_model_call_and_dict_tools():
    req = _request(
        [
            _tool("read_file"),
            {"name": "write_file"},
            {"type": "function", "function": {"name": "execute"}},
            {"type": "web_search"},
        ]
    )
    seen = {}

    def handler(r):
        seen["req"] = r
        return "resp"

    ExcludeToolsMiddleware(READ_ONLY_EXCLUDED).wrap_model_call(req, handler)
    kept = seen["req"].tools
    assert len(kept) == 2
    assert kept[0].name == "read_file"
    assert kept[1] == {"type": "web_search"}


class _Req:
    def __init__(self, name: str):
        self.tool_call = {"name": name, "args": {}, "id": "call_1"}


async def test_blocks_excluded_tool_call_without_running_it():
    called = []

    async def handler(r):
        called.append(r)
        return ToolMessage(content="ran", tool_call_id="call_1")

    mw = ExcludeToolsMiddleware(READ_ONLY_EXCLUDED)
    msg = await mw.awrap_tool_call(_Req("write_file"), handler)
    assert isinstance(msg, ToolMessage)
    assert msg.status == "error"
    assert msg.tool_call_id == "call_1"
    assert "write_file" in msg.content
    assert called == []


async def test_allows_other_tool_calls():
    async def handler(r):
        return ToolMessage(content="ran", tool_call_id="call_1")

    msg = await ExcludeToolsMiddleware(READ_ONLY_EXCLUDED).awrap_tool_call(_Req("grep"), handler)
    assert msg.content == "ran"


def test_sync_guard_blocks():
    mw = ExcludeToolsMiddleware(READ_ONLY_EXCLUDED)
    msg = mw.wrap_tool_call(_Req("execute"), lambda r: (_ for _ in ()).throw(AssertionError()))
    assert msg.status == "error"
