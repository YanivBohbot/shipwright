"""The packaged skills: front matter, the PR template and the Jira workflow."""

from pathlib import Path

import pytest
import yaml

from shipwright.agent.backend import AGENT_DIR

SKILLS_DIR = AGENT_DIR / "skills"

EXPECTED = ["coding-conventions", "jira-workflow", "pr-template", "testing-guidelines"]

PR_SECTIONS = [
    "## Summary",
    "## Linked ticket",
    "## Acceptance criteria",
    "## Tests",
    "## Self-review",
    "## Notes",
]


def _split(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML front matter"
    _, front, body = text.split("---\n", 2)
    return yaml.safe_load(front), body


def test_all_expected_skills_exist():
    assert sorted(p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md")) == EXPECTED


@pytest.mark.parametrize("name", EXPECTED)
def test_skills_have_valid_frontmatter(name):
    meta, body = _split(SKILLS_DIR / name / "SKILL.md")
    assert meta["name"] == name
    assert isinstance(meta["description"], str) and len(meta["description"]) >= 40
    assert body.strip()


def test_pr_template_sections():
    text = (SKILLS_DIR / "pr-template" / "SKILL.md").read_text(encoding="utf-8")
    for heading in PR_SECTIONS:
        assert heading in text
    positions = [text.index(h) for h in PR_SECTIONS]
    assert positions == sorted(positions)
    for tool in ["get_changed_files", "create_branch", "push_files", "create_pull_request"]:
        assert tool in text


def test_jira_workflow_mentions_tools_and_transition():
    text = (SKILLS_DIR / "jira-workflow" / "SKILL.md").read_text(encoding="utf-8")
    for needle in [
        "jira_add_comment",
        "jira_create_issue",
        "jira_get_transitions",
        "jira_transition_issue",
        "In Review",
    ]:
        assert needle in text


def test_testing_guidelines_has_command_and_naming():
    text = (SKILLS_DIR / "testing-guidelines" / "SKILL.md").read_text(encoding="utf-8")
    assert "uv run pytest -q" in text
    assert "test_ac<n>_" in text


def test_pr_template_verdict_wording():
    text = (SKILLS_DIR / "pr-template" / "SKILL.md").read_text(encoding="utf-8")
    assert "pass | changes_requested" in text and "APPROVE | CHANGES REQUESTED" not in text


def test_skills_only_name_tools_the_agent_has():
    """Every tool a skill tells the agent to call exists (no stale custom tool names)."""
    from shipwright.integrations.mcp import ALLOWED_TOOLS

    for name in ["update_jira_ticket", "create_jira_subtasks", "publish_pull_request"]:
        for skill in SKILLS_DIR.glob("*/SKILL.md"):
            assert name not in skill.read_text(encoding="utf-8"), (skill, name)
    assert {"jira_add_comment", "push_files"} <= set(ALLOWED_TOOLS)
