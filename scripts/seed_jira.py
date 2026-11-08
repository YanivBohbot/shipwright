"""Create the demo tickets (demo/tickets.yaml) in the Jira project, idempotently.

Run: `uv run python scripts/seed_jira.py`. It calls the `jira_create_issue` MCP tool directly:
the same tool, authorization and audit as the agent, but no human approval (a setup script).
demo/seeded.json keeps the `scenario -> key` mapping, so a ticket is never created twice.
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from shipwright.domain.tickets import load_demo_tickets

ROOT = Path(__file__).resolve().parent.parent
TICKETS_PATH = ROOT / "demo" / "tickets.yaml"
SEEDED_PATH = ROOT / "demo" / "seeded.json"


def created_key(result: Any, project: str) -> str:
    """The new issue key found in the MCP tool's result (JSON text or content blocks)."""
    text = json.dumps(result) if not isinstance(result, str) else result
    match = re.search(rf"\b{re.escape(project)}-\d+\b", text)
    if not match:
        raise RuntimeError(f"jira_create_issue returned no {project} key: {text[:300]}")
    return match.group(0)


async def seed(create_issue: Any, project: str, tickets: list[dict], seeded: Path) -> dict:
    mapping: dict[int, str] = {}
    if seeded.exists():
        mapping = {int(k): v for k, v in json.loads(seeded.read_text(encoding="utf-8")).items()}
    for t in sorted(tickets, key=lambda t: t["scenario"]):
        if t["scenario"] in mapping:
            continue
        result = await create_issue.ainvoke(
            {
                "project_key": project,
                "summary": t["summary"],
                "issue_type": t["issue_type"],
                "description": t["description"],
            }
        )
        mapping[t["scenario"]] = created_key(result, project)
        seeded.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")  # after each
    return mapping


async def main() -> None:
    from shipwright.audit import build_audit_sink
    from shipwright.core.config import get_settings
    from shipwright.integrations.mcp import load_mcp_tools

    settings = get_settings()
    tools = await load_mcp_tools(settings, build_audit_sink(settings))
    tickets = load_demo_tickets(TICKETS_PATH)
    mapping = await seed(tools["jira_create_issue"], settings.jira_project, tickets, SEEDED_PATH)
    for scenario, key in sorted(mapping.items()):
        print(f"scenario {scenario}: {key}")


if __name__ == "__main__":
    asyncio.run(main())
