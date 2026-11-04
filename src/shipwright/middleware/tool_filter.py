"""Read-only tool enforcement for subagents.

Verified against langchain 1.4.9 / deepagents 0.7.32: built-in write-capable tools are
`write_file`, `edit_file`, `delete` and `execute`. Filtering `request.tools` hides them from the
model; `wrap_tool_call` additionally refuses them if the model calls one anyway.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from pydantic import BaseModel

READ_ONLY_EXCLUDED = frozenset({"write_file", "edit_file", "delete", "execute"})


class CriterionCheck(BaseModel):
    id: str
    met: bool
    evidence: str


class ReviewReport(BaseModel):
    """Structured self-review of the diff against the ticket's acceptance criteria."""

    verdict: Literal["pass", "changes_requested"]
    criteria: list[CriterionCheck]
    risks: list[str]
    suggestions: list[str]


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        return tool.get("name") or (tool.get("function") or {}).get("name")
    return getattr(tool, "name", None)


class ExcludeToolsMiddleware(AgentMiddleware):
    """Remove named tools from the model request and block them if called anyway."""

    def __init__(self, excluded: frozenset[str]) -> None:
        super().__init__()
        self.excluded = frozenset(excluded)

    def _filter(self, request: Any) -> Any:
        kept = [t for t in request.tools if _tool_name(t) not in self.excluded]
        return request.override(tools=kept)

    def _blocked(self, request: Any) -> ToolMessage | None:
        name = request.tool_call["name"]
        if name not in self.excluded:
            return None
        return ToolMessage(
            content=f"Error: tool '{name}' is not available to this agent (read-only).",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._filter(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._filter(request))

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._blocked(request) or handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return self._blocked(request) or await handler(request)
