"""`github_publisher`: publishes the changes as one PR with the GitHub MCP write tools.

Every write (`create_branch`, `push_files`, `create_pull_request`) pauses for a human through
the inherited `interrupt_on`, and is checked by the authorization interceptor. `push_files`
is also checked against the sandbox: the files pushed must be exactly the changed files.
"""

from typing import Any

from deepagents import SubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from shipwright.agent.backend import SKILLS_ROUTE, SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import PushMatchesWorkspaceMiddleware, ReadOnlyToolsMiddleware
from shipwright.agent.prompts.github_publisher import GITHUB_PUBLISHER_PROMPT
from shipwright.core.config import Settings

MCP_TOOLS = ("list_branches", "create_branch", "push_files", "create_pull_request")


def build_github_publisher(
    settings: Settings,
    model: str | BaseChatModel,
    mcp: dict[str, BaseTool],
    changed_files_tool: BaseTool,
    sandbox: Any,
) -> SubAgent:
    return {
        "name": "github_publisher",
        "description": (
            "Publishes the developer's changes as one pull request (branch, commit, PR). Give "
            "it the ticket key, PR title, PR body, draft true/false and the commit message. "
            "Answers 'PR: <url>' or 'REJECTED: <reason>'."
        ),
        "system_prompt": GITHUB_PUBLISHER_PROMPT.format(
            owner=settings.repo_owner, repo_name=settings.repo_name
        ),
        "model": model,
        "tools": [changed_files_tool, *(mcp[name] for name in MCP_TOOLS)],
        "middleware": [
            ReadOnlyToolsMiddleware(),  # MCP only: no file writes, no shell
            PushMatchesWorkspaceMiddleware(sandbox),
        ],
        "skills": [SKILLS_ROUTE],
        "permissions": SUBAGENT_PERMISSIONS,
    }
