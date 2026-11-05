"""Per-run context and custom graph state."""

from dataclasses import dataclass
from typing import Annotated, NotRequired

from deepagents.graph import DeepAgentState
from langchain.agents.middleware.types import OmitFromInput


@dataclass
class ShipwrightContext:
    """Per-run context (passed as `context=` at invoke time)."""

    user_id: str


class ShipwrightState(DeepAgentState):
    """Deep Agents state plus what the human approved at the review gate.

    `approved_diff_hash` / `approved_ticket_key` are written ONLY by `request_human_review`
    (and cleared by `publish_pull_request` after publishing). They are `OmitFromInput`:
    `create_agent` (langchain 1.4.9 `_resolve_schema`) drops them from the graph's input
    schema, so run input cannot seed them and bypass the human review gate. Tool `Command`
    updates still write them (they stay in the full state schema).
    """

    approved_diff_hash: NotRequired[Annotated[str | None, OmitFromInput]]
    approved_ticket_key: NotRequired[Annotated[str | None, OmitFromInput]]
