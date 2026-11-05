"""Seed the organisation policies into MongoDB (run once per org / on policy change).

Usage: uv run python scripts/seed_policies.py
"""

from __future__ import annotations

import asyncio

from langgraph.store.mongodb import MongoDBStore
from pymongo import MongoClient

from shipwright.config import get_settings
from shipwright.persistence import STORE_COLLECTION
from shipwright.policies import seed_policies


async def main() -> None:
    settings = get_settings()
    client = MongoClient(settings.mongodb_uri.get_secret_value())
    try:
        # same database/collection as shipwright.persistence.mongo_persistence (CLI runs)
        store = MongoDBStore(client[settings.mongodb_db][STORE_COLLECTION])
        await seed_policies(store, settings)
    finally:
        client.close()
    print(f"Seeded policies for org {settings.org_id!r}")


if __name__ == "__main__":
    asyncio.run(main())
