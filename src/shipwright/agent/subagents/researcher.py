"""Subagent `researcher`: internal specs/ADRs (`knowledge_search`) and the web (`web_search`).

Deep Agents concept: a **subagent with its own tools and a lighter model**; read-only (no
file writes, no `execute`) and no access to the user's memory.
"""

from deepagents import SubAgent
from langchain_core.tools import BaseTool

from shipwright.agent.backend import SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.loader import load_prompt
from shipwright.core.config import Settings


def build_researcher(
    settings: Settings, *, knowledge_tool: BaseTool, web_tool: BaseTool
) -> SubAgent:
    return {
        "name": "researcher",
        "description": (
            "Looks up internal specs/ADRs and external library or standard knowledge. "
            "Delegate ONLY when the ticket references a spec/ADR (e.g. EXPORT-CSV-v2, "
            "ADR-002) or needs external library/standard knowledge. Do NOT use it for "
            "questions about the repository code (use code_analyst) or for simple tickets "
            "that need no outside context. Returns cited facts (read-only: never edits code)."
        ),
        "system_prompt": load_prompt("researcher"),
        "model": settings.model_light,
        "tools": [knowledge_tool, web_tool],
        "middleware": [ReadOnlyToolsMiddleware()],
        "permissions": SUBAGENT_PERMISSIONS,
    }
