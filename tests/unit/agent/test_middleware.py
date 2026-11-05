"""Middleware stack (order, PII, limits, retries) and the custom audit / read-only middleware."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRequest,
    ModelRetryMiddleware,
    PIIMiddleware,
    TodoListMiddleware,
    ToolRetryMiddleware,
)
from langchain.agents.middleware._redaction import PIIDetectionError
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphInterrupt

from shipwright.agent.middleware import (
    AUDITED_TOOLS,
    READ_ONLY_RETRY_TOOLS,
    WRITE_TOOLS,
    AuditMiddleware,
    PolicySeedMiddleware,
    ReadOnlyToolsMiddleware,
    build_middleware,
    is_transient_error,
)
from shipwright.agent.state import ShipwrightContext
from shipwright.audit import redact_secrets
from shipwright.integrations.mcp import McpToolError
from tests.fakes import MemoryAuditSink, make_settings

GH = "ghp_" + "a" * 36


def _stack():
    return build_middleware(make_settings(orchestrator_model_call_limit=150), MemoryAuditSink())


def test_stack_order_and_config():
    mws = _stack()
    assert [type(m) for m in mws] == [
        TodoListMiddleware,
        PIIMiddleware,
        PIIMiddleware,
        ModelCallLimitMiddleware,
        ModelRetryMiddleware,
        ToolRetryMiddleware,
        ModelFallbackMiddleware,
        AuditMiddleware,
        PolicySeedMiddleware,
    ]
    assert (mws[1].pii_type, mws[1].strategy) == ("email", "redact")
    assert (mws[2].pii_type, mws[2].strategy) == ("github_token", "block")
    assert mws[3].run_limit == 150
    for r in (mws[4], mws[5]):
        assert r.max_retries == 2 and r.backoff_factor == 2.0 and r.retry_on is is_transient_error
    assert mws[4].on_failure == "error"


def test_email_is_redacted_in_input():
    out = _stack()[1].before_model({"messages": [HumanMessage("mail me at a.b@corp.io")]}, None)
    assert "a.b@corp.io" not in out["messages"][-1].content
    assert "[REDACTED_EMAIL]" in out["messages"][-1].content


@pytest.mark.parametrize("token", [GH, "gho_" + "Z" * 40, "github_pat_" + "A1_" * 15])
def test_github_token_blocks_without_echoing_it(token):
    with pytest.raises(PIIDetectionError) as exc:
        _stack()[2].before_model({"messages": [HumanMessage(f"use {token} please")]}, None)
    assert exc.value.pii_type == "github_token" and token not in str(exc.value)
    assert _stack()[2].before_model({"messages": [HumanMessage("ghp_short")]}, None) is None


def test_retry_only_on_transient_errors():
    req = httpx.Request("GET", "http://x")
    assert is_transient_error(openai.APIConnectionError(request=req))
    assert is_transient_error(openai.APITimeoutError(request=req))
    assert is_transient_error(httpx.ReadTimeout("x"))
    resp = httpx.Response(429, request=req)
    assert is_transient_error(openai.RateLimitError("rl", response=resp, body=None))
    assert not is_transient_error(McpToolError("t", "bad"))
    assert not is_transient_error(ValueError("x"))


def test_tool_retry_restricted_to_read_only_tools():
    tr = _stack()[5]
    assert set(READ_ONLY_RETRY_TOOLS) == {"read_jira_ticket", "show_workspace_diff"}
    assert all(tr._should_retry_tool(n) for n in READ_ONLY_RETRY_TOOLS)
    assert not any(tr._should_retry_tool(n) for n in AUDITED_TOOLS)


# ------------------------------------------------------------------ AuditMiddleware


def _request(name: str, args: dict | None = None, context=None, config=None):
    runtime = SimpleNamespace(
        context=context if context is not None else ShipwrightContext(user_id="u-1"),
        config=config if config is not None else {"configurable": {"thread_id": "t-9"}},
    )
    return SimpleNamespace(
        tool_call={"name": name, "args": args or {}, "id": "c1"}, runtime=runtime
    )


async def test_audit_records_audited_tool_with_redaction():
    sink = MemoryAuditSink()

    async def handler(req):
        return ToolMessage(content="x" * 900, tool_call_id="c1")

    args = {"title": "t", "token": GH, "note": f"Authorization: Bearer {GH}"}
    await AuditMiddleware(sink).awrap_tool_call(_request("publish_pull_request", args), handler)
    (ev,) = sink.events
    assert (ev.tool, ev.outcome, ev.thread_id, ev.user_id) == (
        "publish_pull_request",
        "ok",
        "t-9",
        "u-1",
    )
    assert len(ev.result_preview) <= 500 and GH not in str(ev.args) and ev.args["title"] == "t"


async def test_audit_records_error_reraises_and_ignores_other_tools():
    sink = MemoryAuditSink()

    async def boom(req):
        raise RuntimeError(f"boom {GH}")

    with pytest.raises(RuntimeError):
        await AuditMiddleware(sink).awrap_tool_call(_request("update_jira_ticket"), boom)
    assert sink.events[0].outcome == "error" and GH not in sink.events[0].result_preview

    async def ok(req):
        return ToolMessage(content="ok", tool_call_id="c1")

    await AuditMiddleware(sink).awrap_tool_call(_request("read_jira_ticket"), ok)
    assert len(sink.events) == 1


async def test_audit_sink_failure_does_not_break_the_call(caplog):
    sink = AsyncMock()
    sink.record.side_effect = RuntimeError("mongo down")
    msg = ToolMessage(content="ok", tool_call_id="c1")

    async def handler(req):
        return msg

    with caplog.at_level(logging.WARNING):
        out = await AuditMiddleware(sink).awrap_tool_call(_request("create_jira_subtasks"), handler)
    assert out is msg and "audit" in caplog.text.lower()


async def test_audit_missing_runtime_info_falls_back_to_unknown():
    sink = MemoryAuditSink()

    async def handler(req):
        return "ok"

    req = _request("publish_pull_request", context=SimpleNamespace(), config={"x": 1})
    await AuditMiddleware(sink).awrap_tool_call(req, handler)
    assert (sink.events[0].thread_id, sink.events[0].user_id) == ("unknown", "unknown")


async def test_graph_interrupt_is_not_recorded_then_ok_once():
    sink = MemoryAuditSink()
    mw = AuditMiddleware(sink)

    async def pausing(req):
        raise GraphInterrupt()

    with pytest.raises(GraphInterrupt):
        await mw.awrap_tool_call(_request("request_human_review"), pausing)
    assert sink.events == []

    async def done(req):
        return ToolMessage(content="approved", tool_call_id="c1")

    await mw.awrap_tool_call(_request("request_human_review"), done)
    assert [e.outcome for e in sink.events] == ["ok"]


def test_redact_secrets():
    out = str(
        redact_secrets(
            {
                "a": [GH, "github_pat_" + "B" * 30],
                "b": "sk-" + "c" * 30,
                "h": "Authorization: Basic abc123==",
            }
        )
    )
    assert GH not in out and "github_pat_B" not in out and "sk-ccc" not in out
    assert "abc123" not in out


# ------------------------------------------------------------------ ReadOnlyToolsMiddleware


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(func=lambda: "ok", name=name, description=name)


def test_write_tools_are_the_real_builtin_names():
    assert WRITE_TOOLS == frozenset({"write_file", "edit_file", "delete", "execute"})


async def test_read_only_hides_write_tools_from_the_model():
    tools = [
        _tool(n) for n in ("read_file", "write_file", "execute", "grep", "edit_file", "delete")
    ]
    tools += [{"name": "write_file"}, {"type": "function", "function": {"name": "execute"}}]
    tools.append({"type": "web_search"})
    req = ModelRequest(model=None, messages=[], tools=tools)  # type: ignore[arg-type]
    seen = {}

    async def handler(r):
        seen["tools"] = r.tools
        return "resp"

    assert await ReadOnlyToolsMiddleware().awrap_model_call(req, handler) == "resp"
    kept = [getattr(t, "name", t) for t in seen["tools"]]
    assert kept == ["read_file", "grep", {"type": "web_search"}]
    assert len(req.tools) == 9  # the original request is untouched


async def test_read_only_refuses_a_write_call_without_running_it():
    called = []

    async def handler(r):
        called.append(r)
        return ToolMessage(content="ran", tool_call_id="c1")

    blocked = await ReadOnlyToolsMiddleware().awrap_tool_call(_request("write_file"), handler)
    assert blocked.status == "error" and "write_file" in blocked.content and called == []
    allowed = await ReadOnlyToolsMiddleware().awrap_tool_call(_request("grep"), handler)
    assert allowed.content == "ran"
