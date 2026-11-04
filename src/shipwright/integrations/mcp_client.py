"""MCP server configuration, tool loading and result normalisation."""

import json
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from shipwright.config import Settings

_ENV_HINTS = {
    "github": "GITHUB_PAT / GITHUB_MCP_URL",
    "atlassian": "JIRA_URL / JIRA_USERNAME / JIRA_API_TOKEN",
}


class McpConnectionError(RuntimeError):
    """An MCP server could not be reached or initialised."""


def mcp_server_config(settings: Settings) -> dict[str, dict[str, Any]]:
    pat = settings.github_pat.get_secret_value()
    jira_token = settings.jira_api_token.get_secret_value()
    return {
        "github": {
            "transport": "streamable_http",
            "url": settings.github_mcp_url,
            "headers": {"Authorization": f"Bearer {pat}"},
        },
        "atlassian": {
            "transport": "stdio",
            "command": "mcp-atlassian",
            "args": [],
            "env": {
                "JIRA_URL": settings.jira_url,
                "JIRA_USERNAME": settings.jira_username,
                "JIRA_API_TOKEN": jira_token,
            },
        },
    }


async def load_mcp_tools(settings: Settings) -> dict[str, dict[str, BaseTool]]:
    """Load tools per server, as ``{server_name: {tool_name: tool}}``."""
    config = mcp_server_config(settings)
    client = MultiServerMCPClient(config)
    result: dict[str, dict[str, BaseTool]] = {}
    for name in config:
        try:
            tools = await client.get_tools(server_name=name)
        except Exception as exc:
            hint = _ENV_HINTS.get(name, "the MCP settings")
            raise McpConnectionError(f"Cannot reach MCP server '{name}'. Check {hint}.") from exc
        result[name] = {t.name: t for t in tools}
    return result


def normalize_tool_result(raw: Any) -> Any:
    """Turn an MCP tool result (str, content-block list, dict...) into a Python object.

    Text blocks are concatenated; the text is parsed as JSON when possible, else returned as str.
    """
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
            elif isinstance(block, str):
                parts.append(block)
            else:
                return raw
        raw = "".join(parts)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw
