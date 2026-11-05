"""Mongo persistence wiring (no network: client and savers are replaced by recorders)."""

from __future__ import annotations

from typing import Any

import pytest

import shipwright.persistence as persistence
from tests.fakes import settings_stub


class _Client:
    instances: list[_Client] = []

    def __init__(self, uri: str, **kwargs: Any) -> None:
        self.uri = uri
        self.closed = False
        _Client.instances.append(self)

    def __getitem__(self, db: str) -> dict[str, tuple[str, str]]:
        return {persistence.STORE_COLLECTION: (db, persistence.STORE_COLLECTION)}

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    _Client.instances.clear()
    monkeypatch.setattr(persistence, "MongoClient", _Client)
    monkeypatch.setattr(
        persistence, "MongoDBSaver", lambda client, db_name: ("saver", client, db_name)
    )
    monkeypatch.setattr(persistence, "MongoDBStore", lambda collection: ("store", collection))


async def test_mongo_persistence_uses_settings_db_and_closes_client():
    settings = settings_stub(mongodb_db="shipdb")
    async with persistence.mongo_persistence(settings) as (checkpointer, store):
        (client,) = _Client.instances
        assert client.uri == "mongodb://fake:27017"
        assert checkpointer == ("saver", client, "shipdb")
        assert store == ("store", ("shipdb", "store"))
        assert not client.closed
    assert client.closed


async def test_mongo_persistence_closes_client_on_error():
    with pytest.raises(RuntimeError):
        async with persistence.mongo_persistence(settings_stub()):
            raise RuntimeError("boom")
    assert _Client.instances[0].closed
