import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from shipwright.core import persistence
from shipwright.core.persistence import open_persistence
from tests.fakes import make_settings


async def test_local_mode_is_in_memory():
    async with open_persistence(make_settings()) as (checkpointer, store):
        assert isinstance(checkpointer, InMemorySaver)
        assert isinstance(store, InMemoryStore)
        await store.aput(("users", "alice"), "k", {"v": 1})
        assert (await store.aget(("users", "alice"), "k")).value == {"v": 1}


async def test_live_mode_uses_mongo_and_closes_client(monkeypatch):
    import langgraph.checkpoint.mongodb as cp_mod
    import langgraph.store.mongodb as store_mod
    import pymongo

    events: list[str] = []

    class FakeClient(dict):
        def __init__(self, uri):
            events.append(f"client:{uri}")

        def __getitem__(self, db):
            return {persistence.STORE_COLLECTION: f"{db}.store"}

        def close(self):
            events.append("closed")

    monkeypatch.setattr(pymongo, "MongoClient", FakeClient)
    monkeypatch.setattr(cp_mod, "MongoDBSaver", lambda client, db_name: f"saver:{db_name}")
    monkeypatch.setattr(store_mod, "MongoDBStore", lambda coll: f"store:{coll}")

    settings = make_settings(mode="live", mongodb_uri="mongodb://x:1", mongodb_db="sw")
    async with open_persistence(settings) as (checkpointer, store):
        assert (checkpointer, store) == ("saver:sw", "store:sw.store")
        assert events == ["client:mongodb://x:1"]
    assert events[-1] == "closed"


async def test_live_mode_closes_client_on_error(monkeypatch):
    import langgraph.checkpoint.mongodb as cp_mod
    import pymongo

    closed: list[bool] = []

    class FakeClient:
        def __init__(self, uri):
            pass

        def close(self):
            closed.append(True)

    def boom(*a, **k):
        raise RuntimeError("index creation failed")

    monkeypatch.setattr(pymongo, "MongoClient", FakeClient)
    monkeypatch.setattr(cp_mod, "MongoDBSaver", boom)
    with pytest.raises(RuntimeError, match="index creation"):
        async with open_persistence(make_settings(mode="live", mongodb_uri="mongodb://x:1")):
            pass
    assert closed == [True]
