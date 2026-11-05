"""Per-run context and custom graph state."""

from dataclasses import dataclass
from typing import NotRequired

from deepagents.graph import DeepAgentState


@dataclass
class ShipwrightContext:
    """Per-run context (passed as `context=` at invoke time)."""

    user_id: str
    jira_project: str = "SHIP"


class ShipwrightState(DeepAgentState):
    """Deep Agents state plus what the human approved at the review gate.

    `approved_diff_hash` / `approved_ticket_key` are written ONLY by `request_human_review`
    (and cleared by `publish_pull_request` after publishing). API clients must never seed
    `approved_*` keys in run input: that would bypass the human review gate.
    """

    approved_diff_hash: NotRequired[str | None]
    approved_ticket_key: NotRequired[str | None]
