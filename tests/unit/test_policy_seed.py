"""PolicySeedMiddleware seeds POLICIES.md into the runtime store only when absent."""

from types import SimpleNamespace

from langgraph.store.memory import InMemoryStore

from shipwright.middleware.policy_seed import PolicySeedMiddleware
from shipwright.policies import POLICIES_FILE, POLICIES_STORE_KEY
from tests.fakes import settings_stub

NS = ("org", "acme")


def _mw():
    return PolicySeedMiddleware(settings_stub(org_id="acme"))


async def test_absent_policies_are_seeded():
    store = InMemoryStore()
    assert await _mw().abefore_agent({}, SimpleNamespace(store=store)) is None
    item = await store.aget(NS, POLICIES_STORE_KEY)
    assert item is not None
    content = item.value["content"]
    text = "\n".join(content) if isinstance(content, list) else content
    assert text.strip() == POLICIES_FILE.read_text(encoding="utf-8").strip()


async def test_present_policies_are_untouched():
    store = InMemoryStore()
    custom = {"content": "custom", "encoding": "utf-8", "created_at": "x", "modified_at": "x"}
    await store.aput(NS, POLICIES_STORE_KEY, custom)
    await _mw().abefore_agent({}, SimpleNamespace(store=store))
    assert (await store.aget(NS, POLICIES_STORE_KEY)).value == custom


async def test_no_store_is_a_noop():
    assert await _mw().abefore_agent({}, SimpleNamespace(store=None)) is None
