"""Checkpointer + store for a run: in memory (local mode) or MongoDB (live mode).

Verified against langgraph-checkpoint-mongodb 0.5.1 / langgraph-store-mongodb 0.4.0: there is
no async saver class; `MongoDBSaver(client, db_name=...)` implements the async API by running
the sync driver in an executor, and `MongoDBStore(collection)` likewise. Both create their
indexes in `__init__` (blocking network I/O), so they are built in a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from shipwright.core.config import Settings

# Shared with scripts/seed_policies.py: policies must be seeded where the agent reads them.
STORE_COLLECTION = "store"


@asynccontextmanager
async def open_persistence(settings: Settings) -> AsyncIterator[tuple[Any, Any]]:
    """Yield `(checkpointer, store)` for `settings.mode`; release the Mongo client after."""
    if settings.mode == "local":
        yield InMemorySaver(), InMemoryStore()
        return

    from langgraph.checkpoint.mongodb import MongoDBSaver
    from langgraph.store.mongodb import MongoDBStore
    from pymongo import MongoClient

    if settings.mongodb_uri is None:
        raise ValueError("MONGODB_URI is required in live mode")
    client: MongoClient = MongoClient(settings.mongodb_uri.get_secret_value())
    try:
        db = settings.mongodb_db
        checkpointer = await asyncio.to_thread(MongoDBSaver, client, db_name=db)
        store = await asyncio.to_thread(MongoDBStore, client[db][STORE_COLLECTION])
        yield checkpointer, store
    finally:
        client.close()
