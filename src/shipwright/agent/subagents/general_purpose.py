"""Override of deepagents' default `general-purpose` subagent.

Without it, `create_deep_agent` adds a `general-purpose` subagent with ALL the orchestrator's
tools. Same name = replaced by this read-only helper (built-in file tools only).
"""

from deepagents import SubAgent
from langchain_core.language_models import BaseChatModel

from shipwright.agent.backend import SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.general_purpose import GENERAL_PURPOSE_PROMPT


def build_general_purpose(model: str | BaseChatModel) -> SubAgent:
    return {
        "name": "general-purpose",
        "description": (
            "Read-only helper for a quick factual lookup in the files. Prefer the specialised "
            "subagents; this one cannot write, run commands or use Jira/GitHub."
        ),
        "system_prompt": GENERAL_PURPOSE_PROMPT,
        "model": model,
        "tools": [],  # no key = inherit ALL orchestrator tools
        "middleware": [ReadOnlyToolsMiddleware()],
        "permissions": SUBAGENT_PERMISSIONS,
    }
