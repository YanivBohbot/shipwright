"""Deep Agents concept: **middleware** (hooks around the agent loop, model calls and tool calls).

Prebuilt LangChain middleware (verified on langchain 1.4.9):
- `PIIMiddleware`: redact emails; BLOCK GitHub tokens (raises `PIIDetectionError`, which never
  echoes the secret; `main.py` shows it cleanly).
- `ModelCallLimitMiddleware`, `ModelRetryMiddleware`, `ToolRetryMiddleware` (read-only tools
  only: retrying a write could duplicate a PR or a comment), `ModelFallbackMiddleware`.
Custom middleware (async only: the CLI and the server run the graph with `ainvoke`/`astream`):
- `PolicySeedMiddleware` (`abefore_agent`): seeds `/policies/POLICIES.md` into the run's store.
  It is placed before deepagents' `MemoryMiddleware`, which reads the policies.
- `AuditMiddleware` (`awrap_tool_call`): one `AuditEvent` per write-capable tool call.
- `ReadOnlyToolsMiddleware` (`awrap_model_call` + `awrap_tool_call`): hides and refuses the
  write-capable built-in tools in read-only subagents.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
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
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from shipwright.agent.backend import seed_policies_if_absent
from shipwright.audit import PREVIEW_MAX, AuditEvent, AuditSink, redact_secrets
from shipwright.core.config import Settings

logger = logging.getLogger(__name__)

# Classic `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` tokens plus fine-grained `github_pat_` tokens.
GITHUB_TOKEN_PATTERN = r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"
READ_ONLY_RETRY_TOOLS = ("read_jira_ticket", "show_workspace_diff")  # orchestrator tools
AUDITED_TOOLS = frozenset(
    {"publish_pull_request", "update_jira_ticket", "create_jira_subtasks", "request_human_review"}
)
WRITE_TOOLS = frozenset({"write_file", "edit_file", "delete", "execute"})
_TRANSIENT = (openai.APIConnectionError, openai.RateLimitError, httpx.TransportError)


def is_transient_error(exc: BaseException) -> bool:
    """Network / timeout / rate-limit errors only (never a tool's own logic error)."""
    return isinstance(exc, _TRANSIENT)


def build_middleware(settings: Settings, audit_sink: AuditSink) -> list[AgentMiddleware]:
    """The orchestrator's custom middleware, in order (deepagents adds its own around it)."""
    retry = {"max_retries": 2, "retry_on": is_transient_error, "backoff_factor": 2.0}
    return [
        TodoListMiddleware(),  # `write_todos` (planning) is not in the default deep agent stack
        PIIMiddleware("email", strategy="redact", apply_to_input=True),
        PIIMiddleware(
            "github_token", detector=GITHUB_TOKEN_PATTERN, strategy="block", apply_to_input=True
        ),
        ModelCallLimitMiddleware(run_limit=settings.orchestrator_model_call_limit),
        ModelRetryMiddleware(on_failure="error", **retry),
        ToolRetryMiddleware(tools=list(READ_ONLY_RETRY_TOOLS), on_failure="continue", **retry),
        ModelFallbackMiddleware(settings.model_light),
        AuditMiddleware(audit_sink),
        PolicySeedMiddleware(settings),
    ]


class PolicySeedMiddleware(AgentMiddleware):
    """Seed the org policies into whatever store the graph got (local, Mongo or platform)."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if (store := getattr(runtime, "store", None)) is not None:
            await seed_policies_if_absent(store, self.settings)
        return None


def _preview(result: Any) -> str:
    if isinstance(result, ToolMessage):
        text = str(result.content)
    elif isinstance(result, Command):
        text = f"Command(update={result.update!r})"
    else:
        text = str(result)
    return str(redact_secrets(text))[:PREVIEW_MAX]


class AuditMiddleware(AgentMiddleware):
    """Record every call of an `AUDITED_TOOLS` tool; never fails the call itself.

    A graph pause (`GraphBubbleUp` from `interrupt()`) is not recorded: the tool re-runs on
    resume and only its final outcome is logged.
    """

    def __init__(self, sink: AuditSink) -> None:
        super().__init__()
        self.sink = sink

    async def _record(self, request: Any, outcome: str, preview: str) -> None:
        runtime = getattr(request, "runtime", None)
        config = getattr(runtime, "config", None) or {}
        thread_id = (config.get("configurable") or {}).get("thread_id")
        user_id = getattr(getattr(runtime, "context", None), "user_id", None)
        call = request.tool_call
        event = AuditEvent(
            ts=datetime.now(UTC),
            thread_id=str(thread_id or "unknown"),
            user_id=str(user_id or "unknown"),
            tool=call["name"],
            args=redact_secrets(dict(call.get("args") or {})),
            outcome=outcome,  # type: ignore[arg-type]
            result_preview=preview,
        )
        try:
            await self.sink.record(event)
        except Exception:
            logger.warning("audit sink failed to record %s", event.tool, exc_info=True)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[ToolMessage | Command]]
    ) -> ToolMessage | Command:
        if request.tool_call["name"] not in AUDITED_TOOLS:
            return await handler(request)
        try:
            result = await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            await self._record(request, "error", _preview(f"{type(exc).__name__}: {exc}"))
            raise
        await self._record(request, "ok", _preview(result))
        return result


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        return tool.get("name") or (tool.get("function") or {}).get("name")
    return getattr(tool, "name", None)


class ReadOnlyToolsMiddleware(AgentMiddleware):
    """Remove `write_file`/`edit_file`/`delete`/`execute` from requests; refuse them if called."""

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        kept = [t for t in request.tools if _tool_name(t) not in WRITE_TOOLS]
        return await handler(request.override(tools=kept))

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        name = request.tool_call["name"]
        if name not in WRITE_TOOLS:
            return await handler(request)
        return ToolMessage(
            content=f"Error: tool '{name}' is not available to this agent (read-only).",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )
