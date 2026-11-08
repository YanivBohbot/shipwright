import pytest

from shipwright.domain.tickets import (
    extract_acceptance_criteria,
    load_demo_tickets,
    parse_ticket_key,
)

DESC = (
    "See EXPORT-CSV-v2 and ADR-001.\n## Acceptance criteria\n"
    "- [ ] AC-1 header row\n- [ ] AC-2 404 on unknown project\n"
)


def test_parse_ticket_key_normalizes():
    assert parse_ticket_key(" ship-42 ", "SHIP") == "SHIP-42"
    with pytest.raises(ValueError):
        parse_ticket_key("ship 42", "SHIP")
    with pytest.raises(ValueError):
        parse_ticket_key("ABC-1", "SHIP")


def test_extract_acceptance_criteria():
    assert extract_acceptance_criteria(DESC) == ["AC-1 header row", "AC-2 404 on unknown project"]


def test_acceptance_section_ends_at_next_heading_and_plain_bullets_count():
    d = "## Acceptance criteria\n- first\n* second\n## Notes\n- not a criterion\n"
    assert extract_acceptance_criteria(d) == ["first", "second"]


def test_ac_lines_and_checkboxes_outside_section():
    d = "Intro\nAC-3 stand alone\n- [x] done item\n- plain bullet\n"
    assert extract_acceptance_criteria(d) == ["AC-3 stand alone", "done item"]


def test_empty_description():
    assert extract_acceptance_criteria("") == []


def test_demo_tickets_file_is_valid():
    from pathlib import Path

    tickets = load_demo_tickets(Path(__file__).resolve().parents[3] / "demo" / "tickets.yaml")
    assert sorted(t["scenario"] for t in tickets) == list(range(1, len(tickets) + 1))


def test_demo_tickets_missing_field_is_rejected(tmp_path):
    bad = tmp_path / "t.yaml"
    bad.write_text("- scenario: 1\n  summary: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs the fields"):
        load_demo_tickets(bad)
