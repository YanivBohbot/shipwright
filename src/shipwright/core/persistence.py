"""Checkpointer (threads, needed for human-in-the-loop) + store (long-term memory) in MongoDB.

`MongoDBSaver` / `MongoDBStore` create their indexes in `__init__` (blocking network I/O), so
they are built in a worker thread.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.mongodb import MongoDBStore
from pymongo import MongoClient

from shipwright.core.config import Settings

STORE_COLLECTION = "store"


@asynccontextmanager
async def open_persistence(settings: Settings) -> AsyncIterator[tuple[Any, Any]]:
    """Yield `(checkpointer, store)`; close the Mongo client afterwards."""
    client: MongoClient = MongoClient(settings.mongodb_uri.get_secret_value())
    try:
        db = settings.mongodb_db
        checkpointer = await asyncio.to_thread(MongoDBSaver, client, db_name=db)
        store = await asyncio.to_thread(MongoDBStore, client[db][STORE_COLLECTION])
        yield checkpointer, store
    finally:
        client.close()
