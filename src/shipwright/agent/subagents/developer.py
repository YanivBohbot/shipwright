"""`developer`: the only agent that writes files and runs `execute` (tests) in the sandbox.

File tools and `execute` may see the repo at different paths (local shell): both are given.
"""

from deepagents import SubAgent

from shipwright.agent.backend import SKILLS_ROUTE, SUBAGENT_PERMISSIONS
from shipwright.agent.prompts.developer import DEVELOPER_PROMPT
from shipwright.core.config import Settings
from shipwright.sandbox.workspace import REPO_DIR


def build_developer(settings: Settings, shell_repo_dir: str) -> SubAgent:
    return {
        "name": "developer",
        "description": (
            "Implements the change and its tests in the repository and runs the test suite. "
            "Use it once the approach is known, or again with review feedback. Ends with "
            "STATUS: TESTS_PASSING or STATUS: TESTS_FAILING. Never commits or pushes."
        ),
        "system_prompt": DEVELOPER_PROMPT.format(
            repo_dir=REPO_DIR,
            shell_repo_dir=shell_repo_dir,
            max_test_attempts=settings.max_test_attempts,
        ),
        "model": settings.model_strong,
        "tools": [],  # no key = inherit ALL orchestrator tools (MCP writes included)
        "skills": [SKILLS_ROUTE],
        "permissions": SUBAGENT_PERMISSIONS,
    }
