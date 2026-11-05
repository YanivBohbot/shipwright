"""Terminal CLI: `shipwright run <TICKET> [--user USER] [--thread THREAD]` (local mode).

Runs the orchestrator with Mongo persistence, streams progress (todos, active subagent, tool
calls) and answers every pause in the terminal:

- review gate (`request_human_review`): shows diff / tests / reviewer report and asks
  approve / feedback / reject. Approve echoes the payload's `diff_hash` (the approval is bound
  to the diff the human saw).
- `interrupt_on` (HumanInTheLoopMiddleware): one decision per `action_requests` entry, among
  that action's `allowed_decisions` -> `Command(resume={"decisions": [...]})`.

Re-running with the same `--thread` resumes a paused run.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import re
import sys
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from dotenv import load_dotenv
from langchain.agents.middleware import PIIDetectionError
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from pydantic import ValidationError

from shipwright.integrations.mcp_client import McpConnectionError

Ask = Callable[[str], str]

REVIEW_CHOICES = ("approve", "feedback", "reject")
_THREAD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")  # = workspace thread-id rule
PREVIEW = 160


class CliUsageError(Exception):
    """Invalid command-line input (reported without a stack trace)."""


# ------------------------------------------------------------------ resume payloads (pure)


def review_gate_resume(payload: dict[str, Any], choice: str, message: str = "") -> dict[str, Any]:
    """Resume value for a `review_gate` interrupt. Raises ValueError on invalid input."""
    choice = choice.strip().lower()
    message = (message or "").strip()
    if choice == "approve":
        diff_hash = payload.get("diff_hash")
        if not diff_hash:
            raise ValueError("The review gate payload has no diff_hash: cannot approve.")
        return {"type": "approve", "diff_hash": diff_hash}
    if choice == "feedback":
        if not message:
            raise ValueError("Feedback needs a message for the developer.")
        return {"type": "feedback", "message": message}
    if choice == "reject":
        return {"type": "reject", "message": message}
    raise ValueError(f"Unknown decision {choice!r}: expected one of {', '.join(REVIEW_CHOICES)}.")


def _allowed(payload: dict[str, Any], index: int) -> list[str]:
    name = payload["action_requests"][index]["name"]
    configs = payload.get("review_configs") or []
    config = configs[index] if index < len(configs) else None
    if config is None or config.get("action_name") != name:
        config = next((c for c in configs if c.get("action_name") == name), None)
    return list((config or {}).get("allowed_decisions") or ["approve", "reject"])


def hitl_resume(
    payload: dict[str, Any],
    choices: Sequence[str],
    *,
    messages: Sequence[str | None] | None = None,
    edited_args: Sequence[dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Resume value for a HumanInTheLoopMiddleware interrupt: `{"decisions": [...]}`.

    One decision per action request, in order; each must be allowed for its action.
    Raises ValueError otherwise.
    """
    actions = payload["action_requests"]
    if len(choices) != len(actions):
        raise ValueError(f"Expected one decision per action ({len(actions)}), got {len(choices)}.")
    decisions: list[dict[str, Any]] = []
    for i, (action, raw) in enumerate(zip(actions, choices, strict=True)):
        choice = raw.strip().lower()
        allowed = _allowed(payload, i)
        if choice not in allowed:
            raise ValueError(
                f"Decision {choice!r} is not allowed for {action['name']} "
                f"(allowed: {', '.join(allowed)})."
            )
        if choice == "approve":
            decisions.append({"type": "approve"})
        elif choice == "reject":
            message = ((messages or [None] * len(actions))[i] or "").strip()
            decisions.append(
                {"type": "reject", "message": message} if message else {"type": "reject"}
            )
        elif choice == "edit":
            args = (edited_args or [None] * len(actions))[i]
            if not isinstance(args, dict):
                raise ValueError(f"Edit of {action['name']} needs the new arguments as an object.")
            decisions.append(
                {"type": "edit", "edited_action": {"name": action["name"], "args": args}}
            )
        else:
            raise ValueError(f"Unsupported decision {choice!r}.")
    return {"decisions": decisions}


