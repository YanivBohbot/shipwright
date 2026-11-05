"""Override of the `general-purpose` subagent that deepagents adds automatically: no tools.

Deep Agents concept: without this override, `create_deep_agent` adds a `general-purpose`
subagent that inherits ALL orchestrator tools (publish_pull_request, update_jira_ticket...).
Supplying a subagent with that name replaces it.
"""

from deepagents import SubAgent

from shipwright.agent.backend import SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.core.config import Settings

PROMPT = (
    "You are a read-only helper of Shipwright. Answer the question you were given using the "
    "read-only file tools (`ls`, `read_file`, `glob`, `grep`) only. You cannot write files, run "
    "commands, publish or talk to Jira. Return a concise, factual answer."
)


def build_general_purpose(settings: Settings) -> SubAgent:
    return {
        "name": "general-purpose",
        "description": (
            "Read-only helper for a quick factual lookup in the workspace files. Prefer the "
            "specialised subagents (code_analyst, researcher, developer, reviewer); this one "
            "cannot write files, run commands, publish or use Jira."
        ),
        "system_prompt": PROMPT,
        "model": settings.model_light,
        "tools": [],
        "middleware": [ReadOnlyToolsMiddleware()],
        "permissions": SUBAGENT_PERMISSIONS,
    }
