"""CompositeBackend routes, permissions and the policy seed (offline).

The route tests install a Runtime through a private langgraph key (no graph here); the real
graph is covered by `test_orchestrator.py`.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from deepagents.middleware.filesystem import _check_fs_permission
from langchain_core.runnables.config import var_child_runnable_config
from langgraph._internal._constants import CONF, CONFIG_KEY_RUNTIME
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore

from shipwright.agent.backend import (
    PERMISSIONS,
    POLICIES_FILE,
    POLICIES_STORE_KEY,
    SUBAGENT_PERMISSIONS,
    build_backend,
    seed_policies_if_absent,
)
from shipwright.agent.middleware import PolicySeedMiddleware
from shipwright.agent.state import ShipwrightContext, ShipwrightState
from shipwright.sandbox.workspace import Workspace
from tests.fakes import FakeSandbox, make_settings


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
    ws = Workspace(FakeSandbox(), "acme/taskboard-api", "https://example.invalid/x.git")
    backend = build_backend(ws, make_settings(**settings_kw))
    for prefix, route in backend.routes.items():
        if prefix != "/skills/":
            route._store = (
                store  # create_deep_agent(store=...) injects it in real runs; no graph here
            )
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
    settings = make_settings(org_id="acme")
    backend = _backend(store, org_id="acme")
    assert await seed_policies_if_absent(store, settings) is True
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
    assert not hasattr(ShipwrightContext(user_id="u"), "jira_project")  # comes from Settings


def test_skills_route_reads_the_packaged_skills():
    store = InMemoryStore()
    with run_as(store, ShipwrightContext(user_id="alice")):
        text = _backend(store).read("/skills/pr-template/SKILL.md").file_data["content"]
    assert "pass | changes_requested" in text


def test_permissions_make_policies_and_skills_read_only():
    for perms in (PERMISSIONS, SUBAGENT_PERMISSIONS):
        assert _check_fs_permission(perms, "write", "/policies/POLICIES.md") == "deny"
        assert _check_fs_permission(perms, "write", "/skills/pr-template/SKILL.md") == "deny"
        assert _check_fs_permission(perms, "read", "/policies/POLICIES.md") == "allow"
        assert _check_fs_permission(perms, "write", "/workspace/repo/a.py") == "allow"
    assert _check_fs_permission(PERMISSIONS, "write", "/memories/notes.md") == "allow"
    assert _check_fs_permission(SUBAGENT_PERMISSIONS, "write", "/memories/notes.md") == "deny"
    assert _check_fs_permission(SUBAGENT_PERMISSIONS, "read", "/memories/notes.md") == "allow"


NS = ("org", "acme")


def _seed_mw():
    return PolicySeedMiddleware(make_settings(org_id="acme"))


async def test_policy_seed_middleware_seeds_when_absent():
    store = InMemoryStore()
    assert await _seed_mw().abefore_agent({}, SimpleNamespace(store=store)) is None
    content = (await store.aget(NS, POLICIES_STORE_KEY)).value["content"]
    text = "\n".join(content) if isinstance(content, list) else content
    assert text.strip() == POLICIES_FILE.read_text(encoding="utf-8").strip()


async def test_policy_seed_never_overwrites_and_tolerates_no_store():
    store = InMemoryStore()
    custom = {"content": "custom", "encoding": "utf-8", "created_at": "x", "modified_at": "x"}
    await store.aput(NS, POLICIES_STORE_KEY, custom)
    await _seed_mw().abefore_agent({}, SimpleNamespace(store=store))
    assert (await store.aget(NS, POLICIES_STORE_KEY)).value == custom
    assert await _seed_mw().abefore_agent({}, SimpleNamespace(store=None)) is None
