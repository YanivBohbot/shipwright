"""Jira composite tools: read (no interrupt), comment/transition, subtasks; HITL config."""

from shipwright.tickets import Ticket
from shipwright.tools import HITL_INTERRUPTS
from shipwright.tools.jira_tools import make_jira_tools
from tests.fakes import FakeJiraGateway, settings_stub

TICKET = Ticket(
    key="SHIP-2",
    summary="Export tasks as CSV",
    description="Implement EXPORT-CSV-v2.\n## Acceptance criteria\n- AC-1 header row",
    status="To Do",
    acceptance_criteria=["AC-1 header row"],
    spec_refs=["EXPORT-CSV-v2", "ADR-001"],
    issue_type="Story",
)
VAGUE = Ticket("SHIP-4", "Make it faster", "Make the app faster.", "To Do", [], [], "Task")


def _tools(jira: FakeJiraGateway) -> dict:
    return {t.name: t for t in make_jira_tools(jira, settings_stub())}


def test_tool_names():
    assert set(_tools(FakeJiraGateway())) == {
        "read_jira_ticket",
        "update_jira_ticket",
        "create_jira_subtasks",
    }


async def test_read_ticket_tool_reports_missing_ticket():
    read = _tools(FakeJiraGateway())["read_jira_ticket"]
    assert await read.ainvoke({"key": "ship-999"}) == "Ticket SHIP-999 not found in project SHIP."
    assert await read.ainvoke({"key": "ship 42"}) == "Ticket ship 42 not found in project SHIP."


async def test_read_ticket_tool_renders_ticket():
    read = _tools(FakeJiraGateway({"SHIP-2": TICKET}))["read_jira_ticket"]
    out = await read.ainvoke({"key": " ship-2 "})
    assert "SHIP-2" in out and "Export tasks as CSV" in out
    assert "Status: To Do" in out and "Implement EXPORT-CSV-v2." in out
    assert "Acceptance criteria:\n- AC-1 header row" in out
    assert "Spec references: EXPORT-CSV-v2, ADR-001" in out


async def test_read_ticket_tool_flags_missing_criteria():
    read = _tools(FakeJiraGateway({"SHIP-4": VAGUE}))["read_jira_ticket"]
    out = await read.ainvoke({"key": "SHIP-4"})
    assert "Acceptance criteria: NONE FOUND" in out
    assert "Spec references: NONE" in out


async def test_update_jira_ticket_comment_and_transition():
    jira = FakeJiraGateway({"SHIP-2": TICKET})
    update = _tools(jira)["update_jira_ticket"]
    out = await update.ainvoke(
        {"key": "ship-2", "comment": "PR opened: https://x/pull/1", "transition_to": "In Review"}
    )
    assert jira.comments == [("SHIP-2", "PR opened: https://x/pull/1")]
    assert jira.transitions == [("SHIP-2", "In Review")]
    assert "SHIP-2" in out and "In Review" in out


async def test_update_jira_ticket_comment_only_and_invalid_key():
    jira = FakeJiraGateway({"SHIP-2": TICKET})
    update = _tools(jira)["update_jira_ticket"]
    await update.ainvoke({"key": "SHIP-2", "comment": "Needs clarification."})
    assert jira.transitions == []
    out = await update.ainvoke({"key": "OTHER-1", "comment": "x"})
    assert out == "Ticket OTHER-1 not found in project SHIP."
    assert jira.comments == [("SHIP-2", "Needs clarification.")]


async def test_update_jira_ticket_reports_gateway_errors_as_text():
    class Broken(FakeJiraGateway):
        async def transition(self, key: str, to_status: str) -> None:
            raise RuntimeError("no transition named Bogus")

    jira = Broken({"SHIP-2": TICKET})
    out = await _tools(jira)["update_jira_ticket"].ainvoke(
        {"key": "SHIP-2", "comment": "hi", "transition_to": "Bogus"}
    )
    assert jira.comments == [("SHIP-2", "hi")]
    assert "comment added" in out.lower() and "no transition named Bogus" in out


async def test_create_jira_subtasks_returns_created_keys():
    jira = FakeJiraGateway({"SHIP-7": TICKET})
    create = _tools(jira)["create_jira_subtasks"]
    out = await create.ainvoke(
        {
            "parent_key": "ship-7",
            "subtasks": [
                {"summary": "Backend endpoint", "description": "Add GET /export"},
                {"summary": "Docs", "description": "Document the endpoint"},
            ],
        }
    )
    assert jira.subtasks == [
        ("SHIP-7", "Backend endpoint", "Add GET /export"),
        ("SHIP-7", "Docs", "Document the endpoint"),
    ]
    assert "SHIP-101" in out and "SHIP-102" in out


async def test_create_jira_subtasks_rejects_empty_list_and_bad_key():
    jira = FakeJiraGateway()
    create = _tools(jira)["create_jira_subtasks"]
    assert "No subtasks" in await create.ainvoke({"parent_key": "SHIP-7", "subtasks": []})
    out = await create.ainvoke(
        {"parent_key": "nope", "subtasks": [{"summary": "a", "description": "b"}]}
    )
    assert out == "Ticket nope not found in project SHIP."
    assert jira.subtasks == []


def test_hitl_interrupts_config():
    assert HITL_INTERRUPTS == {
        "publish_pull_request": {"allowed_decisions": ["approve", "reject"]},
        "update_jira_ticket": {"allowed_decisions": ["approve", "edit", "reject"]},
        "create_jira_subtasks": {"allowed_decisions": ["approve", "edit", "reject"]},
    }


async def test_read_ticket_tool_reports_gateway_errors_as_text():
    class Broken(FakeJiraGateway):
        async def get_issue(self, key: str) -> Ticket:
            raise RuntimeError("MCP timeout")

    out = await _tools(Broken())["read_jira_ticket"].ainvoke({"key": "SHIP-2"})
    assert out == "Failed to read ticket SHIP-2: MCP timeout"
