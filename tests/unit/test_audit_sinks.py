"""v2 `shipwright.audit` (sinks + redaction); the v1 middleware is tested in test_audit.py."""

import json
from datetime import UTC, datetime

import pytest

from shipwright.audit import (
    AuditEvent,
    JsonlAuditSink,
    MongoAuditSink,
    build_audit_sink,
    redact_secrets,
)
from tests.fakes import make_settings


def _event(**kw) -> AuditEvent:
    values = dict(
        ts=datetime(2026, 11, 5, tzinfo=UTC),
        thread_id="t1",
        user_id="alice",
        tool="publish_pull_request",
        args={"title": "x"},
        outcome="ok",
        result_preview="done",
    )
    return AuditEvent(**{**values, **kw})


async def test_jsonl_sink_appends_one_line_per_event(tmp_path):
    sink = JsonlAuditSink(tmp_path / "logs" / "audit.jsonl")
    await sink.record(_event())
    await sink.record(_event(outcome="error", args={"note": "café"}))
    lines = (tmp_path / "logs" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    first, second = (json.loads(line) for line in lines)
    assert first["ts"] == "2026-11-05T00:00:00+00:00" and first["tool"] == "publish_pull_request"
    assert second["outcome"] == "error" and second["args"] == {"note": "café"}


async def test_mongo_sink_inserts_into_the_given_collection():
    class Coll:
        docs: list = []

        async def insert_one(self, doc):
            self.docs.append(doc)

    coll = Coll()
    await MongoAuditSink(collection=coll).record(_event())
    assert coll.docs[0]["user_id"] == "alice"


def test_build_audit_sink_per_mode(tmp_path):
    local = build_audit_sink(make_settings(tmp_dir=tmp_path))
    assert isinstance(local, JsonlAuditSink) and local.path == tmp_path / "audit.jsonl"
    assert isinstance(build_audit_sink(make_settings(mode="live")), MongoAuditSink)


@pytest.mark.parametrize(
    "secret",
    ["ghp_" + "a" * 36, "github_pat_" + "b" * 30, "sk-" + "c" * 30],
)
def test_redact_secrets_masks_tokens_recursively(secret):
    redacted = redact_secrets({"a": [f"token {secret} end"], "h": "Authorization: Bearer xyz"})
    assert secret not in json.dumps(redacted) and "xyz" not in json.dumps(redacted)
    assert "[REDACTED]" in redacted["a"][0]
