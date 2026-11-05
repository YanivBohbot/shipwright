import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage

from shipwright.context import ShipwrightContext
from shipwright.middleware.audit import (
    AUDITED_TOOLS,
    AuditEvent,
    AuditMiddleware,
    MemoryAuditSink,
    MongoAuditSink,
    redact_secrets,
)

GH = "ghp_" + "a" * 36


def _request(name: str, args: dict | None = None):
    runtime = SimpleNamespace(
        context=ShipwrightContext(user_id="u-1"),
        config={"configurable": {"thread_id": "t-9"}},
    )
    return SimpleNamespace(
        tool_call={"name": name, "args": args or {}, "id": "c1"}, runtime=runtime
    )


def test_audited_tools_constant():
    assert AUDITED_TOOLS == frozenset(
        {
            "publish_pull_request",
            "update_jira_ticket",
            "create_jira_subtasks",
            "request_human_review",
        }
    )


async def test_audit_records_audited_tool_ok():
    sink = MemoryAuditSink()
    mw = AuditMiddleware(sink)

    async def handler(req):
        return ToolMessage(content="x" * 900, tool_call_id="c1")

    args = {"title": "t", "token": GH, "note": f"Authorization: Bearer {GH}"}
    await mw.awrap_tool_call(_request("publish_pull_request", args), handler)
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert (ev.tool, ev.outcome, ev.thread_id, ev.user_id) == (
        "publish_pull_request",
        "ok",
        "t-9",
        "u-1",
    )
    assert len(ev.result_preview) <= 500
    assert GH not in str(ev.args)
    assert ev.args["title"] == "t"


async def test_audit_records_error_and_reraises():
    sink = MemoryAuditSink()

    async def handler(req):
        raise RuntimeError(f"boom {GH}")

    with pytest.raises(RuntimeError):
        await AuditMiddleware(sink).awrap_tool_call(_request("update_jira_ticket"), handler)
    assert sink.events[0].outcome == "error"
    assert "boom" in sink.events[0].result_preview
    assert GH not in sink.events[0].result_preview


async def test_audit_ignores_unaudited_tools():
    sink = MemoryAuditSink()

    async def handler(req):
        return ToolMessage(content="ok", tool_call_id="c1")

    await AuditMiddleware(sink).awrap_tool_call(_request("read_jira_ticket"), handler)
    assert sink.events == []


async def test_audit_sink_failure_does_not_break_call(caplog):
    sink = AsyncMock()
    sink.record.side_effect = RuntimeError("mongo down")
    msg = ToolMessage(content="ok", tool_call_id="c1")

    async def handler(req):
        return msg

    with caplog.at_level(logging.WARNING):
        out = await AuditMiddleware(sink).awrap_tool_call(_request("create_jira_subtasks"), handler)
    assert out is msg
    assert "audit" in caplog.text.lower()


async def test_sync_wrap_tool_call_records_inside_running_loop():
    sink = MemoryAuditSink()
    msg = ToolMessage(content="ok", tool_call_id="c1")
    out = AuditMiddleware(sink).wrap_tool_call(_request("request_human_review"), lambda r: msg)
    assert out is msg
    await asyncio.sleep(0)  # inside a running loop the record is scheduled as a task
    assert [e.tool for e in sink.events] == ["request_human_review"]


def test_sync_wrap_tool_call_records_without_loop():
    sink = MemoryAuditSink()
    AuditMiddleware(sink).wrap_tool_call(_request("request_human_review"), lambda r: "ok")
    assert len(sink.events) == 1


def test_missing_runtime_info_falls_back():
    sink = MemoryAuditSink()
    req = SimpleNamespace(
        tool_call={"name": "publish_pull_request", "args": {}, "id": "c"},
        runtime=SimpleNamespace(context=None, config={}),
    )
    AuditMiddleware(sink).wrap_tool_call(req, lambda r: "ok")
    assert sink.events[0].thread_id == "unknown"
    assert sink.events[0].user_id == "unknown"


def test_redact_secrets():
    out = redact_secrets(
        {
            "a": [GH, "github_pat_" + "B" * 30],
            "b": "sk-" + "c" * 30,
            "h": "Authorization: Basic abc123==",
        }
    )
    s = str(out)
    assert GH not in s
    assert "github_pat_B" not in s
    assert "sk-ccc" not in s
    assert "abc123" not in s


class _FakeCollection:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(doc)


async def test_mongo_sink_inserts_dict():
    coll = _FakeCollection()
    sink = MongoAuditSink(collection=coll)
    ev = AuditEvent(datetime.now(UTC), "t", "u", "publish_pull_request", {"a": 1}, "ok", "r")
    await sink.record(ev)
    assert coll.docs[0]["tool"] == "publish_pull_request"
    assert coll.docs[0]["args"] == {"a": 1}


def test_mongo_sink_is_lazy(monkeypatch):
    import shipwright.middleware.audit as audit

    created = []

    class FakeClient:
        def __init__(self, uri):
            created.append(uri)

        def __getitem__(self, name):
            return {"audit_log": _FakeCollection()} if name == "db1" else None

    monkeypatch.setattr(audit, "AsyncMongoClient", FakeClient)
    sink = MongoAuditSink(uri="mongodb://x", db="db1")
    assert created == []
    assert sink.collection is not None
    assert created == ["mongodb://x"]


async def test_graph_interrupt_is_not_recorded_then_ok_once():
    from langgraph.errors import GraphInterrupt

    sink = MemoryAuditSink()
    mw = AuditMiddleware(sink)

    async def pausing(req):
        raise GraphInterrupt()

    with pytest.raises(GraphInterrupt):
        await mw.awrap_tool_call(_request("request_human_review"), pausing)
    assert sink.events == []

    with pytest.raises(GraphInterrupt):
        mw.wrap_tool_call(
            _request("request_human_review"), lambda r: (_ for _ in ()).throw(GraphInterrupt())
        )
    assert sink.events == []

    async def done(req):
        return ToolMessage(content="approved", tool_call_id="c1")

    await mw.awrap_tool_call(_request("request_human_review"), done)
    assert [e.outcome for e in sink.events] == ["ok"]
