"""Deep Agents concept: **middleware** (hooks around the agent loop, model calls and tool calls).

Prebuilt LangChain middleware:
- `TodoListMiddleware`: the `write_todos` planning tool (not in deepagents' default stack).
- `PIIMiddleware`: redact emails; BLOCK GitHub tokens (raises `PIIDetectionError`).
- `ModelCallLimitMiddleware`, `ModelRetryMiddleware`, `ModelFallbackMiddleware`.
- `ToolRetryMiddleware` on the orchestrator's read tool (retrying a write could duplicate it).
Custom middleware:
- `ReadOnlyToolsMiddleware`: hides and refuses the built-in write tools (`write_file`,
  `execute`...) in every subagent except the developer.
The audit of MCP writes is an MCP interceptor (`integrations/authorization.py`), so it sees the
calls of every subagent.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import openai
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    TodoListMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.messages import ToolMessage

from shipwright.core.config import Settings

# Classic `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` tokens plus fine-grained `github_pat_` tokens.
GITHUB_TOKEN_PATTERN = r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"
BUILTIN_WRITE_TOOLS = frozenset({"write_file", "edit_file", "delete", "execute"})
_TRANSIENT = (openai.APIConnectionError, openai.RateLimitError, httpx.TransportError)


def is_transient_error(exc: BaseException) -> bool:
    """Network / timeout / rate-limit errors only (never a tool's own logic error)."""
    return isinstance(exc, _TRANSIENT)


def build_middleware(settings: Settings) -> list[AgentMiddleware[Any, Any, Any]]:
    """The orchestrator's middleware, in order (deepagents adds its own around it)."""
    retry = {"max_retries": 2, "retry_on": is_transient_error, "backoff_factor": 2.0}
    return [
        TodoListMiddleware(),
        PIIMiddleware("email", strategy="redact", apply_to_input=True),
        PIIMiddleware(
            "github_token", detector=GITHUB_TOKEN_PATTERN, strategy="block", apply_to_input=True
        ),
        ModelCallLimitMiddleware(run_limit=settings.orchestrator_model_call_limit),
        ModelRetryMiddleware(on_failure="error", **retry),
        ToolRetryMiddleware(tools=["jira_get_issue"], on_failure="continue", **retry),
        ModelFallbackMiddleware(settings.model_light),
    ]


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        return tool.get("name") or (tool.get("function") or {}).get("name")
    return getattr(tool, "name", None)


class ReadOnlyToolsMiddleware(AgentMiddleware):
    """Remove `write_file`/`edit_file`/`delete`/`execute` from the model; refuse them if called."""

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        kept = [t for t in request.tools if _tool_name(t) not in BUILTIN_WRITE_TOOLS]
        return await handler(request.override(tools=kept))

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        name = request.tool_call["name"]
        if name not in BUILTIN_WRITE_TOOLS:
            return await handler(request)
        return ToolMessage(
            content=f"Error: tool '{name}' is not available to this agent (read-only).",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )
