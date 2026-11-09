"""Eval runner: the real agent on the demo tickets, stopped at its first write (nothing sent).

    uv run python -m evals.run_evals            # scenarios 1-7, local table
    uv run python -m evals.run_evals 1 4 5      # some scenarios
    uv run python -m evals.run_evals --langsmith   # also upload as a LangSmith experiment

Each scenario runs the real model with the real MCP servers (reads only) and a fresh sandbox.
Every Jira/GitHub write pauses for a human (`interrupt_on`); the runner never resumes, so no
write is ever sent. The first write the agent attempts gives the outcome (`create_branch`... =
`pr`, `jira_add_comment` = `questions`, `jira_create_issue` = `subtasks`, none = `stopped`).

What is recorded per run (the outputs `evals/evaluators.py` reads):
- `subagents`: the orchestrator's `task` delegations, in order;
- `subagent_tools`: the tools each subagent called (parallel delegations share one `tools`
  task, so their tools are recorded for each of them);
- `tests_passed`: the developer's last `STATUS:` line; `pr_body` / `draft`: what the
  orchestrator handed to `github_publisher`.
Checkpoints are in memory (no MongoDB thread is left behind).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from typing import Any, cast

from langchain_core.messages import AIMessage, ToolMessage

PR_WRITES = {"create_branch", "push_files", "create_pull_request"}
OUTCOME_BY_WRITE = {"jira_add_comment": "questions", "jira_create_issue": "subtasks"}
_DRAFT = re.compile(r"`?draft`?\s*[:=]?\s*`?(true|false)`?", re.IGNORECASE)


class Trajectory:
    """Collects, from the stream, who was delegated to and which tools each subagent called."""

    def __init__(self) -> None:
        self.subagents: list[str] = []
        self.labels: dict[str, list[str]] = {}  # "tools:<task id>" -> subagent names
        self.tools: dict[str, list[str]] = {}
        self.descriptions: dict[str, list[str]] = {}
        self.reports: dict[str, list[str]] = {}
        self._task_ids: dict[str, str] = {}  # orchestrator `task` tool_call_id -> subagent
        self._seen: set[str] = set()
        self.final = ""  # the orchestrator's last text answer

    def feed(self, namespace: tuple[str, ...], mode: str, chunk: dict[str, Any]) -> None:
        if mode == "tasks" and not namespace and chunk.get("name") == "tools":
            calls = chunk.get("input")
            names = [
                str((c.get("args") or {}).get("subagent_type", "?"))
                for c in (calls if isinstance(calls, list) else [])
                if isinstance(c, dict) and c.get("name") == "task"
            ]
            if names:
                self.labels[f"tools:{chunk['id']}"] = names
        elif mode == "updates":
            for update in chunk.values():
                messages = update.get("messages") if isinstance(update, dict) else None
                for msg in messages if isinstance(messages, list) else []:
                    self._message(namespace, msg)

    def _message(self, namespace: tuple[str, ...], msg: Any) -> None:
        if isinstance(msg, AIMessage):
            if not namespace and not msg.tool_calls and msg.text:
                self.final = msg.text
            for call in msg.tool_calls:
                call_id = call.get("id") or ""
                if call_id in self._seen:  # a paused AI message is re-emitted on resume
                    continue
                self._seen.add(call_id)
                args = call.get("args") or {}
                if not namespace and call["name"] == "task":
                    sub = str(args.get("subagent_type", "?"))
                    self.subagents.append(sub)
                    self._task_ids[call_id] = sub
                    self.descriptions.setdefault(sub, []).append(str(args.get("description", "")))
                elif namespace:
                    for sub in self.labels.get(namespace[0], ["?"]):
                        self.tools.setdefault(sub, []).append(call["name"])
        elif isinstance(msg, ToolMessage) and not namespace and msg.name == "task":
            sub = self._task_ids.get(msg.tool_call_id, "?")
            self.reports.setdefault(sub, []).append(str(msg.content))


def _first_write(interrupts: Any) -> str | None:
    for intr in interrupts or []:
        for action in (getattr(intr, "value", None) or {}).get("action_requests", []):
            return str(action.get("name"))
    return None


def outputs_from(t: Trajectory, interrupts: Any) -> dict[str, Any]:
    """The run outputs the evaluators read."""
    first = _first_write(interrupts)
    if first is None:
        outcome = "stopped"
    elif first in PR_WRITES:
        outcome = "pr"
    else:
        outcome = OUTCOME_BY_WRITE.get(first, first)
    dev = t.reports.get("developer") or []
    tests_passed = None
    if dev:
        tests_passed = "STATUS: TESTS_PASSING" in dev[-1]
    publish = (t.descriptions.get("github_publisher") or [None])[-1]
    draft = None
    if publish:
        found = _DRAFT.findall(publish)
        draft = found[-1].lower() == "true" if found else None
    return {
        "outcome": outcome,
        "first_write": first,
        "subagents": list(t.subagents),
        "subagent_tools": {k: list(v) for k, v in t.tools.items()},
        "tests_passed": tests_passed,
        "pr_body": publish,
        "draft": draft,
        "final": t.final,
    }


async def run_example(ticket_key: str) -> dict[str, Any]:
    """Run the agent on one ticket until its first write (never sent) or its end."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from shipwright.agent.orchestrator import build_shipwright_agent
    from shipwright.agent.state import ShipwrightContext
    from shipwright.audit import build_audit_sink
    from shipwright.core.config import get_settings
    from shipwright.integrations.mcp import load_mcp_tools
    from shipwright.sandbox.workspace import clone_repo, open_sandbox

    settings = get_settings()
    thread = f"eval-{ticket_key.lower()}-{uuid.uuid4().hex[:6]}"
    mcp = await load_mcp_tools(settings, build_audit_sink(settings))
    sandbox = await open_sandbox(settings, thread)
    await clone_repo(sandbox, settings.shipwright_repo)
    agent = build_shipwright_agent(
        settings, mcp=mcp, sandbox=sandbox, checkpointer=InMemorySaver(), store=InMemoryStore()
    )
    config: Any = {"configurable": {"thread_id": thread}}
    trajectory = Trajectory()
    message = {"messages": [{"role": "user", "content": f"Process Jira ticket {ticket_key}."}]}
    async for item in agent.astream(
        message,
        config,
        context=ShipwrightContext(user_id="eval"),
        stream_mode=["updates", "tasks"],
        subgraphs=True,
    ):
        namespace, mode, chunk = cast(tuple[tuple[str, ...], str, dict[str, Any]], item)
        trajectory.feed(namespace, mode, chunk)
    state = await agent.aget_state(config)
    return {**outputs_from(trajectory, state.interrupts), "thread": thread}


