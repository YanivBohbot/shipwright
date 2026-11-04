import json

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient

from shipwright.integrations.mcp_client import (
    McpConnectionError,
    load_mcp_tools,
    mcp_server_config,
    normalize_tool_result,
)
from tests.fakes import FakeTool, settings_stub


def test_mcp_server_config():
    cfg = mcp_server_config(settings_stub())
    assert cfg["github"] == {
        "transport": "streamable_http",
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": "Bearer ghp_fake"},
    }
    assert cfg["atlassian"]["transport"] == "stdio"
    assert cfg["atlassian"]["command"] == "mcp-atlassian"
    assert cfg["atlassian"]["env"] == {
        "JIRA_URL": "https://acme.atlassian.net",
        "JIRA_USERNAME": "bot@acme.com",
        "JIRA_API_TOKEN": "jira-fake",
    }


def test_config_is_accepted_by_adapter_client():
    MultiServerMCPClient(mcp_server_config(settings_stub()))  # no validation error


async def test_load_mcp_tools_wraps_connection_error(monkeypatch):
    async def boom(self, *, server_name=None):
        raise ConnectionError("refused")

    monkeypatch.setattr(MultiServerMCPClient, "get_tools", boom)
    with pytest.raises(McpConnectionError) as ei:
        await load_mcp_tools(settings_stub())
    assert "github" in str(ei.value)
    assert "GITHUB_PAT" in str(ei.value)


async def test_load_mcp_tools_groups_by_server(monkeypatch):
    async def fake(self, *, server_name=None):
        return [FakeTool(name=f"{server_name}_a"), FakeTool(name=f"{server_name}_b")]

    monkeypatch.setattr(MultiServerMCPClient, "get_tools", fake)
    tools = await load_mcp_tools(settings_stub())
    assert set(tools) == {"github", "atlassian"}
    assert set(tools["github"]) == {"github_a", "github_b"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ("plain text", "plain text"),
        ([{"type": "text", "text": '{"a":'}, {"type": "text", "text": " 2}"}], {"a": 2}),
        ({"already": "obj"}, {"already": "obj"}),
        ([1, 2], [1, 2]),
    ],
)
def test_normalize_tool_result(raw, expected):
    assert normalize_tool_result(raw) == expected
    assert json.dumps(normalize_tool_result(raw))
