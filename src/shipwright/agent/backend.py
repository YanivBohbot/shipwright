"""Deep Agents concept: **CompositeBackend** (one filesystem, several stores) + **permissions**.

| path          | backend                                     | lifetime                         |
|---------------|---------------------------------------------|----------------------------------|
| `/` (default) | the thread's LangSmith sandbox              | the thread (repo clone, `execute`)|
| `/memories/`  | `StoreBackend`, namespace `("users", id)`   | persistent, per user (MongoDB)   |
| `/policies/`  | `FilesystemBackend` over `agent/policies/`  | packaged with the code           |
| `/skills/`    | `FilesystemBackend` over `agent/skills/`    | packaged with the code           |

`PERMISSIONS` make `/policies/` and `/skills/` read-only; `SUBAGENT_PERMISSIONS` also keep the
subagents out of the user's memory (only the orchestrator records preferences).
"""

from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend
from deepagents.backends.protocol import BackendProtocol

AGENT_DIR = Path(__file__).parent
SKILLS_ROUTE = "/skills/"
MEMORY_FILES = ["/memories/preferences.md", "/policies/POLICIES.md"]

# Permissions can only target the routed stores, not the sandbox itself (deepagents refuses
# permissions on a backend with `execute`). Keeping the orchestrator out of the code is done by
# `ReadOnlyToolsMiddleware(writes_only_under="/memories/")` in `middleware.py`.
PERMISSIONS = [
    FilesystemPermission(operations=["write"], paths=["/policies/**", "/skills/**"], mode="deny"),
]
SUBAGENT_PERMISSIONS = [
    FilesystemPermission(
        operations=["write"], paths=["/policies/**", "/skills/**", "/memories/**"], mode="deny"
    ),
]


def user_namespace(rt: Any) -> tuple[str, ...]:
    """Per-user memory namespace; refuses to fall back to a shared one."""
    user_id = getattr(getattr(rt, "context", None), "user_id", None)
    if not user_id:
        raise RuntimeError("Per-user memory needs context=ShipwrightContext(user_id=...).")
    return ("users", user_id)


def build_backend(sandbox: BackendProtocol) -> CompositeBackend:
    return CompositeBackend(
        default=sandbox,
        routes={
            "/memories/": StoreBackend(namespace=user_namespace),
            "/policies/": FilesystemBackend(root_dir=AGENT_DIR / "policies", virtual_mode=True),
            SKILLS_ROUTE: FilesystemBackend(root_dir=AGENT_DIR / "skills", virtual_mode=True),
        },
    )
