"""MongoDB checkpointer + store for local runs (CLI). A deployment gets both from the platform.

Verified against langgraph-checkpoint-mongodb 0.5.1 / langgraph-store-mongodb 0.4.0: there is
no async saver class; `MongoDBSaver(client, db_name=...)` implements the async API by running
the sync driver in an executor, and `MongoDBStore(collection)` likewise (`abatch`). Both create
their indexes in `__init__` (blocking network I/O), so they are built in a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.mongodb import MongoDBStore
from pymongo import MongoClient

from shipwright.config import Settings

# Shared with scripts/seed_policies.py: policies must be seeded where the agent reads them.
STORE_COLLECTION = "store"


@asynccontextmanager
async def mongo_persistence(settings: Settings) -> AsyncIterator[tuple[MongoDBSaver, MongoDBStore]]:
    """Yield `(checkpointer, store)` on database `settings.mongodb_db`; close the client after."""
    client = MongoClient(settings.mongodb_uri.get_secret_value())
    try:
        db = settings.mongodb_db
        checkpointer = await asyncio.to_thread(MongoDBSaver, client, db_name=db)
        store = await asyncio.to_thread(MongoDBStore, client[db][STORE_COLLECTION])
        yield checkpointer, store
    finally:
        client.close()
