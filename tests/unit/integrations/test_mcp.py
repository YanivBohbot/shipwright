import json

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient

from shipwright.integrations.mcp import (
    McpConnectionError,
    load_mcp_tools,
    mcp_server_config,
    normalize_tool_result,
)
from tests.fakes import FakeTool, make_settings


def test_mcp_server_config():
    cfg = mcp_server_config(make_settings(mode="live"))
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
    MultiServerMCPClient(mcp_server_config(make_settings(mode="live")))  # no validation error


async def test_load_mcp_tools_wraps_connection_error(monkeypatch):
    async def boom(self, *, server_name=None):
        raise ConnectionError("refused")

    monkeypatch.setattr(MultiServerMCPClient, "get_tools", boom)
    with pytest.raises(McpConnectionError) as ei:
        await load_mcp_tools(make_settings(mode="live"))
    assert "github" in str(ei.value)
    assert "GITHUB_PAT" in str(ei.value)
    for secret in ("ghp_fake", "jira-fake"):
        assert secret not in str(ei.value)
        assert secret not in str(ei.value.__cause__)


async def test_load_mcp_tools_groups_by_server(monkeypatch):
    async def fake(self, *, server_name=None):
        return [FakeTool(name=f"{server_name}_a"), FakeTool(name=f"{server_name}_b")]

    monkeypatch.setattr(MultiServerMCPClient, "get_tools", fake)
    tools = await load_mcp_tools(make_settings(mode="live"))
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


async def test_load_mcp_tools_disables_adapter_error_swallowing(monkeypatch):
    seen = []

    async def fake(self, *, server_name=None):
        seen.append(self.handle_tool_errors)
        return []

    monkeypatch.setattr(MultiServerMCPClient, "get_tools", fake)
    await load_mcp_tools(make_settings(mode="live"))
    assert seen == [False, False]


def test_mcp_server_config_needs_live_secrets():
    with pytest.raises(ValueError, match="GITHUB_PAT"):
        mcp_server_config(make_settings())


async def test_call_tool_raises_on_every_error_shape():
    from langchain_core.tools import ToolException

    from shipwright.integrations.mcp import McpToolError, call_tool

    assert await call_tool(FakeTool("t", result='{"ok": 1}'), {"a": 1}) == {"ok": 1}
    with pytest.raises(McpToolError, match="'t' failed: boom"):
        await call_tool(FakeTool("t", raises=ToolException("boom")), {})
    with pytest.raises(McpToolError, match="403"):
        await call_tool(FakeTool("t", error_content="403 Forbidden"), {})
