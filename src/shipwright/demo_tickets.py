"""Demo tickets (one per scenario) and idempotent seeding into Jira."""

import json
from pathlib import Path

from shipwright.integrations.jira import JiraGateway
from shipwright.integrations.jira.local_jira import DemoTicket, Expected  # noqa: F401
from shipwright.integrations.jira.local_jira import load_demo_tickets as _load

DEFAULT = Path(__file__).resolve().parents[2] / "demo" / "tickets.yaml"


def load_demo_tickets(path: Path = DEFAULT) -> list[DemoTicket]:
    """v1 entry point (default path); the parser lives in `integrations.jira.local_jira`."""
    return _load(path)


async def seed_demo_tickets(
    jira: JiraGateway, project: str, tickets: list[DemoTicket], seeded_path: Path
) -> dict[int, str]:
    """Create the missing demo tickets in Jira; return the full ``scenario -> key`` mapping.

    Idempotent: scenarios already present in `seeded_path` are not recreated. The file is
    rewritten after each creation so a mid-run failure never loses created keys.
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
