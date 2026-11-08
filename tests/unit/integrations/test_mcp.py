"""MCP integration: connections and the tool allowlist (no server is started)."""

import pytest

from shipwright.agent.hitl import INTERRUPT_ON
from shipwright.integrations.mcp import (
    ALLOWED_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    mcp_connections,
    select_tools,
)
from tests.fakes import fake_mcp_tool, make_settings


def test_connections_carry_credentials_to_each_server():
    conns = mcp_connections(make_settings())
    assert conns["github"]["transport"] == "streamable_http"
    assert conns["github"]["headers"]["Authorization"] == "Bearer ghp_fake"
    env = conns["atlassian"]["env"]
    assert conns["atlassian"]["transport"] == "stdio"
    assert env["JIRA_URL"] == "https://acme.atlassian.net"
    assert env["JIRA_USERNAME"] == "bot@acme.com"
    assert env["JIRA_API_TOKEN"] == "jira-fake"
    assert env["TOOLSETS"] == "jira_issues,jira_comments,jira_transitions"


def test_select_tools_keeps_only_the_allowlist():
    exposed = [fake_mcp_tool(n) for n in (*ALLOWED_TOOLS, "merge_pull_request", "delete_file")]
    assert [t.name for t in select_tools(exposed)] == list(ALLOWED_TOOLS)


def test_select_tools_fails_when_a_tool_is_missing():
    exposed = [fake_mcp_tool(n) for n in ALLOWED_TOOLS if n != "jira_get_issue"]
    with pytest.raises(RuntimeError, match="jira_get_issue"):
        select_tools(exposed)


def test_every_write_tool_needs_human_approval():
    assert set(INTERRUPT_ON) == set(WRITE_TOOLS)
    assert set(READ_TOOLS) | set(WRITE_TOOLS) == set(ALLOWED_TOOLS)
    assert not set(READ_TOOLS) & set(WRITE_TOOLS)
