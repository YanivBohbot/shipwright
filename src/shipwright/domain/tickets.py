"""Ticket helpers: key normalisation, acceptance criteria, the demo tickets file."""

import re
from pathlib import Path

import yaml

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(.*?)\s*#*\s*$")
_AC_HEADING_RE = re.compile(r"^acceptance criteria:?$", re.IGNORECASE)
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.+?)\s*$")
_AC_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?(AC-\d+\b.*?)\s*$")


def parse_ticket_key(raw: str, project: str) -> str:
    """Normalise ``" ship-42 "`` to ``"SHIP-42"``; raise ValueError if not a key of `project`."""
    key = raw.strip().upper()
    if not re.fullmatch(rf"{re.escape(project.upper())}-\d+", key):
        raise ValueError(f"Invalid ticket key {raw!r}: expected format {project.upper()}-<number>")
    return key


def extract_acceptance_criteria(description: str) -> list[str]:
    """Criteria from the `Acceptance criteria` section, plus `- [ ]` and `AC-n` lines elsewhere."""
    found: list[str] = []
    in_section = False
    for line in description.splitlines():
        heading = _HEADING_RE.match(line)
        if heading or _AC_HEADING_RE.match(line.strip()):
            title = heading.group(1) if heading else line.strip()
            in_section = bool(_AC_HEADING_RE.match(title.strip()))
            continue
        if not line.strip():
            continue
        if in_section:
            m = _BULLET_RE.match(line)
            found.append(m.group(1) if m else line.strip())
            continue
        m = _CHECKBOX_RE.match(line) or _AC_LINE_RE.match(line)
        if m:
            found.append(m.group(1))
    return list(dict.fromkeys(found))


DEMO_TICKET_FIELDS = ("scenario", "issue_type", "expected", "summary", "description")


def load_demo_tickets(path: Path) -> list[dict]:
    """The demo tickets of `demo/tickets.yaml` (seeded into Jira by `scripts/seed_jira.py`)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of demo tickets")
    for i, entry in enumerate(data, start=1):
        if not isinstance(entry, dict) or any(f not in entry for f in DEMO_TICKET_FIELDS):
            fields = ", ".join(DEMO_TICKET_FIELDS)
            raise ValueError(f"{path}: ticket #{i} needs the fields {fields}")
    return data
