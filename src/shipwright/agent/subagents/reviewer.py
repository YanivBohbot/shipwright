"""`reviewer`: read-only self-review; answers with a `ReviewReport` (structured output)."""

from typing import Literal

from deepagents import SubAgent
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from shipwright.agent.backend import SKILLS_ROUTE, SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.reviewer import REVIEWER_PROMPT
from shipwright.core.config import Settings
from shipwright.sandbox.workspace import REPO_DIR


class CriterionCheck(BaseModel):
    id: str
    met: bool
    evidence: str


class ReviewReport(BaseModel):
    """Self-review of the diff against the ticket's acceptance criteria."""

    verdict: Literal["pass", "changes_requested"]
    criteria: list[CriterionCheck]
    risks: list[str]
    suggestions: list[str]


def build_reviewer(settings: Settings, diff_tool: BaseTool) -> SubAgent:
    return {
        "name": "reviewer",
        "description": (
            "Read-only review of the diff once the tests pass. Give it the acceptance criteria "
            "verbatim; it returns a ReviewReport (verdict pass / changes_requested)."
        ),
        "system_prompt": REVIEWER_PROMPT.format(repo_dir=REPO_DIR),
        "model": settings.model_light,
        "tools": [diff_tool],
        "middleware": [ReadOnlyToolsMiddleware()],
        "skills": [SKILLS_ROUTE],
        "response_format": ReviewReport,
        "permissions": SUBAGENT_PERMISSIONS,
    }
