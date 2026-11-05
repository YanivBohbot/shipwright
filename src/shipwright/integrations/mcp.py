"""MCP servers (GitHub, Atlassian): connection config, tool loading and a strict `call_tool`.

Raw MCP tools are never given to the agent: only the gateways call them, through `call_tool`,
which turns every kind of MCP failure into `McpToolError`.
"""

import json
import uuid
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

from shipwright.core.config import Settings

_ENV_HINTS = {
    "github": "GITHUB_PAT / GITHUB_MCP_URL",
    "atlassian": "JIRA_URL / JIRA_USERNAME / JIRA_API_TOKEN",
}


class McpConnectionError(RuntimeError):
    """An MCP server could not be reached or initialised."""


class McpToolError(ToolException):
    """An MCP tool call failed (raised ToolException or an `isError` / error-status result)."""

    def __init__(self, tool_name: str, message: str) -> None:
        self.tool_name = tool_name
        self.message = message
        super().__init__(f"MCP tool '{tool_name}' failed: {message}")


def _secret(value: Any, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required to connect to the MCP servers (live mode)")
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)


def mcp_server_config(settings: Settings) -> dict[str, dict[str, Any]]:
    """`MultiServerMCPClient` connections: remote GitHub MCP (HTTP) and mcp-atlassian (stdio)."""
    return {
        "github": {
            "transport": "streamable_http",
            "url": settings.github_mcp_url,
            "headers": {"Authorization": f"Bearer {_secret(settings.github_pat, 'GITHUB_PAT')}"},
        },
        "atlassian": {
            "transport": "stdio",
            "command": "mcp-atlassian",
            "args": [],
            "env": {
                "JIRA_URL": _secret(settings.jira_url, "JIRA_URL"),
                "JIRA_USERNAME": _secret(settings.jira_username, "JIRA_USERNAME"),
                "JIRA_API_TOKEN": _secret(settings.jira_api_token, "JIRA_API_TOKEN"),
            },
        },
    }


async def load_mcp_tools(settings: Settings) -> dict[str, dict[str, BaseTool]]:
    """Load tools per server, as ``{server_name: {tool_name: tool}}``."""
    config = mcp_server_config(settings)
    # handle_tool_errors=False: MCP `isError` results raise ToolException instead of being
    # returned as content (a failed write must never look like a success to the gateways).
    client = MultiServerMCPClient(config, handle_tool_errors=False)
    result: dict[str, dict[str, BaseTool]] = {}
    for name in config:
        try:
            tools = await client.get_tools(server_name=name)
        except Exception as exc:
            hint = _ENV_HINTS.get(name, "the MCP settings")
            raise McpConnectionError(f"Cannot reach MCP server '{name}'. Check {hint}.") from exc
        result[name] = {t.name: t for t in tools}
    return result


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        ).strip()
    return str(content)


async def call_tool(tool: BaseTool, args: dict[str, Any]) -> Any:
    """Call an MCP tool and return its normalised result; raise McpToolError on ANY tool error.

    The tool is invoked as a tool call so that an error result swallowed by the adapter's
    `handle_tool_errors` (a ToolMessage with `status="error"`) is detected too.
    """
    call = {"type": "tool_call", "id": f"sw-{uuid.uuid4().hex}", "name": tool.name, "args": args}
    try:
        result = await tool.ainvoke(call)
    except McpToolError:
        raise
    except ToolException as exc:
        raise McpToolError(tool.name, str(exc)) from exc
    if isinstance(result, ToolMessage):
        if result.status == "error":
            raise McpToolError(tool.name, _content_text(result.content))
        result = result.content
    return normalize_tool_result(result)


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
