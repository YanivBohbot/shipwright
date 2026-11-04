import json

import pytest
from langchain_core.tools import ToolException

from shipwright.integrations.jira import McpJiraGateway
from shipwright.tickets import TicketNotFound
from tests.fakes import FakeTool

ISSUE = {
    "key": "SHIP-1",
    "summary": "Export CSV",
    "description": "See EXPORT-CSV-v2.\n## Acceptance criteria\n- [ ] AC-1 header row\n",
    "status": {"name": "To Do"},
    "issue_type": {"name": "Story"},
}


async def test_get_issue_maps_fields():
    tool = FakeTool(result=json.dumps(ISSUE))
    gw = McpJiraGateway(tools={"jira_get_issue": tool})
    t = await gw.get_issue("SHIP-1")
    assert tool.calls == [{"issue_key": "SHIP-1"}]
    assert (t.key, t.summary, t.status, t.issue_type) == ("SHIP-1", "Export CSV", "To Do", "Story")
    assert t.acceptance_criteria == ["AC-1 header row"]
    assert t.spec_refs == ["EXPORT-CSV-v2"]


async def test_get_issue_accepts_content_blocks_and_null_description():
    blocks = [{"type": "text", "text": json.dumps({**ISSUE, "description": None})}]
    gw = McpJiraGateway(tools={"jira_get_issue": FakeTool(result=blocks)})
    t = await gw.get_issue("SHIP-1")
    assert t.description == ""
    assert t.acceptance_criteria == []


async def test_get_issue_not_found_raises_ticket_not_found():
    tool = FakeTool(raises=ToolException("Issue does not exist"))
    gw = McpJiraGateway(tools={"jira_get_issue": tool})
    with pytest.raises(TicketNotFound):
        await gw.get_issue("SHIP-999")


async def test_get_issue_other_errors_propagate():
    tool = FakeTool(raises=ToolException("401 Unauthorized"))
    gw = McpJiraGateway(tools={"jira_get_issue": tool})
    with pytest.raises(ToolException):
        await gw.get_issue("SHIP-1")


async def test_add_comment_returns_id():
    tool = FakeTool(result=json.dumps({"id": "10001", "body": "hi"}))
    gw = McpJiraGateway(tools={"jira_add_comment": tool})
    assert await gw.add_comment("SHIP-1", "hi") == "10001"
    assert tool.calls == [{"issue_key": "SHIP-1", "body": "hi"}]


async def test_transition_resolves_status_name():
    get_t = FakeTool(result=json.dumps([{"id": "31", "name": "In Review"}]))
    do_t = FakeTool(result="{}")
    gw = McpJiraGateway(tools={"jira_get_transitions": get_t, "jira_transition_issue": do_t})
    await gw.transition("SHIP-1", "in review")
    assert get_t.calls == [{"issue_key": "SHIP-1"}]
    assert do_t.calls == [{"issue_key": "SHIP-1", "transition_id": "31"}]


async def test_transition_unknown_status_raises_value_error():
    get_t = FakeTool(result=json.dumps([{"id": "31", "name": "In Review"}]))
    do_t = FakeTool(result="{}")
    gw = McpJiraGateway(tools={"jira_get_transitions": get_t, "jira_transition_issue": do_t})
    with pytest.raises(ValueError, match="In Review"):
        await gw.transition("SHIP-1", "Done")
    assert do_t.calls == []


async def test_create_subtask_uses_subtask_type_and_parent():
    tool = FakeTool(result=json.dumps({"message": "ok", "issue": {"key": "SHIP-7"}}))
    gw = McpJiraGateway(tools={"jira_create_issue": tool})
    assert await gw.create_subtask("SHIP-1", "Do X", "details") == "SHIP-7"
    (call,) = tool.calls
    assert call["project_key"] == "SHIP"
    assert call["issue_type"] == "Subtask"
    assert json.loads(call["additional_fields"]) == {"parent": "SHIP-1"}
    assert (call["summary"], call["description"]) == ("Do X", "details")


async def test_create_issue_free_type():
    tool = FakeTool(result=json.dumps({"issue": {"key": "SHIP-8"}}))
    gw = McpJiraGateway(tools={"jira_create_issue": tool})
    assert await gw.create_issue("SHIP", "Bug!", "desc", "Bug") == "SHIP-8"
    assert tool.calls == [
        {"project_key": "SHIP", "summary": "Bug!", "issue_type": "Bug", "description": "desc"}
    ]


async def test_missing_tool_is_explicit():
    gw = McpJiraGateway(tools={})
    with pytest.raises(KeyError, match="jira_add_comment"):
        await gw.add_comment("SHIP-1", "x")


@pytest.mark.parametrize(
    "message",
    [
        "Issue SHIP-404 not found.",
        "Issue does not exist or you do not have permission to see it.",
        "issue  does not exist",
    ],
)
async def test_not_found_phrases(message):
    key = "SHIP-404" if "SHIP-404" in message else "SHIP-1"
    gw = McpJiraGateway(tools={"jira_get_issue": FakeTool(raises=ToolException(message))})
    with pytest.raises(TicketNotFound):
        await gw.get_issue(key)


@pytest.mark.parametrize(
    "message",
    [
        "401 Unauthorized fetching SHIP-404",
        "<html><title>404 Not Found</title></html>",
        "Project not found",
        "User not found",
    ],
)
async def test_other_errors_are_not_ticket_not_found(message):
    gw = McpJiraGateway(tools={"jira_get_issue": FakeTool(raises=ToolException(message))})
    with pytest.raises(ToolException):
        await gw.get_issue("SHIP-404")


async def test_non_dict_result_is_runtime_error():
    gw = McpJiraGateway(tools={"jira_get_issue": FakeTool(result="garbage text")})
    with pytest.raises(RuntimeError, match="str"):
        await gw.get_issue("SHIP-1")
