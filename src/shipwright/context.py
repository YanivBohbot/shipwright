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
    """Deep Agents state plus the hash of the diff approved at the review gate."""

    approved_diff_hash: NotRequired[str | None]
