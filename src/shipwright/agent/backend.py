"""Deep Agents concept: **CompositeBackend** (one filesystem, several stores) + **permissions**.

| path          | backend                                  | lifetime                          |
|---------------|------------------------------------------|-----------------------------------|
| `/` (default) | the thread's sandbox (`Workspace.backend`)| the thread (repo clone, `execute`)|
| `/memories/`  | `StoreBackend`, namespace `("users", id)`| persistent, per user              |
| `/policies/`  | `StoreBackend`, namespace `("org", id)`  | persistent, shared, read-only     |
| `/skills/`    | `FilesystemBackend` over `agent/skills/` | packaged with the code, read-only |

`PERMISSIONS` make `/policies/` and `/skills/` read-only for the orchestrator;
`SUBAGENT_PERMISSIONS` additionally keep subagents out of the user's memory.
"""

from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend
from deepagents.backends.utils import create_file_data
from langgraph.store.base import BaseStore

from shipwright.core.config import Settings
from shipwright.sandbox.workspace import Workspace

MEMORIES_ROUTE = "/memories/"
POLICIES_ROUTE = "/policies/"
SKILLS_ROUTE = "/skills/"
SKILLS_DIR = Path(__file__).parent / "skills"
POLICIES_FILE = Path(__file__).parent / "policies" / "POLICIES.md"
# CompositeBackend strips the route prefix: StoreBackend sees "/POLICIES.md".
POLICIES_STORE_KEY = "/POLICIES.md"

_READ_ONLY = [
    FilesystemPermission(operations=["write"], paths=["/policies/**"], mode="deny"),
    FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny"),
]
PERMISSIONS: list[FilesystemPermission] = list(_READ_ONLY)
SUBAGENT_PERMISSIONS: list[FilesystemPermission] = [
    *_READ_ONLY,
    FilesystemPermission(operations=["write"], paths=["/memories/**"], mode="deny"),
]


def user_namespace(rt: Any) -> tuple[str, ...]:
    """Store namespace of the per-user memory; refuses to fall back to a shared namespace."""
    user_id = getattr(getattr(rt, "context", None), "user_id", None)
    if not user_id:
        raise RuntimeError(
            "Per-user memory requires context=ShipwrightContext(user_id=...) at invoke time."
        )
    return ("users", user_id)


def build_backend(workspace: Workspace, settings: Settings) -> CompositeBackend:
    org_id = settings.org_id
    return CompositeBackend(
        default=workspace.backend,
        routes={
            MEMORIES_ROUTE: StoreBackend(namespace=user_namespace),
            POLICIES_ROUTE: StoreBackend(namespace=lambda rt: ("org", org_id)),
            SKILLS_ROUTE: FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True),
        },
    )


async def seed_policies_if_absent(store: BaseStore, settings: Settings) -> bool:
    """Write POLICIES.md under `("org", org_id)` unless present (never overwrites)."""
    namespace = ("org", settings.org_id)
    if await store.aget(namespace, POLICIES_STORE_KEY) is not None:
        return False
    data = create_file_data(POLICIES_FILE.read_text(encoding="utf-8"))
    value = {k: data[k] for k in ("content", "encoding", "created_at", "modified_at")}
    await store.aput(namespace, POLICIES_STORE_KEY, value)
    return True
