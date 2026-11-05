"""Subagent `reviewer`: read-only self-review returning a structured `ReviewReport`.

Deep Agents concept: a subagent with **structured output** (`response_format`): its final
answer is a validated Pydantic object, which the orchestrator copies into the PR body.
"""

from typing import Literal

from deepagents import SubAgent
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from shipwright.agent.backend import SKILLS_ROUTE, SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.loader import load_prompt
from shipwright.core.config import Settings
from shipwright.sandbox.workspace import REPO_DIR


class CriterionCheck(BaseModel):
    id: str
    met: bool
    evidence: str


class ReviewReport(BaseModel):
    """Structured self-review of the diff against the ticket's acceptance criteria."""

    verdict: Literal["pass", "changes_requested"]
    criteria: list[CriterionCheck]
    risks: list[str]
    suggestions: list[str]


def build_reviewer(settings: Settings, *, diff_tool: BaseTool) -> SubAgent:
    return {
        "name": "reviewer",
        "description": (
            "Read-only self-review. Delegate AFTER the developer reports passing tests: "
            "pass it the ticket's acceptance criteria verbatim; it checks each one "
            "against the workspace diff, the org policies and coding conventions and "
            "returns a structured ReviewReport (pass / changes_requested)."
        ),
        "system_prompt": load_prompt("reviewer", repo_dir=REPO_DIR),
        "model": settings.model_light,
        "tools": [diff_tool],
        "middleware": [ReadOnlyToolsMiddleware()],
        "skills": [SKILLS_ROUTE],
        "response_format": ReviewReport,
        "permissions": SUBAGENT_PERMISSIONS,
    }
