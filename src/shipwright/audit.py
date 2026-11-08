"""Audit trail of the write tool calls: the event, secret redaction, the MongoDB `audit_log`.

The middleware that produces events lives with the agent (`agent/middleware.py`).
"""

import functools
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from shipwright.core.config import Settings

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic|token)?\s*[^\s,;\"']+"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
)


def redact_secrets(value: Any) -> Any:
    """Recursively mask GitHub tokens, `sk-` API keys and Authorization headers in `value`."""
    if isinstance(value, str):
        out = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", value)
        for pat in _SECRET_PATTERNS[1:]:
            out = pat.sub("[REDACTED]", out)
        return out
    if isinstance(value, dict):
        return {k: redact_secrets(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_secrets(v) for v in value]
    return value


@dataclass
class AuditEvent:
    ts: datetime
    thread_id: str
    user_id: str
    tool: str
    args: dict
    outcome: Literal["ok", "error"]
    result_preview: str


class AuditSink(Protocol):
    async def record(self, event: AuditEvent) -> None: ...


class MongoAuditSink:
    """Writes events to the `audit_log` collection. The client is created lazily."""

    def __init__(self, uri: str | None = None, db: str = "shipwright", *, collection: Any = None):
        self._uri = uri
        self._db = db
        self._collection = collection

    @property
    def collection(self) -> Any:
        if self._collection is None:
            from pymongo import AsyncMongoClient

            self._collection = AsyncMongoClient(self._uri)[self._db]["audit_log"]
        return self._collection

    async def record(self, event: AuditEvent) -> None:
        await self.collection.insert_one(asdict(event))


@functools.cache
def _shared_mongo_sink(uri: str, db: str) -> MongoAuditSink:
    """One sink (one lazily created Mongo client) per process: the graph factory runs per run."""
    return MongoAuditSink(uri, db)


def build_audit_sink(settings: Settings) -> AuditSink:
    return _shared_mongo_sink(settings.mongodb_uri.get_secret_value(), settings.mongodb_db)
