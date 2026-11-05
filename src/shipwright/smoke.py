"""Verification helpers of `scripts/smoke_live.py` (pure logic over the gateways; unit-tested).

After a live run the PR must exist (open, on `shipwright/<KEY>` or its newest `-rN` revision,
see `publish_pull_request`), the ticket must carry a comment with the PR URL and be `In Review`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shipwright.integrations.github import GitHubGateway
from shipwright.tickets import Ticket

IN_REVIEW = "In Review"


@dataclass
class SmokeReport:
    key: str
    branch: str | None = None
    pr_url: str | None = None
    status: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def key_for_scenario(scenario: int, seeded_path: Path) -> str:
    """Jira key of a demo scenario from `demo/seeded.json` (written by scripts/seed_jira.py)."""
    seeded_path = Path(seeded_path)
    if not seeded_path.exists():
        raise ValueError(f"{seeded_path} not found: run `uv run python scripts/seed_jira.py` first")
    mapping = json.loads(seeded_path.read_text(encoding="utf-8"))
    if str(scenario) not in mapping:
        raise ValueError(
            f"Scenario {scenario} is not in {seeded_path} (have: {', '.join(mapping)})"
        )
    return str(mapping[str(scenario)])


def comment_bodies(data: Any) -> list[str]:
    """Comment texts from a `jira_get_issue` result (`comments` list of dicts or strings)."""
    raw = data.get("comments") if isinstance(data, dict) else None
    bodies: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        body = item.get("body") if isinstance(item, dict) else item
        if isinstance(body, str):
            bodies.append(body)
    return bodies


def _revision(branch: str, root: str) -> int:
    match = re.fullmatch(rf"{re.escape(root)}(?:-r(\d+))?", branch)
    return -1 if match is None else int(match.group(1) or 1)


async def verify_outcome(
    github: GitHubGateway, repo: str, ticket: Ticket, comments: list[str]
) -> SmokeReport:
    """Check PR, Jira comment and status after a run; never raises on a failed check."""
    report = SmokeReport(key=ticket.key, status=ticket.status)
    root = f"shipwright/{ticket.key}"
    branches = sorted(
        (b for b in await github.list_branch_names(repo) if _revision(b, root) > 0),
        key=lambda b: _revision(b, root),
        reverse=True,
    )
    if not branches:
        report.problems.append(f"no branch {root} (or {root}-rN) on {repo}")
    for branch in branches:  # newest revision first
        if (url := await github.find_open_pull_request(repo, branch)) is not None:
            report.branch, report.pr_url = branch, url
            break
    else:
        if branches:
            report.problems.append(f"no open PR on {', '.join(branches)}")
    if report.pr_url and not any(report.pr_url in body for body in comments):
        report.problems.append(f"no Jira comment on {ticket.key} contains {report.pr_url}")
    if ticket.status.strip().lower() != IN_REVIEW.lower():
        report.problems.append(f"{ticket.key} is {ticket.status!r}, expected {IN_REVIEW!r}")
    return report
