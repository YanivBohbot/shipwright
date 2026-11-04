"""Demo tickets (one per scenario) and idempotent seeding into Jira."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

from shipwright.integrations.jira import JiraGateway

DEFAULT = Path(__file__).resolve().parents[2] / "demo" / "tickets.yaml"

Expected = Literal["pr", "questions", "subtasks", "draft_or_stop"]
_EXPECTED_VALUES = frozenset(get_args(Expected))
_FIELDS = ("scenario", "summary", "description", "issue_type", "expected")


@dataclass(frozen=True)
class DemoTicket:
    scenario: int
    summary: str
    description: str
    issue_type: str
    expected: Expected


def _parse(entry: Any, index: int) -> DemoTicket:
    if not isinstance(entry, dict):
        raise ValueError(f"Demo ticket #{index} must be a mapping")
    missing = [f for f in _FIELDS if f not in entry]
    if missing:
        raise ValueError(f"Demo ticket #{index} is missing field(s): {', '.join(missing)}")
    if not isinstance(entry["scenario"], int) or isinstance(entry["scenario"], bool):
        raise ValueError(f"Demo ticket #{index}: 'scenario' must be an integer")
    for f in ("summary", "description", "issue_type"):
        if not isinstance(entry[f], str) or not entry[f].strip():
            raise ValueError(f"Demo ticket #{index}: '{f}' must be a non-empty string")
    if entry["expected"] not in _EXPECTED_VALUES:
        raise ValueError(
            f"Demo ticket #{index}: unknown expected value {entry['expected']!r}; "
            f"expected one of {sorted(_EXPECTED_VALUES)}"
        )
    return DemoTicket(
        scenario=entry["scenario"],
        summary=entry["summary"].strip(),
        description=entry["description"].strip("\n"),
        issue_type=entry["issue_type"].strip(),
        expected=entry["expected"],
    )


def load_demo_tickets(path: Path = DEFAULT) -> list[DemoTicket]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of demo tickets")
    tickets = [_parse(entry, i) for i, entry in enumerate(data, start=1)]
    seen: set[int] = set()
    for t in tickets:
        if t.scenario in seen:
            raise ValueError(f"Duplicate scenario {t.scenario} in {path}")
        seen.add(t.scenario)
    return tickets


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
        raw = json.loads(seeded_path.read_text(encoding="utf-8"))
        mapping = {int(k): str(v) for k, v in raw.items()}
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
