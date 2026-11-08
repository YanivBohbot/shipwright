"""MCP servers -> LangChain tools, with `MultiServerMCPClient`.

The agent receives the servers' own tools (no wrapper). Least privilege is an allowlist by
tool name: only the operations Shipwright needs are kept, everything else the servers expose
(merge, delete, Confluence...) never reaches the model. Every write tool is listed in
`WRITE_TOOLS`, which `agent/hitl.py` turns into `interrupt_on` (human approval).

Tool names verified against github/github-mcp-server (remote) and mcp-atlassian 0.23.1.
"""

import shutil
import sys
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection

from shipwright.core.config import Settings

GITHUB_READ_TOOLS = ("list_branches",)
GITHUB_WRITE_TOOLS = ("create_branch", "push_files", "create_pull_request")
JIRA_READ_TOOLS = ("jira_get_issue", "jira_get_transitions")
JIRA_WRITE_TOOLS = ("jira_add_comment", "jira_transition_issue", "jira_create_issue")

ALLOWED_TOOLS = GITHUB_READ_TOOLS + GITHUB_WRITE_TOOLS + JIRA_READ_TOOLS + JIRA_WRITE_TOOLS
WRITE_TOOLS = GITHUB_WRITE_TOOLS + JIRA_WRITE_TOOLS
READ_TOOLS = GITHUB_READ_TOOLS + JIRA_READ_TOOLS

# mcp-atlassian groups its tools in toolsets: start only the three Shipwright uses.
JIRA_TOOLSETS = "jira_issues,jira_comments,jira_transitions"


def _mcp_atlassian_command() -> str:
    """Absolute path of `mcp-atlassian` (the venv's Scripts/bin dir may not be on PATH)."""
    found = shutil.which("mcp-atlassian")
    if found:
        return found
    exe = "mcp-atlassian.exe" if sys.platform == "win32" else "mcp-atlassian"
    return str(Path(sys.executable).parent / exe)


def mcp_connections(settings: Settings) -> dict[str, Connection]:
    """`MultiServerMCPClient` connections: GitHub's remote server (HTTP) and mcp-atlassian."""
    return {
        "github": {
            "transport": "streamable_http",
            "url": settings.github_mcp_url,
            "headers": {"Authorization": f"Bearer {settings.github_pat.get_secret_value()}"},
        },
        "atlassian": {
            "transport": "stdio",
            "command": _mcp_atlassian_command(),
            "args": [],
            "env": {
                "JIRA_URL": settings.jira_url,
                "JIRA_USERNAME": settings.jira_username,
                "JIRA_API_TOKEN": settings.jira_api_token.get_secret_value(),
                "TOOLSETS": JIRA_TOOLSETS,
            },
        },
    }


def select_tools(tools: list[BaseTool], allowed: tuple[str, ...] = ALLOWED_TOOLS) -> list[BaseTool]:
    """Keep the allowlisted tools, in allowlist order; fail loudly if one is missing."""
    by_name = {t.name: t for t in tools}
    missing = [name for name in allowed if name not in by_name]
    if missing:
        raise RuntimeError(
            f"MCP servers do not expose: {', '.join(missing)}. Check the credentials in .env "
            "(mcp-atlassian hides the Jira tools when JIRA_URL/USERNAME/API_TOKEN are missing)."
        )
    return [by_name[name] for name in allowed]


async def load_mcp_tools(settings: Settings) -> list[BaseTool]:
    """Connect to both servers and return the allowlisted tools.

    An MCP tool error comes back to the model as an error ToolMessage (the adapter's
    default `handle_tool_errors=True`), so the agent can read it and react.
    """
    client = MultiServerMCPClient(mcp_connections(settings))
    return select_tools(await client.get_tools())
