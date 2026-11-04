"""Composite backend tests: user-scoped memory, org-scoped seeded policies (offline)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from langchain_core.runnables.config import var_child_runnable_config
from langgraph._internal._constants import CONF, CONFIG_KEY_RUNTIME
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore

from shipwright.backends import build_backend
from shipwright.context import ShipwrightContext, ShipwrightState
from shipwright.policies import seed_policies
from shipwright.workspace import Workspace
from tests.fakes import FakeSandbox, settings_stub


@contextmanager
def run_as(store: InMemoryStore, context: ShipwrightContext | None):
    """Install a Runtime the way a graph run does (StoreBackend reads it via get_runtime())."""
    rt = Runtime(context=context, store=store)
    token = var_child_runnable_config.set({CONF: {CONFIG_KEY_RUNTIME: rt}})
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


def _backend(store: InMemoryStore, **settings_kw):
    ws = Workspace(FakeSandbox(), "acme/taskboard-api", skills_source=Path("does-not-exist"))
    backend = build_backend(ws, settings_stub(**settings_kw))
    for route in backend.routes.values():
        route._store = store  # create_deep_agent(store=...) injects it in real runs; no graph here
    return backend


def test_memories_route_is_user_scoped():
    store = InMemoryStore()
    backend = _backend(store)
    with run_as(store, ShipwrightContext(user_id="alice")):
        assert backend.write("/memories/preferences.md", "use tabs").error is None
        assert "use tabs" in backend.read("/memories/preferences.md").file_data["content"]
    with run_as(store, ShipwrightContext(user_id="bob")):
        assert backend.read("/memories/preferences.md").error is not None
    # Stored under ("users", "alice"), key without the route prefix.
    assert store.get(("users", "alice"), "/preferences.md") is not None
    assert store.get(("users", "bob"), "/preferences.md") is None


def test_memories_without_context_raises_instead_of_sharing():
    store = InMemoryStore()
    backend = _backend(store)
    with run_as(store, None), pytest.raises(RuntimeError, match="user_id"):
        backend.write("/memories/x.md", "secret-ish")
    assert store.search(("users",)) == []


async def test_policies_route_is_org_scoped_and_seeded():
    store = InMemoryStore()
    settings = settings_stub(org_id="acme")
    backend = _backend(store, org_id="acme")
    await seed_policies(store, settings)
    assert store.get(("org", "acme"), "/POLICIES.md") is not None
    with run_as(store, ShipwrightContext(user_id="alice")):
        result = backend.read("/policies/POLICIES.md")
    assert "Forbidden paths" in result.file_data["content"]
    assert "ADR-001" in result.file_data["content"]
    with run_as(store, ShipwrightContext(user_id="alice")):
        other = _backend(store, org_id="other-org").read("/policies/POLICIES.md")
    assert other.error is not None


def test_policies_store_key_matches_backend_write_convention():
    store = InMemoryStore()
    backend = _backend(store)
    with run_as(store, ShipwrightContext(user_id="alice")):
        backend.write("/policies/probe.md", "x")
    assert store.get(("org", "default-org"), "/probe.md") is not None


def test_state_extends_deep_agent_state():
    from deepagents.graph import DeepAgentState

    assert "approved_diff_hash" in ShipwrightState.__optional_keys__
    assert set(DeepAgentState.__annotations__) <= set(ShipwrightState.__annotations__)
    assert ShipwrightContext(user_id="u").jira_project == "SHIP"
