"""`researcher`: internal specs/ADRs, related Jira tickets and the web. Read-only."""

from deepagents import SubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from shipwright.agent.backend import SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.researcher import RESEARCHER_PROMPT
from shipwright.core.config import Settings

MCP_TOOLS = ("jira_get_issue", "jira_search")  # Jira, read-only


def build_researcher(
    settings: Settings,
    model: str | BaseChatModel,
    mcp: dict[str, BaseTool],
    knowledge_tool: BaseTool,
    web_tool: BaseTool,
) -> SubAgent:
    return {
        "name": "researcher",
        "description": (
            "Finds internal specs/ADRs (e.g. EXPORT-CSV-v2, ADR-002), related Jira tickets and "
            "external library or standard knowledge, with citations. Use it only when the "
            "ticket references a spec, a linked ticket or needs outside knowledge."
        ),
        "system_prompt": RESEARCHER_PROMPT.format(jira_project=settings.jira_project),
        "model": model,
        "tools": [knowledge_tool, web_tool, *(mcp[name] for name in MCP_TOOLS)],
        "middleware": [ReadOnlyToolsMiddleware()],
        "permissions": SUBAGENT_PERMISSIONS,
    }
