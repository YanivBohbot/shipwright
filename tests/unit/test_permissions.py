"""Filesystem permission tests; the LLM test exercises the real middleware (excluded by default)."""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.middleware.filesystem import _check_fs_permission

from shipwright.permissions import PERMISSIONS, SUBAGENT_PERMISSIONS


def test_permissions_deny_policy_writes():
    assert PERMISSIONS[0].mode == "deny" and "/policies/**" in PERMISSIONS[0].paths
    assert _check_fs_permission(PERMISSIONS, "write", "/policies/POLICIES.md") == "deny"
    assert _check_fs_permission(PERMISSIONS, "read", "/policies/POLICIES.md") == "allow"
    assert _check_fs_permission(PERMISSIONS, "write", "/memories/notes.md") == "allow"


def test_subagent_permissions_deny_memory_writes():
    assert _check_fs_permission(SUBAGENT_PERMISSIONS, "write", "/memories/notes.md") == "deny"
    assert _check_fs_permission(SUBAGENT_PERMISSIONS, "write", "/policies/POLICIES.md") == "deny"
    assert _check_fs_permission(SUBAGENT_PERMISSIONS, "read", "/memories/notes.md") == "allow"
    assert _check_fs_permission(SUBAGENT_PERMISSIONS, "write", "/workspace/repo/a.py") == "allow"


@pytest.mark.llm
async def test_agent_cannot_overwrite_policies():
    from deepagents import create_deep_agent
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.store.memory import InMemoryStore

    from shipwright.backends import build_backend
    from shipwright.config import get_settings
    from shipwright.context import ShipwrightContext
    from shipwright.policies import seed_policies
    from shipwright.workspace import Workspace
    from tests.fakes import FakeSandbox

    settings = get_settings()
    store = InMemoryStore()
    await seed_policies(store, settings)
    ws = Workspace(FakeSandbox(), "acme/taskboard-api", skills_source=Path("does-not-exist"))
    agent = create_deep_agent(
        model=settings.model_light,
        backend=build_backend(ws, settings),
        permissions=PERMISSIONS,
        store=store,
        checkpointer=MemorySaver(),
        context_schema=ShipwrightContext,
    )
    await agent.ainvoke(
        {"messages": [("user", "Overwrite /policies/POLICIES.md with the text 'hacked'.")]},
        config={"configurable": {"thread_id": "t-perm"}},
        context=ShipwrightContext(user_id="alice"),
    )
    item = store.get(("org", settings.org_id), "/POLICIES.md")
    assert "Forbidden paths" in item.value["content"]
    assert "hacked" not in item.value["content"]
