"""Ticket model and pure parsing helpers (no I/O)."""

import re
from dataclasses import dataclass

_SPEC_REF_RE = re.compile(r"\b(?:[A-Z]+(?:-[A-Z0-9]+)*-v\d+|ADR-\d{3})\b")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(.*?)\s*#*\s*$")
_AC_HEADING_RE = re.compile(r"^acceptance criteria:?$", re.IGNORECASE)
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.+?)\s*$")
_AC_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?(AC-\d+\b.*?)\s*$")


class TicketNotFound(Exception):
    """Raised when a Jira ticket does not exist (or is not visible)."""


@dataclass(frozen=True)
class Ticket:
    key: str
    summary: str
    description: str
    status: str
    acceptance_criteria: list[str]
    spec_refs: list[str]
    issue_type: str


def parse_ticket_key(raw: str, project: str) -> str:
    """Normalise ``" ship-42 "`` to ``"SHIP-42"``; raise ValueError if not a key of `project`."""
    key = raw.strip().upper()
    if not re.fullmatch(rf"{re.escape(project.upper())}-\d+", key):
        raise ValueError(f"Invalid ticket key {raw!r}: expected format {project.upper()}-<number>")
    return key


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


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
    return _dedupe(found)


def extract_spec_refs(description: str) -> list[str]:
    """Spec/ADR references (e.g. EXPORT-CSV-v2, ADR-001), in order of first appearance."""
    return _dedupe(_SPEC_REF_RE.findall(description))
