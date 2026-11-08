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
        # The orchestrator never runs commands and writes only its user's memory.
        ReadOnlyToolsMiddleware(writes_only_under="/memories/"),
    ]


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        return tool.get("name") or (tool.get("function") or {}).get("name")
    return getattr(tool, "name", None)


class PushMatchesWorkspaceMiddleware(AgentMiddleware):
    """`push_files` must push EXACTLY the sandbox's changed files: same paths, same contents.

    The model copies the files from `get_changed_files` into `push_files`; a copy mistake (a
    wrong path, an altered line, a missing file) is refused here before anything reaches
    GitHub. It runs after the human approval, so it also checks edited arguments.
    """

    def __init__(self, sandbox: Any) -> None:
        super().__init__()
        self.sandbox = sandbox

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        call = request.tool_call
        if call["name"] != "push_files":
            return await handler(request)
        from shipwright.sandbox.workspace import changed_files

        expected = {f["path"]: f["content"] for f in (await changed_files(self.sandbox))["files"]}
        sent = {
            str(f.get("path")): f.get("content")
            for f in (call.get("args") or {}).get("files") or []
            if isinstance(f, dict)
        }
        problems = sorted(
            [f"unknown path {p!r}" for p in sent if p not in expected]
            + [f"missing {p}" for p in expected if p not in sent]
            + [f"content differs: {p}" for p in sent if p in expected and sent[p] != expected[p]]
        )
        if not problems:
            return await handler(request)
        return ToolMessage(
            content="NOT PUSHED: the files must be exactly those of get_changed_files ("
            + "; ".join(problems)
            + "). Call get_changed_files again and pass its `files` unchanged.",
            tool_call_id=call["id"],
            name="push_files",
            status="error",
        )


FILE_WRITE_TOOLS = frozenset({"write_file", "edit_file"})


class ReadOnlyToolsMiddleware(AgentMiddleware):
    """Remove built-in tools from the model and refuse them if called anyway.

    Default: `write_file`/`edit_file`/`delete`/`execute` are removed (read-only subagents).
    With `writes_only_under="/memories/"` (the orchestrator), `write_file`/`edit_file` stay
    available but are refused outside that prefix: the orchestrator records preferences, never
    touches the code, and never runs commands. Only the developer does.
    """

    def __init__(
        self,
        blocked: frozenset[str] | set[str] = BUILTIN_WRITE_TOOLS,
        writes_only_under: str | None = None,
    ) -> None:
        super().__init__()
        self.blocked = frozenset(blocked)
        self.writes_only_under = writes_only_under

    def _hidden(self, name: str | None) -> bool:
        return name in self.blocked and not (self.writes_only_under and name in FILE_WRITE_TOOLS)

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        kept = [t for t in request.tools if not self._hidden(_tool_name(t))]
        return await handler(request.override(tools=kept))

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        call = request.tool_call
        name = call["name"]
        if name not in self.blocked:
            return await handler(request)
        path = str((call.get("args") or {}).get("file_path", ""))
        prefix = self.writes_only_under
        if not self._hidden(name) and prefix is not None and path.startswith(prefix):
            return await handler(request)
        where = f" outside {self.writes_only_under}" if not self._hidden(name) else ""
        return ToolMessage(
            content=f"Error: tool '{name}' is not available to this agent{where}.",
            tool_call_id=call["id"],
            name=name,
            status="error",
        )
