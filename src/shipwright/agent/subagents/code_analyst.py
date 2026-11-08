"""`code_analyst`: read-only explorer of the cloned repo, plus GitHub history and open PRs."""

from deepagents import SubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from shipwright.agent.backend import SKILLS_ROUTE, SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.code_analyst import CODE_ANALYST_PROMPT
from shipwright.core.config import Settings
from shipwright.sandbox.workspace import REPO_DIR

MCP_TOOLS = ("list_commits", "list_pull_requests", "search_code")  # GitHub, read-only


def build_code_analyst(
    settings: Settings, model: str | BaseChatModel, mcp: dict[str, BaseTool]
) -> SubAgent:
    return {
        "name": "code_analyst",
        "description": (
            "Read-only explorer of the repository. Use it before any implementation to find "
            "the files concerned, the tests and conventions, recent commits / open PRs on "
            "those files, and an approach."
        ),
        "system_prompt": CODE_ANALYST_PROMPT.format(
            repo_dir=REPO_DIR,
            repo=settings.shipwright_repo,
            owner=settings.repo_owner,
            repo_name=settings.repo_name,
        ),
        "model": model,
        "tools": [mcp[name] for name in MCP_TOOLS],
        "middleware": [ReadOnlyToolsMiddleware()],
        "skills": [SKILLS_ROUTE],
        "permissions": SUBAGENT_PERMISSIONS,
    }
