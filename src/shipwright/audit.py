"""Audit trail of write-capable tool calls: the event, secret redaction and where it is stored.

Local mode appends JSON lines to `tmp/audit.jsonl`; live mode writes to MongoDB `audit_log`.
The middleware that produces events lives with the agent.
"""

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from shipwright.core.config import Settings

PREVIEW_MAX = 500

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
    result_preview: str  # at most PREVIEW_MAX characters


class AuditSink(Protocol):
    async def record(self, event: AuditEvent) -> None: ...


class JsonlAuditSink:
    """Appends one JSON object per event to `path` (created on first write)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _append(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    async def record(self, event: AuditEvent) -> None:
        data = {**asdict(event), "ts": event.ts.isoformat()}
        await asyncio.to_thread(self._append, json.dumps(data, ensure_ascii=False, default=str))


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


def build_audit_sink(settings: Settings) -> AuditSink:
    if settings.mode == "local":
        return JsonlAuditSink(settings.tmp_dir / "audit.jsonl")
    if settings.mongodb_uri is None:
        raise ValueError("MONGODB_URI is required for the live audit log")
    return MongoAuditSink(settings.mongodb_uri.get_secret_value(), settings.mongodb_db)
