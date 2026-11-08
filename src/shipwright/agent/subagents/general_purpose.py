"""Override of deepagents' default `general-purpose` subagent.

Without it, `create_deep_agent` adds a `general-purpose` subagent with ALL the orchestrator's
tools (the Jira/GitHub write tools included). Same name = replaced by this read-only helper.
"""

from deepagents import SubAgent

from shipwright.agent.backend import SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.general_purpose import GENERAL_PURPOSE_PROMPT
from shipwright.core.config import Settings


def build_general_purpose(settings: Settings) -> SubAgent:
    return {
        "name": "general-purpose",
        "description": (
            "Read-only helper for a quick factual lookup in the files. Prefer the specialised "
            "subagents; this one cannot write, run commands or use Jira/GitHub."
        ),
        "system_prompt": GENERAL_PURPOSE_PROMPT,
        "model": settings.model_light,
        "tools": [],  # no key = inherit ALL orchestrator tools (MCP writes included)
        "middleware": [ReadOnlyToolsMiddleware()],
        "permissions": SUBAGENT_PERMISSIONS,
    }
