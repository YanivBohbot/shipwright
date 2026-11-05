"""Live smoke run of one demo scenario against REAL Jira / GitHub / sandbox (needs a full .env).

Usage: uv run python scripts/smoke_live.py <scenario> [--thread THREAD] [--user USER]

1. reads the ticket key of the scenario in demo/seeded.json (scripts/seed_jira.py),
2. runs the terminal CLI flow (`shipwright run`): YOU decide at every interrupt,
3. prints the PR URL and checks via the GitHub / Jira gateways that the PR exists, the ticket
   has a comment with the PR URL and is `In Review`. Exit code 1 if a check fails.

Running it twice on the same scenario opens a NEW PR on `shipwright/<KEY>-r2` that says
"Supersedes <first PR>" (existing branches are never pushed to, ruling R6).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from shipwright.smoke import comment_bodies, key_for_scenario, verify_outcome

ROOT = Path(__file__).resolve().parent.parent
SEEDED_PATH = ROOT / "demo" / "seeded.json"
TOOL_GET_ISSUE = "jira_get_issue"


async def verify_live(key: str) -> bool:
    from shipwright.config import get_settings
    from shipwright.integrations.github import McpGitHubGateway
    from shipwright.integrations.jira import McpJiraGateway
    from shipwright.integrations.mcp_client import invoke_mcp_tool, load_mcp_tools

    settings = get_settings()
    tools = await load_mcp_tools(settings)
    jira = McpJiraGateway(tools["atlassian"])
    github = McpGitHubGateway(tools["github"])
    ticket = await jira.get_issue(key)
    # mcp-atlassian returns the latest comments inside jira_get_issue (comment_limit); the exact
    # result shape is to be confirmed on the first live run.
    issue = await invoke_mcp_tool(
        tools["atlassian"][TOOL_GET_ISSUE], {"issue_key": key, "comment_limit": 20}
    )
    report = await verify_outcome(github, settings.shipwright_repo, ticket, comment_bodies(issue))
    print(f"\nTicket {key}: status {report.status!r}")
    print(f"PR: {report.pr_url or 'NOT FOUND'} (branch {report.branch})")
    for problem in report.problems:
        print(f"  FAIL: {problem}")
    print("SMOKE OK" if report.ok else "SMOKE FAILED")
    return report.ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario", type=int, help="Demo scenario number (1-7).")
    parser.add_argument("--thread", default=None)
    parser.add_argument("--user", default=None)
    args = parser.parse_args(argv)
    load_dotenv()
    from shipwright import cli

    try:
        key = key_for_scenario(args.scenario, SEEDED_PATH)
    except ValueError as e:
        print(f"smoke_live: {e}", file=sys.stderr)
        return 2
    cli_args = ["run", key]
    if args.thread:
        cli_args += ["--thread", args.thread]
    if args.user:
        cli_args += ["--user", args.user]
    code = cli.main(cli_args)
    if code != 0:
        return code
    return 0 if asyncio.run(verify_live(key)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
