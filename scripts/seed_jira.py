"""Create the demo tickets (demo/tickets.yaml) in the live Jira project, idempotently.

Run: `uv run python scripts/seed_jira.py` (live mode settings; demo/seeded.json keeps the keys).
Local mode needs no seeding: LocalJira reads demo/tickets.yaml directly.
"""

import asyncio
import json
from pathlib import Path

from shipwright.integrations.jira.gateway import JiraGateway
from shipwright.integrations.jira.local_jira import DemoTicket, load_demo_tickets

ROOT = Path(__file__).resolve().parent.parent
TICKETS_PATH = ROOT / "demo" / "tickets.yaml"
SEEDED_PATH = ROOT / "demo" / "seeded.json"


async def seed_demo_tickets(
    jira: JiraGateway, project: str, tickets: list[DemoTicket], seeded_path: Path
) -> dict[int, str]:
    """Create the missing demo tickets; return the full `scenario -> key` mapping.

    Scenarios already in `seeded_path` are not recreated; the file is rewritten after each
    creation so a mid-run failure never loses created keys.
    """
    seeded_path = Path(seeded_path)
    mapping: dict[int, str] = {}
    if seeded_path.exists():
        try:
            raw = json.loads(seeded_path.read_text(encoding="utf-8"))
            mapping = {int(k): str(v) for k, v in raw.items()}
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"Corrupt seed file {seeded_path}: expected a JSON object mapping "
                "scenario numbers to Jira keys"
            ) from exc
    for t in sorted(tickets, key=lambda t: t.scenario):
        if t.scenario in mapping:
            continue
        mapping[t.scenario] = await jira.create_issue(
            project, t.summary, t.description, t.issue_type
        )
        seeded_path.parent.mkdir(parents=True, exist_ok=True)
        seeded_path.write_text(
            json.dumps({str(k): v for k, v in sorted(mapping.items())}, indent=2) + "\n",
            encoding="utf-8",
        )
    return dict(sorted(mapping.items()))


async def main() -> None:
    from shipwright.core.config import get_settings
    from shipwright.integrations.jira.mcp_jira import McpJira
    from shipwright.integrations.mcp import load_mcp_tools

    settings = get_settings()
    jira = McpJira((await load_mcp_tools(settings))["atlassian"])
    tickets = load_demo_tickets(TICKETS_PATH)
    mapping = await seed_demo_tickets(jira, settings.jira_project, tickets, SEEDED_PATH)
    for scenario, key in mapping.items():
        print(f"scenario {scenario}: {key}")


if __name__ == "__main__":
    asyncio.run(main())
