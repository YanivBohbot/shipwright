"""Seed the organisation policies into the runtime store on first use.

A deployment (LangSmith) gives the graph its own platform store, which `seed_policies.py`
(MongoDB) never touches. This middleware makes sure `/policies/POLICIES.md` exists there.

Verified against langchain 1.4.9 / langgraph: `abefore_agent(state, runtime)` receives the
`Runtime`, whose `.store` is the store passed to / injected into the graph (None if none).
Custom middleware is placed BEFORE deepagents' `MemoryMiddleware`, which reads the policies.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware

from shipwright.config import Settings
from shipwright.policies import seed_policies_if_absent


class PolicySeedMiddleware(AgentMiddleware):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        store = getattr(runtime, "store", None)
        if store is not None:
            await seed_policies_if_absent(store, self._settings)
        return None
