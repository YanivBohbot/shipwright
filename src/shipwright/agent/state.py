"""Deep Agents concept: **context schema**, per-run read-only data passed at invoke time.

`agent.astream(..., context=ShipwrightContext(user_id="alice"))`; the `/memories/` backend
reads it to pick the user's namespace, the audit middleware to record who ran the tool.
"""

from dataclasses import dataclass


@dataclass
class ShipwrightContext:
    user_id: str
