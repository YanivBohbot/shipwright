"""Deep Agents concept: **human-in-the-loop**, two flavours, both resumed by `Command(resume=)`.

1. `interrupt_on` (`INTERRUPT_ON`): deepagents' HumanInTheLoopMiddleware pauses BEFORE a write
   tool runs. Payload `{"action_requests": [...], "review_configs": [...]}`; resume value
   `{"decisions": [{"type": "approve"} | {"type": "reject", "message"} | {"type": "edit", ...}]}`.
2. `interrupt()` inside `request_human_review` (the review gate). Payload `{"kind":
   "review_gate", "diff", "diff_hash", ...}`; resume value `{"type": "approve", "diff_hash":
   <echoed from the payload>}` | `{"type": "feedback", "message"}` | `{"type": "reject", ...}`.

The pure `*_resume` builders validate a decision; the `ask_*` functions prompt in a terminal.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any

from langgraph.types import Command

Ask = Callable[[str], str]

INTERRUPT_ON: dict[str, dict[str, list[str]]] = {
    "publish_pull_request": {"allowed_decisions": ["approve", "reject"]},
    "update_jira_ticket": {"allowed_decisions": ["approve", "edit", "reject"]},
    "create_jira_subtasks": {"allowed_decisions": ["approve", "edit", "reject"]},
}
REVIEW_CHOICES = ("approve", "feedback", "reject")


def review_gate_resume(payload: dict[str, Any], choice: str, message: str = "") -> dict[str, Any]:
    """Resume value for the review gate. Approve echoes `diff_hash` (binds it to what was seen)."""
    choice, message = choice.strip().lower(), (message or "").strip()
    if choice == "approve":
        if not payload.get("diff_hash"):
            raise ValueError("The review gate payload has no diff_hash: cannot approve.")
        return {"type": "approve", "diff_hash": payload["diff_hash"]}
    if choice == "feedback":
        if not message:
            raise ValueError("Feedback needs a message for the developer.")
        return {"type": "feedback", "message": message}
    if choice == "reject":
        return {"type": "reject", "message": message}
    raise ValueError(f"Unknown decision {choice!r}: expected one of {', '.join(REVIEW_CHOICES)}.")


def allowed_decisions(payload: dict[str, Any], index: int) -> list[str]:
    name = payload["action_requests"][index]["name"]
    configs = payload.get("review_configs") or []
    config = next((c for c in configs if c.get("action_name") == name), None)
    return list((config or {}).get("allowed_decisions") or ["approve", "reject"])


def hitl_resume(
    payload: dict[str, Any],
    choices: Sequence[str],
    *,
    messages: Sequence[str | None] = (),
    edited_args: Sequence[dict[str, Any] | None] = (),
) -> dict[str, Any]:
    """`{"decisions": [...]}`: one allowed decision per action request, in order."""
    actions = payload["action_requests"]
    if len(choices) != len(actions):
        raise ValueError(f"Expected one decision per action ({len(actions)}), got {len(choices)}.")
    decisions: list[dict[str, Any]] = []
    for i, action in enumerate(actions):
        choice = choices[i].strip().lower()
        if choice not in allowed_decisions(payload, i):
            raise ValueError(f"Decision {choice!r} is not allowed for {action['name']}.")
        if choice == "approve":
            decisions.append({"type": "approve"})
        elif choice == "reject":
            message = (messages[i] if i < len(messages) else None) or ""
            decisions.append({"type": "reject", **({"message": message} if message else {})})
        else:  # edit
            args = edited_args[i] if i < len(edited_args) else None
            if not isinstance(args, dict):
                raise ValueError(f"Edit of {action['name']} needs the new arguments as an object.")
            decisions.append(
                {"type": "edit", "edited_action": {"name": action["name"], "args": args}}
            )
    return {"decisions": decisions}


def _section(title: str, body: str) -> None:
    print(f"\n----- {title} -----\n{body.rstrip()}")


def ask_review_gate(payload: dict[str, Any], ask: Ask = input) -> dict[str, Any]:
    _section(f"Review gate for {payload.get('ticket_key', '?')}", payload.get("summary", ""))
    _section("Diff", payload.get("diff", ""))
    _section("Tests", payload.get("test_report", ""))
    _section("Reviewer report", payload.get("review_report", ""))
    while True:
        choice = ask(f"\nDecision [{'/'.join(REVIEW_CHOICES)}]: ").strip().lower()
        message = ""
        if choice in ("feedback", "reject"):
            message = ask("Feedback for the developer: " if choice == "feedback" else "Reason: ")
        try:
            return review_gate_resume(payload, choice, message)
        except ValueError as e:
            print(f"  {e}")


def ask_hitl(payload: dict[str, Any], ask: Ask = input) -> dict[str, Any]:
    choices, messages, edited = [], [], []
    for i, action in enumerate(payload["action_requests"]):
        allowed = allowed_decisions(payload, i)
        args_text = json.dumps(action.get("args", {}), indent=2, ensure_ascii=False, default=str)
        _section(f"Approval needed: {action['name']}", args_text)
        while True:
            choice = ask(f"Decision for {action['name']} [{'/'.join(allowed)}]: ").strip().lower()
            message, args = None, None
            if choice == "reject":
                message = ask("Reason (optional): ")
            elif choice == "edit":
                try:
                    args = json.loads(ask("New arguments as a JSON object: "))
                except ValueError as e:
                    print(f"  Invalid JSON: {e}")
                    continue
            if choice in allowed and (choice != "edit" or isinstance(args, dict)):
                break
            print(f"  Choose one of: {', '.join(allowed)} (edit needs a JSON object)")
        choices.append(choice)
        messages.append(message)
        edited.append(args)
    return hitl_resume(payload, choices, messages=messages, edited_args=edited)


def build_resume(interrupts: Sequence[Any], ask: Ask | None = None) -> Command:
    """Ask the human about every pending interrupt; one `Command(resume=...)` for all of them."""
    ask = ask or input  # resolved at call time (tests patch `input`)
    answers: dict[str, Any] = {}
    for intr in interrupts:
        value = intr.value if isinstance(intr.value, dict) else {}
        if value.get("kind") == "review_gate":
            answers[intr.id] = ask_review_gate(value, ask)
        elif "action_requests" in value:
            answers[intr.id] = ask_hitl(value, ask)
        else:
            raise RuntimeError(f"Unsupported interrupt payload: {intr.value!r}")
    return Command(resume=next(iter(answers.values())) if len(answers) == 1 else answers)
