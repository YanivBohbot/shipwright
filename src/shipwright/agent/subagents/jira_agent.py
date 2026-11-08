"""`jira_agent`: the Jira writes (comments, sub-tasks, transition) with the Jira MCP tools.

Every write pauses for a human through the inherited `interrupt_on`, and is checked by the
authorization interceptor (only issues of the configured project).
"""

from deepagents import SubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from shipwright.agent.backend import SKILLS_ROUTE, SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.jira_agent import JIRA_AGENT_PROMPT
from shipwright.core.config import Settings

MCP_TOOLS = (
    "jira_get_issue",
    "jira_get_transitions",
    "jira_add_comment",
    "jira_transition_issue",
    "jira_create_issue",
)


def build_jira_agent(
    settings: Settings, model: str | BaseChatModel, mcp: dict[str, BaseTool]
) -> SubAgent:
    return {
        "name": "jira_agent",
        "description": (
            "Writes to Jira: clarification questions, the sub-task split, or the post-PR "
            "comment with the 'In Review' transition. Give it the ticket key and the exact "
            "texts. Answers what it did, or 'REJECTED: <reason>'."
        ),
        "system_prompt": JIRA_AGENT_PROMPT.format(jira_project=settings.jira_project),
        "model": model,
        "tools": [mcp[name] for name in MCP_TOOLS],
        "middleware": [ReadOnlyToolsMiddleware()],  # MCP only: no file writes, no shell
        "skills": [SKILLS_ROUTE],
        "permissions": SUBAGENT_PERMISSIONS,
    }
