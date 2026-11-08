"""Smoke test of every subagent with the REAL model and the REAL MCP servers, read-only.

Run: `uv run python scripts/smoke_subagents.py [name ...]`. For each subagent, the orchestrator
is told to delegate exactly one read-only task to it; the script records which tools the
subagent called and checks the expected ones are among them. No Jira or GitHub write is
expected: if one is attempted, the run pauses for approval and the case FAILS (nothing is sent).
Checkpoints and memory stay in memory (no MongoDB writes); the audit sink is a no-op.
"""

import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from shipwright.agent.orchestrator import build_shipwright_agent
from shipwright.agent.state import ShipwrightContext
from shipwright.core.config import get_settings
from shipwright.integrations.mcp import load_mcp_tools
from shipwright.sandbox.workspace import clone_repo, open_sandbox


class NoAudit:
    async def record(self, event: Any) -> None:
        return None


@dataclass
class Case:
    subagent: str
    task: str
    expected: set[str]


def cases(repo: str, project: str) -> list[Case]:
    return [
        Case(
            "researcher",
            "Find the internal spec EXPORT-CSV-v2 and list the CSV columns it requires. Also "
            f"search Jira (project = {project}) for tickets that mention CSV and list their keys.",
            {"knowledge_search", "jira_search"},
        ),
        Case(
            "code_analyst",
            "Where is task creation validated? Use search_code for 'assignee' in "
            f"repo:{repo}, and list the recent commits. Do not propose code.",
            {"search_code", "list_commits"},
        ),
        Case(
            "developer",
            "Do NOT modify any file. Only run the full test suite once and report the summary "
            "line.",
            {"execute"},
        ),
        Case(
            "reviewer",
            "Review the current workspace diff against these criteria: AC1 'nothing changed'. "
            "Use show_workspace_diff.",
            {"show_workspace_diff"},
        ),
        Case(
            "github_publisher",
            "Do NOT publish anything and do NOT create any branch. Only call get_changed_files "
            "and list_branches, then report the branch names.",
            {"get_changed_files", "list_branches"},
        ),
        Case(
            "jira_agent",
            f"Do NOT write anything. Only read {project}-4 with jira_get_issue and list its "
            "transitions with jira_get_transitions, then report them.",
            {"jira_get_issue", "jira_get_transitions"},
        ),
        Case(
            "general-purpose",
            "List the files of /workspace/repo/app and say which one defines the Pydantic models.",
            {"ls"},
        ),
    ]


async def run_case(agent: Any, case: Case, n: int) -> tuple[bool, set[str], str]:
    config: RunnableConfig = {"configurable": {"thread_id": f"smoke-{n}-{case.subagent}"}}
    prompt = (
        "SMOKE TEST, not a ticket. Ignore your ticket workflow. Call the `task` tool exactly "
        f"once with subagent_type='{case.subagent}' and this instruction:\n\n{case.task}\n\n"
        "Then answer with the subagent's report verbatim. Do nothing else."
    )
    used: set[str] = set()
    delegated = False
    async for namespace, _mode, chunk in agent.astream(
        {"messages": [{"role": "user", "content": prompt}]},
        config,
        context=ShipwrightContext(user_id="smoke"),
        stream_mode=["updates"],
        subgraphs=True,
    ):
        for update in chunk.values() if isinstance(chunk, dict) else []:
            messages = update.get("messages", []) if isinstance(update, dict) else []
            for msg in messages if isinstance(messages, list) else []:
                if not isinstance(msg, AIMessage):
                    continue
                for call in msg.tool_calls:
                    if not namespace and call["name"] == "task":
                        delegated |= call["args"].get("subagent_type") == case.subagent
                    elif namespace:
                        used.add(call["name"])
    state = await agent.aget_state(config)
    if state.interrupts:
        names = [a["name"] for i in state.interrupts for a in i.value["action_requests"]]
        return False, used, f"attempted a WRITE (paused, nothing sent): {names}"
    answer = str(state.values["messages"][-1].content)[:160].replace("\n", " ")
    ok = delegated and case.expected <= used
    why = "" if delegated else "orchestrator did not delegate to it; "
    return ok, used, why + answer


async def main(only: list[str]) -> int:
    load_dotenv()  # the model providers and LangSmith read os.environ (as in main.py)
    settings = get_settings()
    mcp = await load_mcp_tools(settings, NoAudit())
    sandbox = await open_sandbox(settings, "smoke")
    await clone_repo(sandbox, settings.shipwright_repo)
    agent = build_shipwright_agent(
        settings, mcp=mcp, sandbox=sandbox, checkpointer=InMemorySaver(), store=InMemoryStore()
    )
    selected = [c for c in cases(settings.shipwright_repo, settings.jira_project)]
    selected = [c for c in selected if not only or c.subagent in only]
    failures = 0
    for n, case in enumerate(selected, start=1):
        ok, used, detail = await run_case(agent, case, n)
        failures += not ok
        missing = case.expected - used
        print(f"{'PASS' if ok else 'FAIL'}  {case.subagent:<17} tools={sorted(used)}")
        if missing:
            print(f"      missing expected tools: {sorted(missing)}")
        print(f"      {detail}")
    print(f"\n{len(selected) - failures}/{len(selected)} subagents OK")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
