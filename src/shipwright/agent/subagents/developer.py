"""Subagent `developer`: the ONLY agent that writes files and runs `execute` (tests) in the sandbox.

Deep Agents concept: a subagent using the **sandbox backend** (`execute`) with the strong model.
File tools see the virtual path `repo_dir`; shell commands need `shell_repo_dir` (they differ
for the local shell backend on Windows), so the prompt gets both.
"""

from deepagents import SubAgent

from shipwright.agent.backend import SKILLS_ROUTE, SUBAGENT_PERMISSIONS
from shipwright.agent.prompts.loader import load_prompt
from shipwright.core.config import Settings
from shipwright.sandbox.workspace import REPO_DIR


def build_developer(settings: Settings, *, shell_repo_dir: str) -> SubAgent:
    prompt = load_prompt(
        "developer",
        repo_dir=REPO_DIR,
        shell_repo_dir=shell_repo_dir,
        max_test_attempts=settings.max_test_attempts,
    )
    return {
        "name": "developer",
        "description": (
            "Implements the change and its tests in the cloned repository and runs the "
            "test suite (the only agent allowed to write files or execute commands). "
            "Delegate once the approach is known, or again with the reviewer's findings "
            "when changes are requested. Reports STATUS: TESTS_PASSING or "
            "STATUS: TESTS_FAILING. Never commits or pushes."
        ),
        "system_prompt": prompt,
        "model": settings.model_strong,
        "tools": [],
        "skills": [SKILLS_ROUTE],
        "permissions": SUBAGENT_PERMISSIONS,
    }
