"""Orchestrator middleware stack (PII, limits, retries, fallback, audit).

Verified against langchain 1.4.9:
- `PIIMiddleware(pii_type, *, strategy, detector: Callable | regex str | None, apply_to_input,
  apply_to_output, apply_to_tool_results)`. Strategy `block` RAISES `PIIDetectionError`
  (message: "Detected N instance(s) of github_token in text content" - it never echoes the
  secret), which aborts the run; the CLI must catch it and show that message.
- `ModelCallLimitMiddleware(*, thread_limit=None, run_limit=None, exit_behavior="end"|"error")`.
- `ModelRetryMiddleware(*, max_retries, retry_on, on_failure, backoff_factor, initial_delay,
  max_delay, jitter)`; `ToolRetryMiddleware` same plus `tools=[names]`.
- `ModelFallbackMiddleware(first_model, *additional_models)` (positional).
"""

from __future__ import annotations

from typing import Any

import httpx
import openai
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    ToolRetryMiddleware,
)

from shipwright.middleware.audit import AuditMiddleware, AuditSink

# Classic `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` tokens plus fine-grained `github_pat_` tokens.
GITHUB_TOKEN_PATTERN = r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"

# Only side-effect-free tools may be retried: retrying a write could duplicate PRs/comments.
READ_ONLY_RETRY_TOOLS = frozenset(
    {"read_jira_ticket", "knowledge_search", "web_search", "show_workspace_diff"}
)

_TRANSIENT = (
    openai.APIConnectionError,  # includes openai.APITimeoutError
    openai.RateLimitError,
    httpx.TransportError,  # connect/read/write/pool timeouts, network errors
)


def is_transient_error(exc: BaseException) -> bool:
    """True for network/timeout/rate-limit errors only (not tool logic errors like McpToolError)."""
    return isinstance(exc, _TRANSIENT)


def build_orchestrator_middleware(settings: Any, audit_sink: AuditSink) -> list[AgentMiddleware]:
    retry_kwargs = {
        "max_retries": 2,
        "retry_on": is_transient_error,
        "backoff_factor": 2.0,
        "initial_delay": 1.0,
    }
    return [
        PIIMiddleware("email", strategy="redact", apply_to_input=True),
        PIIMiddleware(
            "github_token",
            detector=GITHUB_TOKEN_PATTERN,
            strategy="block",
            apply_to_input=True,
        ),
        ModelCallLimitMiddleware(run_limit=settings.orchestrator_model_call_limit),
        ModelRetryMiddleware(on_failure="error", **retry_kwargs),
        ToolRetryMiddleware(
            tools=sorted(READ_ONLY_RETRY_TOOLS), on_failure="continue", **retry_kwargs
        ),
        ModelFallbackMiddleware(settings.model_light),
        AuditMiddleware(audit_sink),
    ]
