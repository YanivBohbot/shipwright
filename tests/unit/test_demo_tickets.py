"""demo/tickets.yaml (one ticket per scenario) and the idempotent seed script."""

import dataclasses
import json
from pathlib import Path

import pytest

from scripts.seed_jira import seed_demo_tickets
from shipwright.domain.tickets import extract_acceptance_criteria, extract_spec_refs
from shipwright.integrations.jira.local_jira import DemoTicket
from shipwright.integrations.jira.local_jira import load_demo_tickets as _load
from tests.fakes import FakeJiraGateway

ROOT = Path(__file__).resolve().parents[2]


def load_demo_tickets(path: Path = ROOT / "demo" / "tickets.yaml") -> list[DemoTicket]:
    return _load(path)


def _by_scenario() -> dict[int, DemoTicket]:
    return {t.scenario: t for t in load_demo_tickets()}


def test_demo_tickets_cover_all_scenarios():
    assert {t.scenario for t in load_demo_tickets()} == {1, 2, 3, 4, 5, 6, 7}


def test_expected_outcomes():
    expected = {s: t.expected for s, t in _by_scenario().items()}
    assert expected == {
        1: "pr",
        2: "pr",
        3: "pr",
        4: "questions",
        5: "draft_or_stop",
        6: "pr",
        7: "subtasks",
    }


def test_scenario_4_has_no_acceptance_criteria():
    assert extract_acceptance_criteria(_by_scenario()[4].description) == []


def test_scenario_2_references_export_spec():
    t = _by_scenario()[2]
    assert "EXPORT-CSV-v2" in extract_spec_refs(t.description)
    assert len(extract_acceptance_criteria(t.description)) == 6


def test_scenario_3_and_6_references():
    tickets = _by_scenario()
    assert "ADR-002" in extract_spec_refs(tickets[3].description)
    assert len(extract_acceptance_criteria(tickets[3].description)) == 3
    assert "RFC 9110" in tickets[3].description
    assert "HEALTH-v1" in extract_spec_refs(tickets[6].description)
    assert len(extract_acceptance_criteria(tickets[6].description)) == 5


def test_scenario_5_has_external_network_criterion():
    acs = extract_acceptance_criteria(_by_scenario()[5].description)
    assert any("partner.example.invalid" in ac for ac in acs)


def test_scenario_1_and_7_have_content():
    tickets = _by_scenario()
    assert "assignee" in tickets[1].description
    assert extract_acceptance_criteria(tickets[1].description)
    assert "OAuth2" in tickets[7].summary and "RBAC" in tickets[7].summary


def test_demo_ticket_is_frozen():
    t = load_demo_tickets()[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.summary = "x"  # type: ignore[misc]


def test_unknown_expected_value_rejected(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(
        "- scenario: 1\n  summary: s\n  description: d\n  issue_type: Bug\n  expected: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected"):
        load_demo_tickets(p)


def test_missing_field_rejected(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("- scenario: 1\n  summary: s\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_demo_tickets(p)


def test_duplicate_scenario_rejected(tmp_path):
    entry = "- scenario: 1\n  summary: s\n  description: d\n  issue_type: Bug\n  expected: pr\n"
    p = tmp_path / "t.yaml"
    p.write_text(entry * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_demo_tickets(p)


async def test_seed_creates_all_and_writes_mapping(tmp_path):
    jira = FakeJiraGateway()
    path = tmp_path / "seeded.json"
    tickets = load_demo_tickets()
    mapping = await seed_demo_tickets(jira, "SHIP", tickets, path)
    assert set(mapping) == {1, 2, 3, 4, 5, 6, 7}
    assert len(jira.issues) == 7
    assert all(project == "SHIP" for project, *_ in jira.issues)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == {str(s): k for s, k in mapping.items()}


async def test_seed_is_idempotent(tmp_path):
    jira = FakeJiraGateway()
    path = tmp_path / "seeded.json"
    tickets = load_demo_tickets()
    first = await seed_demo_tickets(jira, "SHIP", tickets, path)
    second = await seed_demo_tickets(jira, "SHIP", tickets, path)
    assert first == second
    assert len(jira.issues) == 7


async def test_seed_only_creates_missing(tmp_path):
    jira = FakeJiraGateway()
    path = tmp_path / "seeded.json"
    path.write_text(json.dumps({"1": "SHIP-1", "2": "SHIP-2"}), encoding="utf-8")
    mapping = await seed_demo_tickets(jira, "SHIP", load_demo_tickets(), path)
    assert len(jira.issues) == 5
    assert mapping[1] == "SHIP-1" and mapping[2] == "SHIP-2"


def test_seed_script_exposes_main():
    import importlib.util

    spec = importlib.util.spec_from_file_location("seed_jira", ROOT / "scripts" / "seed_jira.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)


async def test_seed_corrupt_file_raises_clear_error(tmp_path):
    path = tmp_path / "seeded.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="seeded.json"):
        await seed_demo_tickets(FakeJiraGateway(), "SHIP", load_demo_tickets(), path)
