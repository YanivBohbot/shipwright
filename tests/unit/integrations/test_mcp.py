"""MCP integration: connections, the allowlist and the approval rule (no server is started)."""

import pytest

from shipwright.integrations.mcp import (
    ALLOWED_TOOLS,
    interrupt_on_for,
    is_read_only,
    mcp_connections,
    select_tools,
)
from tests.fakes import READ_ONLY_MCP_TOOLS, fake_mcp, fake_mcp_tool, make_settings


def test_connections_carry_credentials_to_each_server():
    conns = mcp_connections(make_settings())
    github, atlassian = conns["github"], conns["atlassian"]
    assert github["transport"] == "streamable_http"
    assert github.get("headers") == {"Authorization": "Bearer ghp_fake"}
    assert atlassian["transport"] == "stdio"
    env = atlassian.get("env") or {}
    assert env["JIRA_URL"] == "https://acme.atlassian.net"
    assert env["JIRA_USERNAME"] == "bot@acme.com"
    assert env["JIRA_API_TOKEN"] == "jira-fake"
    assert env["TOOLSETS"] == "jira_issues,jira_comments,jira_transitions"


def test_select_tools_keeps_only_the_allowlist():
    exposed = [fake_mcp_tool(n) for n in (*ALLOWED_TOOLS, "merge_pull_request", "delete_file")]
    assert list(select_tools(exposed)) == list(ALLOWED_TOOLS)


def test_select_tools_fails_when_a_tool_is_missing():
    exposed = [fake_mcp_tool(n) for n in ALLOWED_TOOLS if n != "jira_get_issue"]
    with pytest.raises(RuntimeError, match="jira_get_issue"):
        select_tools(exposed)


def test_every_tool_not_declared_read_only_needs_approval():
    tools = list(fake_mcp().values())
    approval = interrupt_on_for(tools)
    assert set(approval) == set(ALLOWED_TOOLS) - READ_ONLY_MCP_TOOLS
    assert set(approval) == {
        "create_branch",
        "push_files",
        "create_pull_request",
        "jira_add_comment",
        "jira_transition_issue",
        "jira_create_issue",
    }


def test_a_tool_without_annotation_needs_approval():
    tool = fake_mcp_tool("list_branches")
    tool.metadata = None  # a server that sends no annotation
    assert not is_read_only(tool)
    assert "list_branches" in interrupt_on_for([tool])
