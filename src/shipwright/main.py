"""Shipwright CLI: `shipwright run SHIP-1 [--user alice]`, human-in-the-loop in the terminal.

Reading order: this file -> `agent/orchestrator.py` (the `create_deep_agent` call) -> each
component it imports. A run: open persistence -> build the dependencies of the mode (local demo
or live services) -> build the agent -> stream it, printing which subagent / tool is running ->
at every pause, ask the human and resume with `Command(resume=...)` -> repeat until it ends.
"""

import argparse
import asyncio
import getpass
import re
import sys
import uuid
from collections.abc import Sequence
from typing import Any

from dotenv import load_dotenv
from langchain.agents.middleware import PIIDetectionError
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from shipwright.integrations.mcp import McpConnectionError

PREVIEW = 160
_THREAD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")  # = the workspace thread-id rule


def _short(text: Any) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= PREVIEW else flat[: PREVIEW - 3] + "..."


class Progress:
    """Prints stream events readably: `[orchestrator]`, `[developer]`... then the tool call.

    Subagents run inside the orchestrator's `tools` node; the `tasks` stream tells which tool
    calls a `tools` task id runs, and a subagent's events carry `tools:<task id>` in their
    namespace, so each line can be labelled with the subagent's name.
    """

    def __init__(self) -> None:
        self.labels: dict[str, str] = {}
        self.printed: set[str] = set()  # tool call ids (HITL re-emits a paused AI message)

    def label(self, namespace: tuple[str, ...]) -> str:
        return self.labels.get(namespace[0], "subagent") if namespace else "orchestrator"

    def on_task(self, namespace: tuple[str, ...], task: dict[str, Any]) -> None:
        """The orchestrator's `tools` task gets the list of tool calls it executes."""
        calls = task.get("input")
        if namespace or task.get("name") != "tools" or not isinstance(calls, list):
            return
        subagents = [
            str((c.get("args") or {}).get("subagent_type", "subagent"))
            for c in calls
            if isinstance(c, dict) and c.get("name") == "task"
        ]
        if subagents:  # parallel delegations share one task: "researcher+code_analyst"
            self.labels[f"tools:{task['id']}"] = "+".join(subagents)

    def on_update(self, namespace: tuple[str, ...], chunk: dict[str, Any]) -> None:
        who = f"[{self.label(namespace)}]"
        for update in chunk.values():
            if not isinstance(update, dict):
                continue
            todos = update.get("todos")
            if isinstance(todos, list) and todos:
                print(f"{who} plan:")
                for todo in todos:
                    print(f"    [{todo.get('status', '?')}] {todo.get('content', '')}")
            messages = update.get("messages")
            for msg in messages if isinstance(messages, list) else []:
                self._message(who, namespace, msg)

    def _message(self, who: str, namespace: tuple[str, ...], msg: Any) -> None:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls:
                if call.get("id") in self.printed:
                    continue
                self.printed.add(call.get("id") or "")
                args = call.get("args") or {}
                if call["name"] == "task":
                    sub = args.get("subagent_type", "?")
                    print(f"{who} -> delegates to {sub}: {_short(args.get('description', ''))}")
                else:
                    print(f"{who} -> {call['name']}({_short(args)})")
            if not msg.tool_calls and msg.text and not namespace:
                print(f"\n{msg.text}")
        elif isinstance(msg, ToolMessage):
            print(f"{who}    {msg.name or 'tool'}: {_short(msg.content)}")


async def run_ticket(ticket: str, user: str, thread: str | None) -> None:
    from shipwright.agent.dependencies import build_dependencies
    from shipwright.agent.hitl import build_resume
    from shipwright.agent.orchestrator import build_shipwright_agent
    from shipwright.agent.state import ShipwrightContext
    from shipwright.core.config import get_settings
    from shipwright.core.persistence import open_persistence
    from shipwright.domain.tickets import parse_ticket_key

    settings = get_settings()
    try:
        key = parse_ticket_key(ticket, settings.jira_project)
    except ValueError as e:
        raise CliUsageError(str(e)) from e
    thread = thread or f"{key.lower()}-{uuid.uuid4().hex[:8]}"
    print(f"Shipwright | {key} | mode {settings.mode} | thread {thread} | user {user}")
    async with open_persistence(settings) as (checkpointer, store):
        deps = await build_dependencies(
            settings, thread_id=thread, checkpointer=checkpointer, store=store
        )
        agent = build_shipwright_agent(deps)
        config = {"configurable": {"thread_id": thread}}
        context = ShipwrightContext(user_id=user)
        state = await agent.aget_state(config)
        if state.interrupts:  # live mode only: a paused run of this thread in MongoDB
            print("Resuming a paused run.")
            run_input: Any = await asyncio.to_thread(build_resume, state.interrupts)
        else:
            run_input = {"messages": [{"role": "user", "content": f"Process Jira ticket {key}."}]}
        progress = Progress()
        stream = {"context": context, "stream_mode": ["updates", "tasks"], "subgraphs": True}
        while True:
            async for namespace, mode, chunk in agent.astream(run_input, config, **stream):
                if mode == "tasks" and "input" in chunk:
                    progress.on_task(namespace, chunk)
                elif mode == "updates":
                    progress.on_update(namespace, chunk)
            state = await agent.aget_state(config)
            if not state.interrupts:
                break
            # input() blocks: run it in a worker thread so the event loop keeps running
            run_input = await asyncio.to_thread(build_resume, state.interrupts)
    if settings.mode == "local":
        print(f"\nLocal PRs: {settings.local_prs_dir}\nLocal Jira:\n{deps.jira.summary_text()}")
    print(f"\nDone (thread {thread}).")


class CliUsageError(Exception):
    """Invalid command-line input (reported without a stack trace)."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    try:
        default_user = getpass.getuser()
    except Exception:
        default_user = "local-user"
    parser = argparse.ArgumentParser(prog="shipwright", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Process one Jira ticket into a reviewed pull request.")
    run.add_argument("ticket", help='Ticket key, e.g. "SHIP-1".')
    run.add_argument("--user", default=default_user, help="User id (per-user memory).")
    run.add_argument("--thread", help="Thread id; reuse it to resume a paused run (live mode).")
    return parser.parse_args(argv)


def _err(message: str) -> None:
    print(f"shipwright: {message}", file=sys.stderr)


def _config_error(e: ValidationError) -> str:
    if any("openai_api_key" in map(str, err["loc"]) for err in e.errors()):
        return "OPENAI_API_KEY is missing: set it in .env (the only key local mode needs)."
    details = "; ".join(str(err.get("msg", "")) for err in e.errors())
    return f"invalid configuration (.env): {details}"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.thread is not None and not _THREAD_RE.match(args.thread):
        _err(f"invalid --thread {args.thread!r} (letters, digits, '-' and '_' only)")
        return 2
    load_dotenv()  # model providers and LangSmith tracing read os.environ
    try:
        asyncio.run(run_ticket(args.ticket, args.user, args.thread))
    except PIIDetectionError as e:
        _err(f"run blocked by the PII guard: {e}. Nothing containing it was sent.")
    except McpConnectionError as e:
        _err(f"MCP connection failed: {e}")
    except ValidationError as e:
        _err(_config_error(e))
    except CliUsageError as e:
        _err(str(e))
        return 2
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
    else:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
