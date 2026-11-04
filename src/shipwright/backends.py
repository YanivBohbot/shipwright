"""CompositeBackend: sandbox by default, per-user memory and org policies in the store."""

from __future__ import annotations

from typing import Any

from deepagents.backends import CompositeBackend, StoreBackend

from shipwright.config import Settings
from shipwright.workspace import Workspace

MEMORIES_ROUTE = "/memories/"
POLICIES_ROUTE = "/policies/"


def user_namespace(rt: Any) -> tuple[str, ...]:
    """Namespace of the per-user memory. Refuses to fall back to a shared namespace."""
    context = getattr(rt, "context", None)
    user_id = getattr(context, "user_id", None)
    if not user_id:
        msg = (
            "Per-user memory requires a run context with a non-empty `user_id` "
            "(pass context=ShipwrightContext(user_id=...) when invoking the agent)."
        )
        raise RuntimeError(msg)
    return ("users", user_id)


def build_backend(workspace: Workspace, settings: Settings) -> CompositeBackend:
    org_id = settings.org_id
    return CompositeBackend(
        default=workspace.backend,
        routes={
            MEMORIES_ROUTE: StoreBackend(namespace=user_namespace),
            POLICIES_ROUTE: StoreBackend(namespace=lambda rt: ("org", org_id)),
        },
    )
