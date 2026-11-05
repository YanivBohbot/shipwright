"""Audit trail for write-capable tools (publish PR, update Jira, ...).

Verified against langchain 1.4.9: `ToolCallRequest` has `tool_call`, `tool`, `state`, `runtime`
(a `ToolRuntime` with `.config` and `.context`). `thread_id` comes from
`runtime.config["configurable"]["thread_id"]`; `user_id` from `runtime.context.user_id`
(`ShipwrightContext`). Missing values fall back to "unknown".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command
from pymongo import AsyncMongoClient

from shipwright.audit import (  # noqa: F401  (one definition, shared with v2)
    PREVIEW_MAX,
    AuditEvent,
    AuditSink,
    redact_secrets,
)

logger = logging.getLogger(__name__)

AUDITED_TOOLS = frozenset(
    {"publish_pull_request", "update_jira_ticket", "create_jira_subtasks", "request_human_review"}
)
_UNKNOWN = "unknown"


class MemoryAuditSink:
    """In-memory sink for tests."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)


class MongoAuditSink:
    """Writes events to the `audit_log` collection. The client is created lazily."""

    def __init__(
        self,
        uri: str | None = None,
        db: str = "shipwright",
        *,
        collection: Any | None = None,
    ) -> None:
        self._uri = uri
        self._db = db
        self._collection = collection

    @property
    def collection(self) -> Any:
        if self._collection is None:
            client = AsyncMongoClient(self._uri)
            self._collection = client[self._db]["audit_log"]
        return self._collection

    async def record(self, event: AuditEvent) -> None:
        await self.collection.insert_one(asdict(event))


def _preview(result: Any) -> str:
    if isinstance(result, ToolMessage):
        text = result.content if isinstance(result.content, str) else str(result.content)
    elif isinstance(result, Command):
        text = f"Command(update={result.update!r})"
    else:
        text = str(result)
    return str(redact_secrets(text))[:PREVIEW_MAX]


class AuditMiddleware(AgentMiddleware):
    """Records every call to a tool in `AUDITED_TOOLS`. Never fails the call itself.

    Graph pauses (`GraphBubbleUp`, e.g. `interrupt()` in `request_human_review`) are re-raised
    without recording: only the final outcome after resume is logged.

    The sync `wrap_tool_call` path is best-effort: with no running loop it uses `asyncio.run`
    (a loop-bound Mongo client may then fail, which is logged as a warning); inside a running
    loop the record is scheduled as a task and may be lost if the loop closes first.
    """

    def __init__(self, sink: AuditSink) -> None:
        super().__init__()
        self.sink = sink
        self._pending: set[asyncio.Task] = set()

    @staticmethod
    def _ids(request: Any) -> tuple[str, str]:
        runtime = getattr(request, "runtime", None)
        config = getattr(runtime, "config", None) or {}
        thread_id = (config.get("configurable") or {}).get("thread_id")
        if thread_id is None:
            thread_id = getattr(getattr(runtime, "execution_info", None), "thread_id", None)
        user_id = getattr(getattr(runtime, "context", None), "user_id", None)
        return str(thread_id or _UNKNOWN), str(user_id or _UNKNOWN)

    def _event(self, request: Any, outcome: Literal["ok", "error"], preview: str) -> AuditEvent:
        thread_id, user_id = self._ids(request)
        call = request.tool_call
        return AuditEvent(
            ts=datetime.now(UTC),
            thread_id=thread_id,
            user_id=user_id,
            tool=call["name"],
            args=redact_secrets(dict(call.get("args") or {})),
            outcome=outcome,
            result_preview=preview,
        )

    async def _arecord(self, event: AuditEvent) -> None:
        try:
            await self.sink.record(event)
        except Exception:
            logger.warning("audit sink failed to record %s event", event.tool, exc_info=True)

    def _record_sync(self, event: AuditEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._arecord(event))
            return
        task = loop.create_task(self._arecord(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if request.tool_call["name"] not in AUDITED_TOOLS:
            return await handler(request)
        try:
            result = await handler(request)
        except GraphBubbleUp:
            raise  # interrupt()/pause: not a failure; the tool re-runs on resume
        except Exception as exc:
            await self._arecord(
                self._event(request, "error", _preview(f"{type(exc).__name__}: {exc}"))
            )
            raise
        await self._arecord(self._event(request, "ok", _preview(result)))
        return result

    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if request.tool_call["name"] not in AUDITED_TOOLS:
            return handler(request)
        try:
            result = handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            self._record_sync(
                self._event(request, "error", _preview(f"{type(exc).__name__}: {exc}"))
            )
            raise
        self._record_sync(self._event(request, "ok", _preview(result)))
        return result
