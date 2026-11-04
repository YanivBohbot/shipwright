"""Seed the read-only organisation policies into the store."""

from __future__ import annotations

from pathlib import Path

from deepagents.backends.utils import create_file_data
from langgraph.store.base import BaseStore

from shipwright.config import Settings

POLICIES_FILE = Path(__file__).parent / "policies" / "POLICIES.md"
# CompositeBackend strips the "/policies" route prefix, so StoreBackend sees "/POLICIES.md".
POLICIES_STORE_KEY = "/POLICIES.md"


async def seed_policies(store: BaseStore, settings: Settings) -> None:
    """Write POLICIES.md under ("org", org_id); readable as /policies/POLICIES.md."""
    content = POLICIES_FILE.read_text(encoding="utf-8")
    file_data = create_file_data(content)
    value = {k: file_data[k] for k in ("content", "encoding", "created_at", "modified_at")}
    await store.aput(("org", settings.org_id), POLICIES_STORE_KEY, value)
