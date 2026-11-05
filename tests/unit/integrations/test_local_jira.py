import json
from pathlib import Path

import pytest

from shipwright.domain.tickets import TicketNotFound
from shipwright.integrations.jira.local_jira import LocalJira, load_demo_tickets

ROOT = Path(__file__).resolve().parents[3]
DEMO_TICKETS = ROOT / "demo" / "tickets.yaml"


@pytest.fixture
def jira(tmp_path) -> LocalJira:
    return LocalJira(DEMO_TICKETS, tmp_path / ".local_jira.json")


async def test_demo_tickets_are_served_by_scenario_key(jira):
    t = await jira.get_issue("SHIP-1")
    assert t.key == "SHIP-1" and t.issue_type == "Bug" and t.status == "To Do"
    assert "empty assignee" in t.summary
    assert len(t.acceptance_criteria) == 3
    t2 = await jira.get_issue("SHIP-2")
    assert t2.spec_refs == ["EXPORT-CSV-v2", "ADR-001"]
    with pytest.raises(TicketNotFound):
        await jira.get_issue("SHIP-999")


async def test_comments_and_transitions_persist_across_instances(jira, tmp_path):
    assert await jira.add_comment("SHIP-1", "PR opened") == "1"
    assert await jira.add_comment("SHIP-1", "second") == "2"
    await jira.transition("SHIP-1", "in review")
    again = LocalJira(DEMO_TICKETS, tmp_path / ".local_jira.json")
    assert (await again.get_issue("SHIP-1")).status == "In Review"
    state = json.loads((tmp_path / ".local_jira.json").read_text(encoding="utf-8"))
    assert [c["body"] for c in state["SHIP-1"]["comments"]] == ["PR opened", "second"]
    text = again.summary_text()
    assert "SHIP-1 [In Review]" in text and "comment #1: PR opened" in text


async def test_unknown_status_and_unknown_ticket_are_refused(jira, tmp_path):
    with pytest.raises(ValueError, match="No transition to status 'Shipped'.*In Review"):
        await jira.transition("SHIP-1", "Shipped")
    with pytest.raises(TicketNotFound):
        await jira.add_comment("SHIP-42", "x")
    assert not (tmp_path / ".local_jira.json").exists()


async def test_subtasks_and_issues_get_new_keys(jira):
    first = await jira.create_subtask("SHIP-4", "Profile endpoints", "Find the slow ones")
    second = await jira.create_subtask("SHIP-4", "Add caching", "")
    assert (first, second) == ("SHIP-101", "SHIP-102")
    sub = await jira.get_issue("SHIP-101")
    assert sub.issue_type == "Subtask" and sub.summary == "Profile endpoints"
    assert await jira.create_issue("SHIP", "New", "desc", "Task") == "SHIP-103"
    assert "  subtask SHIP-101" in jira.summary_text()
    with pytest.raises(TicketNotFound):
        await jira.create_subtask("SHIP-77", "x", "y")
    with pytest.raises(ValueError, match="project"):
        await jira.create_issue("OTHER", "x", "y", "Task")


def test_load_demo_tickets_validates(tmp_path):
    assert [t.scenario for t in load_demo_tickets(DEMO_TICKETS)][:5] == [1, 2, 3, 4, 5]
    bad = tmp_path / "t.yaml"
    bad.write_text(
        "- {scenario: 1, summary: s, description: d, issue_type: Bug, expected: pr}\n"
        "- {scenario: 1, summary: s, description: d, issue_type: Bug, expected: pr}\n"
    )
    with pytest.raises(ValueError, match="Duplicate scenario 1"):
        load_demo_tickets(bad)
    bad.write_text("- {scenario: 1, summary: s}\n")
    with pytest.raises(ValueError, match="missing field"):
        load_demo_tickets(bad)
