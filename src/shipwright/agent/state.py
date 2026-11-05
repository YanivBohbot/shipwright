"""Deep Agents concept: **context schema** (per-run, read-only) and **state schema** (custom keys).

- `ShipwrightContext` is passed at invoke time (`context=ShipwrightContext(user_id="alice")`);
  tools and backends read it from the runtime (per-user `/memories/` namespace, audit).
- `ShipwrightState` extends `DeepAgentState` with what the human approved at the review gate.
"""

from dataclasses import dataclass
from typing import Annotated, NotRequired

from deepagents.graph import DeepAgentState
from langchain.agents.middleware.types import OmitFromInput


@dataclass
class ShipwrightContext:
    """Per-run context: who is running the agent."""

    user_id: str


class ShipwrightState(DeepAgentState):
    """Deep Agents state plus the review-gate approval.

    `approved_diff_hash` / `approved_ticket_key` are written ONLY by `request_human_review`
    (and cleared by `publish_pull_request`). They are `OmitFromInput`: `create_agent` drops
    them from the graph's input schema, so run input cannot seed them and skip the review gate.
    Tool `Command` updates still write them.
    """

    approved_diff_hash: NotRequired[Annotated[str | None, OmitFromInput]]
    approved_ticket_key: NotRequired[Annotated[str | None, OmitFromInput]]
