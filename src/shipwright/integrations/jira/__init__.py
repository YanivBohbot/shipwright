"""Jira gateway: protocol (`gateway`), live MCP (`mcp_jira`), local demo tickets (`local_jira`).

The re-exports below keep the v1 import path `shipwright.integrations.jira` working until
phase B removes v1.
"""

from shipwright.integrations.jira.gateway import JiraGateway  # noqa: F401
from shipwright.integrations.jira.mcp_jira import (  # noqa: F401
    SUBTASK_ISSUE_TYPE,
    TOOL_ADD_COMMENT,
    TOOL_CREATE_ISSUE,
    TOOL_GET_ISSUE,
    TOOL_GET_TRANSITIONS,
    TOOL_TRANSITION_ISSUE,
    McpJira,
)

McpJiraGateway = McpJira  # v1 name
