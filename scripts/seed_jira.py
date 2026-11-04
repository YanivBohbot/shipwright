"""Create the demo tickets in the Jira project (live). Idempotent via demo/seeded.json."""

import asyncio
from pathlib import Path

from shipwright.config import get_settings
from shipwright.demo_tickets import load_demo_tickets, seed_demo_tickets
from shipwright.integrations.jira import McpJiraGateway
from shipwright.integrations.mcp_client import load_mcp_tools

ROOT = Path(__file__).resolve().parent.parent
SEEDED_PATH = ROOT / "demo" / "seeded.json"


async def main() -> None:
    settings = get_settings()
    tools = await load_mcp_tools(settings)
    jira = McpJiraGateway(tools["atlassian"])
    mapping = await seed_demo_tickets(jira, settings.jira_project, load_demo_tickets(), SEEDED_PATH)
    for scenario, key in mapping.items():
        print(f"scenario {scenario}: {key}")


if __name__ == "__main__":
    asyncio.run(main())