def _evaluators() -> list[Any]:
    from evals.evaluators import (
        criteria_covered,
        no_pr_when_ambiguous,
        outcome_matches,
        subagent_tools,
        tests_green,
        trajectory_subagents,
    )

    return [
        outcome_matches,
        trajectory_subagents,
        subagent_tools,
        no_pr_when_ambiguous,
        tests_green,
        criteria_covered,
    ]


def _fmt(score: Any) -> str:
    return "-" if score is None else f"{score:.2f}".rstrip("0").rstrip(".")


async def run_local(scenarios: list[int]) -> int:
    from types import SimpleNamespace

    from evals.dataset import build_examples
    from shipwright.core.config import get_settings

    examples = {e["inputs"]["scenario"]: e for e in build_examples(get_settings().jira_project)}
    evaluators = _evaluators()
    keys = ["outcome", "trajectory_subagents", "subagent_tools", "criteria_covered"]
    rows = []
    for n in scenarios:
        ex = examples[n]
        key = ex["inputs"]["ticket_key"]
        print(f"--- scenario {n} ({key}): running...", flush=True)
        try:
            out = await run_example(key)
        except Exception as e:  # one broken scenario must not hide the others
            print(f"    ERROR: {e!r}")
            rows.append((n, key, None, {}))
            continue
        run = SimpleNamespace(outputs=out)
        example = SimpleNamespace(inputs=ex["inputs"], outputs=ex["outputs"])
        scores = {}
        for ev in evaluators:
            res = ev(run, example)
            scores[res["key"]] = res
            if res.get("comment") and res["score"] not in (None, 1, 1.0):
                print(f"    {res['key']}: {res['comment']}")
        print(f"    outcome={out['outcome']} first_write={out['first_write']}")
        if out["final"]:
            print("    final: " + " ".join(out["final"].split())[:300])
        print(f"    subagents={out['subagents']}")
        for sub, tools in out["subagent_tools"].items():
            print(f"      {sub}: {sorted(set(tools))}")
        rows.append((n, key, out, scores))

    print("\n| # | ticket | outcome | " + " | ".join(keys) + " |")
    print("|---|---|---|" + "---|" * len(keys))
    failed = 0
    for n, key, out, scores in rows:
        if out is None:
            print(f"| {n} | {key} | ERROR |" + " |" * len(keys))
            failed += 1
            continue
        vals = [_fmt(scores.get(k, {}).get("score")) for k in keys]
        failed += any(scores.get(k, {}).get("score") not in (None, 1, 1.0) for k in keys)
        print(f"| {n} | {key} | {out['outcome']} | " + " | ".join(vals) + " |")
    return 1 if failed else 0


def run_langsmith(scenarios: list[int]) -> int:
    import langsmith

    from evals.dataset import DATASET_NAME, upsert_dataset
    from shipwright.core.config import get_settings

    client = langsmith.Client()
    upsert_dataset(client, project=get_settings().jira_project)
    examples = [
        e
        for e in client.list_examples(dataset_name=DATASET_NAME)
        if (e.inputs or {}).get("scenario") in scenarios
    ]

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(run_example(inputs["ticket_key"]))

    langsmith.evaluate(
        target,
        data=examples,
        evaluators=_evaluators(),
        experiment_prefix="shipwright-first-write",
        max_concurrency=1,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("scenarios", nargs="*", type=int, default=list(range(1, 8)))
    parser.add_argument("--langsmith", action="store_true", help="upload as an experiment")
    args = parser.parse_args(argv)
    load_dotenv()
    if args.langsmith:
        return run_langsmith(args.scenarios)
    return asyncio.run(run_local(args.scenarios))


if __name__ == "__main__":
    sys.exit(main())