# ------------------------------------------------------------------ interactive prompts


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
        if choice == "feedback":
            message = ask("Feedback for the developer: ")
        elif choice == "reject":
            message = ask("Reason (optional): ")
        try:
            return review_gate_resume(payload, choice, message)
        except ValueError as e:
            print(f"  {e}")


def ask_hitl(payload: dict[str, Any], ask: Ask = input) -> dict[str, Any]:
    choices: list[str] = []
    messages: list[str | None] = []
    edited: list[dict[str, Any] | None] = []
    for i, action in enumerate(payload["action_requests"]):
        allowed = _allowed(payload, i)
        _section(
            f"Approval needed: {action['name']}",
            json.dumps(action.get("args", {}), indent=2, ensure_ascii=False, default=str),
        )
        while True:
            choice = ask(f"Decision for {action['name']} [{'/'.join(allowed)}]: ").strip().lower()
            if choice not in allowed:
                print(f"  Choose one of: {', '.join(allowed)}")
                continue
            message, args = None, None
            if choice == "reject":
                message = ask("Reason (optional): ")
            elif choice == "edit":
                try:
                    args = json.loads(ask("New arguments as JSON: "))
                except ValueError as e:
                    print(f"  Invalid JSON: {e}")
                    continue
                if not isinstance(args, dict):
                    print("  The arguments must be a JSON object.")
                    continue
            break
        choices.append(choice)
        messages.append(message)
        edited.append(args)
    return hitl_resume(payload, choices, messages=messages, edited_args=edited)


def build_resume_command(interrupts: Sequence[Any], ask: Ask = input) -> Command:
    """Ask the human about every pending interrupt and build the resume Command."""
    answers: dict[str, Any] = {}
    for intr in interrupts:
        value = intr.value
        if isinstance(value, dict) and value.get("kind") == "review_gate":
            answers[intr.id] = ask_review_gate(value, ask)
        elif isinstance(value, dict) and "action_requests" in value:
            answers[intr.id] = ask_hitl(value, ask)
        else:
            raise RuntimeError(f"Unsupported interrupt payload: {value!r}")
    if len(answers) == 1:
        return Command(resume=next(iter(answers.values())))
    return Command(resume=answers)


# ------------------------------------------------------------------ streaming display


def _short(text: Any) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= PREVIEW else flat[: PREVIEW - 3] + "..."


def print_update(namespace: tuple[str, ...], chunk: dict[str, Any]) -> None:
    prefix = "  [subagent] " if namespace else ""
    for node, update in chunk.items():
        if node == "__interrupt__" or not isinstance(update, dict):
            continue
        todos = update.get("todos")
        if isinstance(todos, list) and todos:
            print(f"{prefix}Todos:")
            for todo in todos:
                print(f"{prefix}  [{todo.get('status', '?')}] {todo.get('content', '')}")
        messages = update.get("messages")
        for msg in messages if isinstance(messages, list) else []:
            if isinstance(msg, AIMessage):
                for call in msg.tool_calls:
                    if call["name"] == "task":
                        args = call.get("args", {})
                        print(
                            f"{prefix}-> subagent {args.get('subagent_type', '?')}: "
                            f"{_short(args.get('description', ''))}"
                        )
                    else:
                        print(f"{prefix}-> {call['name']}({_short(call.get('args', {}))})")
                if not msg.tool_calls and msg.content and not namespace:
                    print(f"\n{msg.text}")
            elif isinstance(msg, ToolMessage):
                print(f"{prefix}   {msg.name or 'tool'}: {_short(msg.content)}")


# ------------------------------------------------------------------ run


def default_thread(key: str) -> str:
    return f"{key.lower()}-{uuid.uuid4().hex[:8]}"


_KEY_ARGS = ("ticket_key", "key", "parent_key")


def paused_ticket_keys(interrupts: Sequence[Any]) -> set[str]:
    """Ticket keys the pending interrupts are about (review gate payload or tool arguments)."""
    keys: set[str] = set()
    for intr in interrupts:
        value = intr.value if isinstance(intr.value, dict) else {}
        sources = [value, *(a.get("args") or {} for a in value.get("action_requests") or [])]
        for source in sources:
            for name in _KEY_ARGS:
                if isinstance(source.get(name), str) and source[name].strip():
                    keys.add(source[name].strip().upper())
    return keys


