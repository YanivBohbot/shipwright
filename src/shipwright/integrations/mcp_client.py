"""v1 location: re-exports `shipwright.integrations.mcp` (removed in phase B)."""

from shipwright.integrations.mcp import (  # noqa: F401
    McpConnectionError,
    McpToolError,
    load_mcp_tools,
    mcp_server_config,
    normalize_tool_result,
)
from shipwright.integrations.mcp import call_tool as invoke_mcp_tool  # noqa: F401
