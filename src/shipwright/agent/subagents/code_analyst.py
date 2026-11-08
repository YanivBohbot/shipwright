"""`code_analyst`: read-only explorer of the cloned repo (built-in file tools + skills)."""

from deepagents import SubAgent

from shipwright.agent.backend import SKILLS_ROUTE, SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.code_analyst import CODE_ANALYST_PROMPT
from shipwright.core.config import Settings
from shipwright.sandbox.workspace import REPO_DIR


def build_code_analyst(settings: Settings) -> SubAgent:
    return {
        "name": "code_analyst",
        "description": (
            "Read-only explorer of the repository. Use it before any implementation to find "
            "the files concerned, the existing tests and conventions, and an approach."
        ),
        "system_prompt": CODE_ANALYST_PROMPT.format(repo_dir=REPO_DIR),
        "model": settings.model_light,
        "tools": [],  # no key = inherit ALL orchestrator tools (MCP writes included)
        "middleware": [ReadOnlyToolsMiddleware()],
        "skills": [SKILLS_ROUTE],
        "permissions": SUBAGENT_PERMISSIONS,
    }