def resume_mismatch_warning(key: str, interrupts: Sequence[Any]) -> str | None:
    """Warning when `--thread` resumes a run paused on another ticket than the one given."""
    paused = paused_ticket_keys(interrupts)
    if not paused or key.upper() in paused:
        return None
    return (
        f"WARNING: this thread is paused on {', '.join(sorted(paused))}, not {key}. "
        "Your decision applies to the paused run."
    )


async def ask_resume(interrupts: Sequence[Any]) -> Command:
    """`build_resume_command` in a worker thread, so `input()` never blocks the event loop."""
    return await asyncio.to_thread(build_resume_command, interrupts)


async def run_ticket(args: argparse.Namespace) -> None:
    from shipwright.agent import build_agent
    from shipwright.config import get_settings
    from shipwright.context import ShipwrightContext
    from shipwright.graph import build_live_dependencies
    from shipwright.persistence import mongo_persistence
    from shipwright.policies import POLICIES_STORE_KEY, seed_policies
    from shipwright.tickets import parse_ticket_key
    from shipwright.workspace import open_workspace

    settings = get_settings()
    try:
        key = parse_ticket_key(args.ticket, settings.jira_project)
    except ValueError as e:
        raise CliUsageError(str(e)) from e
    thread = args.thread or default_thread(key)
    async with mongo_persistence(settings) as (checkpointer, store):
        if await store.aget(("org", settings.org_id), POLICIES_STORE_KEY) is None:
            await seed_policies(store, settings)  # first run: policies were never seeded
        workspace = await open_workspace(settings, thread_id=thread, repo=settings.shipwright_repo)
        await workspace.ensure_cloned()
        deps = await build_live_dependencies(settings)
        agent = build_agent(settings, deps, workspace, checkpointer=checkpointer, store=store)
        config = {"configurable": {"thread_id": thread}}
        context = ShipwrightContext(user_id=args.user, jira_project=settings.jira_project)
        print(f"Shipwright: {key} | thread {thread} | user {args.user}")

        state = await agent.aget_state(config)
        if state.interrupts:
            print("Resuming a paused run.")
            if (warning := resume_mismatch_warning(key, state.interrupts)) is not None:
                print(warning, file=sys.stderr)
            run_input: Any = await ask_resume(state.interrupts)
        else:
            run_input = {"messages": [{"role": "user", "content": f"Process Jira ticket {key}."}]}
        while True:
            async for namespace, chunk in agent.astream(
                run_input, config, context=context, stream_mode="updates", subgraphs=True
            ):
                print_update(namespace, chunk)
            state = await agent.aget_state(config)
            if not state.interrupts:
                break
            run_input = await ask_resume(state.interrupts)
        print(f"\nDone. Resume or inspect later with --thread {thread}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    try:
        default_user = getpass.getuser()
    except Exception:
        default_user = "local-user"
    parser = argparse.ArgumentParser(prog="shipwright", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Process one Jira ticket into a pull request.")
    run.add_argument("ticket", help='Jira ticket key, e.g. "SHIP-1".')
    run.add_argument("--user", default=default_user, help="User id (per-user memory).")
    run.add_argument("--thread", default=None, help="Thread id (reuse it to resume a run).")
    return parser.parse_args(argv)


def _err(message: str) -> None:
    print(f"shipwright: {message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.thread is not None and not _THREAD_RE.match(args.thread):
        _err(f"invalid --thread {args.thread!r} (letters, digits, '-' and '_' only)")
        return 2
    load_dotenv()  # model providers and LangSmith tracing read os.environ
    try:
        asyncio.run(run_ticket(args))
    except PIIDetectionError as e:
        _err(f"run blocked by the PII guard: {e}. Nothing containing it was sent.")
        return 1
    except McpConnectionError as e:
        _err(f"MCP connection failed: {e}")
        return 1
    except ValidationError as e:
        fields = ", ".join(".".join(map(str, err["loc"])) for err in e.errors())
        _err(f"invalid or missing configuration (.env): {fields}")
        return 1
    except CliUsageError as e:
        _err(str(e))
        return 2
    except KeyboardInterrupt:
        _err("interrupted; rerun with the same --thread to resume")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
