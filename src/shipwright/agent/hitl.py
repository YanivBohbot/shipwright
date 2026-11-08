"""Deep Agents concept: **human-in-the-loop** with `interrupt_on`.

Every MCP write tool pauses BEFORE it runs (`HumanInTheLoopMiddleware`, added by
`create_deep_agent`). The paused run carries one interrupt whose value is
`{"action_requests": [{"name", "args", ...}], "review_configs": [...]}`; the run resumes with
`Command(resume={"decisions": [...]})`, one decision per action, in order:
`{"type": "approve"}`, `{"type": "edit", "edited_action": {"name", "args"}}` or
`{"type": "reject", "message": "..."}` (the message goes back to the agent).

The human sees the exact arguments, so approving `push_files` approves the exact files pushed.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any

from langgraph.types import Command

from shipwright.integrations.mcp import WRITE_TOOLS

INTERRUPT_ON: dict[str, dict[str, list[str]]] = {
    name: {"allowed_decisions": ["approve", "edit", "reject"]} for name in WRITE_TOOLS
}


def ask_decisions(interrupt_value: dict[str, Any], ask: Callable[[str], str] = input) -> dict:
    """Show each pending action in the terminal and return the resume value."""
    decisions = []
    for action in interrupt_value["action_requests"]:
        args = json.dumps(action["args"], indent=2, ensure_ascii=False)
        print(f"\n----- Approval needed: {action['name']} -----\n{args}")
        while True:
            choice = ask("approve / edit / reject? ").strip().lower()
            if choice == "approve":
                decisions.append({"type": "approve"})
                break
            if choice == "reject":
                reason = ask("Reason (sent to the agent): ").strip()
                decisions.append({"type": "reject", "message": reason or "Rejected by the human."})
                break
            if choice == "edit":
                try:
                    new_args = json.loads(ask("New arguments (JSON object): "))
                except ValueError as e:
                    print(f"  Invalid JSON: {e}")
                    continue
                decisions.append(
                    {"type": "edit", "edited_action": {"name": action["name"], "args": new_args}}
                )
                break
    return {"decisions": decisions}


def build_resume(interrupts: Sequence[Any], ask: Callable[[str], str] = input) -> Command:
    """One `Command(resume=...)` answering every pending interrupt (keyed by id if several)."""
    answers = {i.id: ask_decisions(i.value, ask) for i in interrupts}
    return Command(resume=next(iter(answers.values())) if len(answers) == 1 else answers)
