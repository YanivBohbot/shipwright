"""`JiraGateway` for local mode: tickets from `demo/tickets.yaml`, writes to a JSON state file.

Ticket keys are `<project>-<scenario>` (SHIP-1 ... SHIP-5). Comments, statuses and created
issues/subtasks (SHIP-101, ...) are kept in `demo/.local_jira.json`; `summary_text()` renders
it for humans.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

from shipwright.domain.tickets import (
    Ticket,
    TicketNotFound,
    extract_acceptance_criteria,
    extract_spec_refs,
)

STATUSES = ("To Do", "In Progress", "In Review", "Done")
Expected = Literal["pr", "questions", "subtasks", "draft_or_stop"]
_FIELDS = ("scenario", "summary", "description", "issue_type", "expected")


@dataclass(frozen=True)
class DemoTicket:
    scenario: int
    summary: str
    description: str
    issue_type: str
    expected: Expected


def _parse(entry: Any, i: int) -> DemoTicket:
    if not isinstance(entry, dict):
        raise ValueError(f"Demo ticket #{i} must be a mapping")
    if missing := [f for f in _FIELDS if f not in entry]:
        raise ValueError(f"Demo ticket #{i} is missing field(s): {', '.join(missing)}")
    if not isinstance(entry["scenario"], int) or isinstance(entry["scenario"], bool):
        raise ValueError(f"Demo ticket #{i}: 'scenario' must be an integer")
    for f in ("summary", "description", "issue_type"):
        if not isinstance(entry[f], str) or not entry[f].strip():
            raise ValueError(f"Demo ticket #{i}: '{f}' must be a non-empty string")
    if entry["expected"] not in get_args(Expected):
        raise ValueError(
            f"Demo ticket #{i}: unknown expected value {entry['expected']!r}; "
            f"expected one of {sorted(get_args(Expected))}"
        )
    return DemoTicket(
        scenario=entry["scenario"],
        summary=entry["summary"].strip(),
        description=entry["description"].strip("\n"),
        issue_type=entry["issue_type"].strip(),
        expected=entry["expected"],
    )


def load_demo_tickets(path: Path) -> list[DemoTicket]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of demo tickets")
    tickets = [_parse(entry, i) for i, entry in enumerate(data, start=1)]
    scenarios = [t.scenario for t in tickets]
    if dup := next((s for s in scenarios if scenarios.count(s) > 1), None):
        raise ValueError(f"Duplicate scenario {dup} in {path}")
    return tickets


class LocalJira:
    def __init__(self, tickets_path: Path, state_path: Path, project: str = "SHIP") -> None:
        self.project = project.upper()
        self.state_path = Path(state_path)
        self._demo = {f"{self.project}-{t.scenario}": t for t in load_demo_tickets(tickets_path)}

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _save(self, state: dict[str, dict[str, Any]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)

    def _entry(self, state: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
        if key not in self._demo and key not in state:
            raise TicketNotFound(key)
        return state.setdefault(key, {"status": STATUSES[0], "comments": []})

    async def get_issue(self, key: str) -> Ticket:
        entry = self._entry(self._load(), key)
        demo = self._demo.get(key)
        description = demo.description if demo else entry.get("description", "")
        return Ticket(
            key=key,
            summary=demo.summary if demo else entry.get("summary", ""),
            description=description,
            status=entry["status"],
            acceptance_criteria=extract_acceptance_criteria(description),
            spec_refs=extract_spec_refs(description),
            issue_type=demo.issue_type if demo else entry.get("issue_type", ""),
        )

    async def add_comment(self, key: str, body: str) -> str:
        state = self._load()
        comments = self._entry(state, key)["comments"]
        comment_id = str(len(comments) + 1)
        comments.append({"id": comment_id, "at": datetime.now(UTC).isoformat(), "body": body})
        self._save(state)
        return comment_id

    async def transition(self, key: str, to_status: str) -> None:
        state = self._load()
        entry = self._entry(state, key)
        wanted = next((s for s in STATUSES if s.lower() == to_status.strip().lower()), None)
        if wanted is None:
            raise ValueError(
                f"No transition to status {to_status!r} for {key}. Available: {', '.join(STATUSES)}"
            )
        entry["status"] = wanted
        self._save(state)

    def _new_key(self, state: dict[str, dict[str, Any]]) -> str:
        numbers = [int(k.rsplit("-", 1)[1]) for k in [*self._demo, *state]]
        return f"{self.project}-{max([100, *numbers]) + 1}"

    async def create_issue(
        self, project: str, summary: str, description: str, issue_type: str
    ) -> str:
        if project.upper() != self.project:
            raise ValueError(f"LocalJira serves project {self.project}, not {project!r}")
        state = self._load()
        key = self._new_key(state)
        state[key] = {
            "summary": summary,
            "description": description,
            "issue_type": issue_type,
            "status": STATUSES[0],
            "comments": [],
        }
        self._save(state)
        return key

    async def create_subtask(self, parent_key: str, summary: str, description: str) -> str:
        state = self._load()
        parent = self._entry(state, parent_key)
        key = self._new_key(state)
        state[key] = {
            "summary": summary,
            "description": description,
            "issue_type": "Subtask",
            "parent": parent_key,
            "status": STATUSES[0],
            "comments": [],
        }
        parent.setdefault("subtasks", []).append(key)
        self._save(state)
        return key

    def summary_text(self) -> str:
        """Human-readable dump of every ticket touched in local mode."""
        lines: list[str] = []
        for key, entry in self._load().items():
            demo = self._demo.get(key)
            title = demo.summary if demo else entry.get("summary", "")
            lines.append(f"{key} [{entry['status']}] {title}")
            lines += [f"  subtask {k}" for k in entry.get("subtasks", [])]
            lines += [f"  comment #{c['id']}: {c['body']}" for c in entry["comments"]]
        return "\n".join(lines) or "(no local Jira activity yet)"
