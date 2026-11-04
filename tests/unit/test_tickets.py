import pytest

from shipwright.tickets import (
    Ticket,
    extract_acceptance_criteria,
    extract_spec_refs,
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


def test_extract_acceptance_criteria_and_refs():
    assert extract_acceptance_criteria(DESC) == ["AC-1 header row", "AC-2 404 on unknown project"]
    assert extract_spec_refs(DESC) == ["EXPORT-CSV-v2", "ADR-001"]


def test_acceptance_section_ends_at_next_heading_and_plain_bullets_count():
    d = "## Acceptance criteria\n- first\n* second\n## Notes\n- not a criterion\n"
    assert extract_acceptance_criteria(d) == ["first", "second"]


def test_ac_lines_and_checkboxes_outside_section():
    d = "Intro\nAC-3 stand alone\n- [x] done item\n- plain bullet\n"
    assert extract_acceptance_criteria(d) == ["AC-3 stand alone", "done item"]


def test_empty_description():
    assert extract_acceptance_criteria("") == []
    assert extract_spec_refs("") == []


def test_spec_refs_deduplicated():
    assert extract_spec_refs("ADR-001 ADR-001 FOO-v1 ADR-12") == ["ADR-001", "FOO-v1"]


def test_ticket_is_frozen():
    t = Ticket("SHIP-1", "s", "d", "To Do", [], [], "Task")
    with pytest.raises(AttributeError):
        t.key = "X"  # type: ignore[misc]
