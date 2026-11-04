"""The four scoped subagents the orchestrator delegates to via the `task` tool."""

from __future__ import annotations

from importlib import resources
from typing import Any

from langchain_core.tools import BaseTool

from shipwright.config import Settings
from shipwright.middleware.tool_filter import (
    READ_ONLY_EXCLUDED,
    ExcludeToolsMiddleware,
    ReviewReport,
)
from shipwright.permissions import SUBAGENT_PERMISSIONS
from shipwright.workspace import REPO_DIR

SKILLS_SOURCE = ["/skills/"]  # skills are NOT inherited by subagents


def load_prompt(name: str, **values: Any) -> str:
    """Load `prompts/<name>.md` and fill its `{placeholders}` (literal braces are `{{ }}`)."""
    text = (resources.files("shipwright.subagents") / "prompts" / f"{name}.md").read_text(
        encoding="utf-8"
    )
    return text.format(**values)


def build_subagents(
    settings: Settings,
    *,
    knowledge_tool: BaseTool,
    web_tool: BaseTool,
    diff_tool: BaseTool,
    repo_dir: str = REPO_DIR,
    shell_repo_dir: str,
) -> list[dict[str, Any]]:
    """Build researcher, code_analyst, developer and reviewer specs.

    `repo_dir` is the virtual path for the built-in file tools; `shell_repo_dir` is the path to
    use inside `execute` commands (they differ with the local provider on Windows).
    """
    values = {
        "repo_dir": repo_dir,
        "shell_repo_dir": shell_repo_dir,
        "max_test_attempts": settings.max_test_attempts,
    }

    def read_only() -> list[ExcludeToolsMiddleware]:
        return [ExcludeToolsMiddleware(READ_ONLY_EXCLUDED)]

    return [
        {
            "name": "researcher",
            "description": (
                "Looks up internal specs/ADRs and external library or standard knowledge. "
                "Delegate ONLY when the ticket references a spec/ADR (e.g. EXPORT-CSV-v2, "
                "ADR-002) or needs external library/standard knowledge. Do NOT use it for "
                "questions about the repository code (use code_analyst) or for simple tickets "
                "that need no outside context. Returns cited facts, has no code access."
            ),
            "system_prompt": load_prompt("researcher", **values),
            "model": settings.model_light,
            "tools": [knowledge_tool, web_tool],
            "middleware": read_only(),
            "permissions": SUBAGENT_PERMISSIONS,
        },
        {
            "name": "code_analyst",
            "description": (
                "Read-only explorer of the cloned repository. Delegate BEFORE any "
                "implementation to locate the files concerned by the ticket, existing tests "
                "and conventions, and get a proposed approach. Never writes code or runs "
                "commands."
            ),
            "system_prompt": load_prompt("code_analyst", **values),
            "model": settings.model_light,
            "tools": [],
            "middleware": read_only(),
            "skills": list(SKILLS_SOURCE),
            "permissions": SUBAGENT_PERMISSIONS,
        },
        {
            "name": "developer",
            "description": (
                "Implements the change and its tests in the cloned repository and runs the "
                "test suite (the only agent allowed to write files or execute commands). "
                "Delegate once the approach is known, or again with the reviewer's findings "
                "when changes are requested. Reports STATUS: TESTS_PASSING or "
                "STATUS: TESTS_FAILING. Never commits or pushes."
            ),
            "system_prompt": load_prompt("developer", **values),
            "model": settings.model_strong,
            "tools": [],
            "skills": list(SKILLS_SOURCE),
            "permissions": SUBAGENT_PERMISSIONS,
        },
        {
            "name": "reviewer",
            "description": (
                "Read-only self-review. Delegate AFTER the developer reports passing tests: "
                "pass it the ticket's acceptance criteria verbatim; it checks each one "
                "against the workspace diff, the org policies and coding conventions and "
                "returns a structured ReviewReport (pass / changes_requested)."
            ),
            "system_prompt": load_prompt("reviewer", **values),
            "model": settings.model_light,
            "tools": [diff_tool],
            "middleware": read_only(),
            "skills": list(SKILLS_SOURCE),
            "response_format": ReviewReport,
            "permissions": SUBAGENT_PERMISSIONS,
        },
    ]
