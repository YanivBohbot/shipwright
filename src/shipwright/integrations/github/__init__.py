"""GitHub gateway: protocol (`gateway`), live MCP (`mcp_github`), local bare repo (`local_github`).

The re-exports below keep the v1 import path `shipwright.integrations.github` working until
phase B removes v1.
"""

from shipwright.integrations.github.gateway import FileChange, GitHubGateway  # noqa: F401
from shipwright.integrations.github.mcp_github import (  # noqa: F401
    TOOL_CREATE_BRANCH,
    TOOL_CREATE_PULL_REQUEST,
    TOOL_DELETE_FILE,
    TOOL_LIST_BRANCHES,
    TOOL_LIST_PULL_REQUESTS,
    TOOL_PUSH_FILES,
    McpGitHub,
)

McpGitHubGateway = McpGitHub  # v1 name
