"""The five subagent specs and their prompts."""

import re

import pytest
from langchain_core.tools import StructuredTool

from shipwright.agent.backend import SUBAGENT_PERMISSIONS
from shipwright.agent.middleware import ReadOnlyToolsMiddleware
from shipwright.agent.prompts.loader import load_prompt
from shipwright.agent.subagents.code_analyst import build_code_analyst
from shipwright.agent.subagents.developer import build_developer
from shipwright.agent.subagents.general_purpose import build_general_purpose
from shipwright.agent.subagents.researcher import build_researcher
from shipwright.agent.subagents.reviewer import ReviewReport, build_reviewer
from tests.fakes import make_settings


def _t(name):
    return StructuredTool.from_function(func=lambda: "x", name=name, description=name)


def _specs(**settings_kw):
    s = make_settings(**settings_kw)
    specs = [
        build_general_purpose(s),
        build_researcher(s, knowledge_tool=_t("k"), web_tool=_t("w")),
        build_code_analyst(s),
        build_developer(s, shell_repo_dir="C:/ws/repo"),
        build_reviewer(s, diff_tool=_t("d")),
    ]
    return s, {sp["name"]: sp for sp in specs}


def _read_only(spec) -> bool:
    return any(isinstance(m, ReadOnlyToolsMiddleware) for m in spec.get("middleware", []))


def test_subagent_specs_shape():
    s, specs = _specs()
    assert specs["reviewer"]["response_format"] is ReviewReport
    assert specs["developer"]["model"] == s.model_strong
    for n in ("general-purpose", "researcher", "code_analyst", "reviewer"):
        assert specs[n]["model"] == s.model_light and _read_only(specs[n])
    assert not _read_only(specs["developer"])
    assert all(sp["permissions"] == SUBAGENT_PERMISSIONS for sp in specs.values())
    assert [t.name for t in specs["researcher"]["tools"]] == ["k", "w"]
    assert [t.name for t in specs["reviewer"]["tools"]] == ["d"]
    for n in ("general-purpose", "code_analyst", "developer"):
        assert specs[n]["tools"] == []
    assert "skills" not in specs["researcher"] and "skills" not in specs["general-purpose"]
    for n in ("code_analyst", "developer", "reviewer"):
        assert specs[n]["skills"] == ["/skills/"]
    assert all(sp["description"] for sp in specs.values())
    assert "ADR" in specs["researcher"]["description"]


def test_developer_prompt_gets_attempts_and_both_paths():
    _, specs = _specs(max_test_attempts=5)
    dev = specs["developer"]["system_prompt"]
    assert "at most 5" in dev and "/workspace/repo" in dev and "C:/ws/repo" in dev
    assert "STATUS: TESTS_PASSING" in dev and "STATUS: TESTS_FAILING" in dev
    assert "/policies/POLICIES.md" in dev and "testing-guidelines" in dev
    assert "git commit" in dev and "git push" in dev


@pytest.mark.parametrize(
    "name", ["orchestrator", "researcher", "code_analyst", "developer", "reviewer"]
)
def test_prompts_format_without_leftover_placeholders(name):
    text = load_prompt(
        name,
        repo_dir="/r",
        shell_repo_dir="/s",
        max_test_attempts=3,
        max_review_cycles=2,
        jira_url="https://j",
        jira_project="SHIP",
    )
    assert not re.search(r"\{[A-Za-z_]*\}", text) and text.strip()


def test_orchestrator_prompt_carry_overs():
    text = load_prompt(
        "orchestrator",
        repo_dir="/r",
        shell_repo_dir="/s",
        max_test_attempts=3,
        max_review_cycles=2,
        jira_url="https://j",
        jira_project="SHIP",
    )
    assert "https://j/browse/" in text
    assert "Only after `request_human_review` returned APPROVED" in text
    assert "changelog_entry" in text and "shipwright/<key>-rN" in text
    assert "Supersedes" in text and "`pass` or `changes_requested`" in text
