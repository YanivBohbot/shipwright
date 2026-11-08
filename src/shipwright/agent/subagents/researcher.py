"""`researcher`: internal specs/ADRs and the web. Read-only, light model, its own tools."""

from deepagents import SubAgent
from langchain_core.tools import BaseTool

from shipwright.agent.backend import SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.researcher import RESEARCHER_PROMPT
from shipwright.core.config import Settings


def build_researcher(settings: Settings, knowledge_tool: BaseTool, web_tool: BaseTool) -> SubAgent:
    return {
        "name": "researcher",
        "description": (
            "Finds internal specs/ADRs (e.g. EXPORT-CSV-v2, ADR-002) and external library or "
            "standard knowledge, with citations. Use it only when the ticket references a spec "
            "or needs outside knowledge; not for questions about the repository code."
        ),
        "system_prompt": RESEARCHER_PROMPT,
        "model": settings.model_light,
        "tools": [knowledge_tool, web_tool],
        "middleware": [ReadOnlyToolsMiddleware()],
        "permissions": SUBAGENT_PERMISSIONS,
    }
