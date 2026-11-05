import httpx
import openai
import pytest
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    ToolRetryMiddleware,
)
from langchain.agents.middleware._redaction import PIIDetectionError
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import HumanMessage

from shipwright.integrations.mcp_client import McpToolError
from shipwright.middleware.audit import AuditMiddleware, MemoryAuditSink
from shipwright.middleware.stack import (
    READ_ONLY_RETRY_TOOLS,
    build_orchestrator_middleware,
    is_transient_error,
)


class _S:
    orchestrator_model_call_limit = 150
    model_light = "fake-light"


@pytest.fixture(autouse=True)
def _fake_fallback(monkeypatch):
    monkeypatch.setattr(
        "langchain.agents.middleware.model_fallback.init_chat_model",
        lambda name: GenericFakeChatModel(messages=iter([])),
    )


def _stack():
    return build_orchestrator_middleware(_S(), MemoryAuditSink())


def test_stack_order():
    mws = _stack()
    assert [type(m) for m in mws] == [
        PIIMiddleware,
        PIIMiddleware,
        ModelCallLimitMiddleware,
        ModelRetryMiddleware,
        ToolRetryMiddleware,
        ModelFallbackMiddleware,
        AuditMiddleware,
    ]
    assert (mws[0].pii_type, mws[0].strategy) == ("email", "redact")
    assert (mws[1].pii_type, mws[1].strategy) == ("github_token", "block")
    assert mws[2].run_limit == 150


def test_email_is_redacted_in_input():
    mw = _stack()[0]
    out = mw.before_model({"messages": [HumanMessage("mail me at a.b@corp.io")]}, None)
    assert "a.b@corp.io" not in out["messages"][-1].content
    assert "[REDACTED_EMAIL]" in out["messages"][-1].content


@pytest.mark.parametrize(
    "token", ["ghp_" + "a" * 36, "gho_" + "Z" * 40, "github_pat_" + "A1_" * 15]
)
def test_github_token_blocks_with_exception(token):
    mw = _stack()[1]
    with pytest.raises(PIIDetectionError) as exc:
        mw.before_model({"messages": [HumanMessage(f"use {token} please")]}, None)
    assert exc.value.pii_type == "github_token"
    assert token not in str(exc.value)  # message is safe to show in the CLI


def test_github_token_clean_text_passes():
    mw = _stack()[1]
    assert mw.before_model({"messages": [HumanMessage("ghp_short and normal text")]}, None) is None


def test_retry_only_on_transient_errors():
    req = httpx.Request("GET", "http://x")
    assert is_transient_error(openai.APIConnectionError(request=req))
    assert is_transient_error(openai.APITimeoutError(request=req))
    assert is_transient_error(httpx.ConnectError("x"))
    assert is_transient_error(httpx.ReadTimeout("x"))
    resp = httpx.Response(429, request=req)
    assert is_transient_error(openai.RateLimitError("rl", response=resp, body=None))
    assert not is_transient_error(McpToolError("t", "bad"))
    assert not is_transient_error(ValueError("x"))
    bad = openai.BadRequestError("b", response=httpx.Response(400, request=req), body=None)
    assert not is_transient_error(bad)


def test_retry_config():
    mws = _stack()
    model_retry, tool_retry = mws[3], mws[4]
    for r in (model_retry, tool_retry):
        assert r.max_retries == 2
        assert r.backoff_factor == 2.0
        assert r.retry_on is is_transient_error
    assert model_retry.on_failure == "error"


def test_tool_retry_restricted_to_read_only_tools():
    assert READ_ONLY_RETRY_TOOLS == frozenset(
        {"read_jira_ticket", "knowledge_search", "web_search", "show_workspace_diff"}
    )
    tr = _stack()[4]
    for name in READ_ONLY_RETRY_TOOLS:
        assert tr._should_retry_tool(name)
    for name in (
        "publish_pull_request",
        "update_jira_ticket",
        "create_jira_subtasks",
        "request_human_review",
    ):
        assert not tr._should_retry_tool(name)
