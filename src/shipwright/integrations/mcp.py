"""MCP servers -> LangChain tools, with `MultiServerMCPClient`.

The agents receive the servers' own tools (no wrapper), in three layers:
1. **Allowlist** (`ALLOWED_TOOLS`): only the operations Shipwright needs are loaded; merge,
   delete, Confluence... never reach a model. Each subagent then gets its own subset.
2. **Human approval** (`interrupt_on_for`): every tool the server does not mark
   `readOnlyHint: true` pauses for a human before it runs (fail-safe: no annotation = approval).
3. **Authorization interceptors** (`integrations/authorization.py`): every call, including
   arguments a human edited at approval time, must stay inside the configured repo, the
   `shipwright/*` branches and the Jira project; every write is audited.

Tool names verified against github/github-mcp-server (remote) and mcp-atlassian 0.23.1.
"""

import shutil
import sys
from pathlib import Path

from langchain.agents.middleware import InterruptOnConfig
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection

from shipwright.audit import AuditSink
from shipwright.core.config import Settings
from shipwright.integrations.authorization import audit_interceptor, authorize_interceptor

GITHUB_TOOLS = (
    "list_branches",
    "list_commits",
    "list_pull_requests",
    "search_code",
    "create_branch",
    "push_files",
    "create_pull_request",
)
JIRA_TOOLS = (
    "jira_get_issue",
    "jira_search",
    "jira_get_transitions",
    "jira_add_comment",
    "jira_transition_issue",
    "jira_create_issue",
)
ALLOWED_TOOLS = GITHUB_TOOLS + JIRA_TOOLS
# mcp-atlassian groups its tools in toolsets: start only the three Shipwright uses.
JIRA_TOOLSETS = "jira_issues,jira_comments,jira_transitions"
APPROVAL = InterruptOnConfig(allowed_decisions=["approve", "edit", "reject"])


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


def is_read_only(tool: BaseTool) -> bool:
    """The server's own MCP annotation (`readOnlyHint`), kept by the adapter in `metadata`."""
    return (tool.metadata or {}).get("readOnlyHint") is True


def interrupt_on_for(tools: list[BaseTool]) -> dict[str, bool | InterruptOnConfig]:
    """`interrupt_on`: a human approves every tool that is not declared read-only."""
    return {t.name: APPROVAL for t in tools if not is_read_only(t)}


def select_tools(tools: list[BaseTool]) -> dict[str, BaseTool]:
    """`{name: tool}` for the allowlisted tools; fail loudly if one is missing."""
    by_name = {t.name: t for t in tools}
    missing = [name for name in ALLOWED_TOOLS if name not in by_name]
    if missing:
        raise RuntimeError(
            f"MCP servers do not expose: {', '.join(missing)}. Check the credentials in .env."
        )
    return {name: by_name[name] for name in ALLOWED_TOOLS}


async def load_mcp_tools(settings: Settings, audit_sink: AuditSink) -> dict[str, BaseTool]:
    """Connect to both servers and return the allowlisted tools by name.

    An MCP tool error (or an authorization refusal) comes back to the model as an error
    ToolMessage (the adapter's default `handle_tool_errors=True`), so the agent can react.
    """
    write_tools: set[str] = set()  # filled below, once the annotations are known
    client = MultiServerMCPClient(
        mcp_connections(settings),
        # Outermost first: the audit also records the writes the authorization refuses.
        tool_interceptors=[
            audit_interceptor(audit_sink, write_tools),
            authorize_interceptor(settings),
        ],
    )
    tools = select_tools(await client.get_tools())
    write_tools.update(name for name, tool in tools.items() if not is_read_only(tool))
    return tools
