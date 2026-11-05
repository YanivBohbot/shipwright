"""Subagent `code_analyst`: read-only explorer of the cloned repository.

Deep Agents concept: a subagent that only uses the **built-in filesystem tools** (`ls`,
`read_file`, `glob`, `grep`) on the shared backend, with **skills** (`coding-conventions`).
Skills are not inherited from the orchestrator: each subagent lists its own sources.
"""

from deepagents import SubAgent

from shipwright.agent.backend import SKILLS_ROUTE, SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.loader import load_prompt
from shipwright.core.config import Settings
from shipwright.sandbox.workspace import REPO_DIR


def build_code_analyst(settings: Settings) -> SubAgent:
    return {
        "name": "code_analyst",
        "description": (
            "Read-only explorer of the cloned repository. Delegate BEFORE any "
            "implementation to locate the files concerned by the ticket, existing tests "
            "and conventions, and get a proposed approach. Never writes code or runs "
            "commands."
        ),
        "system_prompt": load_prompt("code_analyst", repo_dir=REPO_DIR),
        "model": settings.model_light,
        "tools": [],
        "middleware": [ReadOnlyToolsMiddleware()],
        "skills": [SKILLS_ROUTE],
        "permissions": SUBAGENT_PERMISSIONS,
    }
