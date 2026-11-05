"""Scripted human decisions per demo scenario (shared by integration tests and evals).

A *decider* maps an interrupt value to its resume value:

- review gate (`kind == "review_gate"`): `approve` (echoes `diff_hash`), `feedback` or `reject`.
- `interrupt_on` pause (`action_requests`): the same choice for every pending action.

`decider_for(scenario)` returns a FRESH stateful decider (gate decisions are consumed in order,
the last one repeats) so one decider must not be shared between runs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from shipwright.cli import hitl_resume, review_gate_resume

Decider = Callable[[dict[str, Any]], Any]
GateStep = tuple[str, str]  # (choice, message)

S6_FEEDBACK = "Use a dataclass for the health payload, not a dict"


def scripted_decider(gate: Sequence[GateStep], hitl: str = "approve") -> Decider:
    """Decider answering review gates from `gate` in order (the last step repeats) and every
    `interrupt_on` pause with the single choice `hitl`."""
    if not gate:
        raise ValueError("gate needs at least one (choice, message) step")
    steps = list(gate)
    gates_seen = 0

    def decide(value: dict[str, Any]) -> Any:
        nonlocal gates_seen
        if value.get("kind") == "review_gate":
            choice, message = steps[min(gates_seen, len(steps) - 1)]
            gates_seen += 1
            return review_gate_resume(value, choice, message)
        if isinstance(value.get("action_requests"), list):
            return hitl_resume(value, [hitl] * len(value["action_requests"]))
        raise ValueError(f"Unsupported interrupt payload: {value!r}")

    return decide


def approve_all() -> Decider:
    return scripted_decider([("approve", "")])


# scenario -> factory of a fresh decider
SCENARIO_DECISIONS: dict[int, Callable[[], Decider]] = {
    1: approve_all,
    2: approve_all,
    3: approve_all,
    # ambiguous: the agent must only comment on Jira (approved) and never reach the gate
    4: lambda: scripted_decider([("reject", "Not expected.")]),
    # failing tests: approve the gate anyway -> draft PR
    5: approve_all,
    # revision: feedback on the first gate, approval of the second
    6: lambda: scripted_decider([("feedback", S6_FEEDBACK), ("approve", "")]),
    # oversized: approve the sub-tasks; a gate would be unexpected
    7: lambda: scripted_decider([("reject", "Not expected.")]),
}


def decider_for(scenario: int) -> Decider:
    try:
        return SCENARIO_DECISIONS[scenario]()
    except KeyError:
        raise ValueError(f"No scripted decisions for scenario {scenario}") from None
