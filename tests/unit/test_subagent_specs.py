import re

import pytest
from langchain_core.tools import StructuredTool

from shipwright.middleware.tool_filter import ExcludeToolsMiddleware, ReviewReport
from shipwright.permissions import SUBAGENT_PERMISSIONS
from shipwright.subagents.specs import build_subagents, load_prompt
from tests.fakes import settings_stub


def _t(name):
    return StructuredTool.from_function(func=lambda: "x", name=name, description=name)


def _specs(**kw):
    kw.setdefault("shell_repo_dir", "C:/ws/repo")
    return {
        s["name"]: s
        for s in build_subagents(
            settings_stub(), knowledge_tool=_t("k"), web_tool=_t("w"), diff_tool=_t("d"), **kw
        )
    }


def test_subagent_specs_shape():
    specs = _specs()
    s = settings_stub()
    assert set(specs) == {"researcher", "code_analyst", "developer", "reviewer"}
    assert specs["reviewer"]["response_format"] is ReviewReport
    assert specs["developer"]["model"] == s.model_strong
    for n in ("researcher", "code_analyst", "reviewer"):
        assert specs[n]["model"] == s.model_light
        assert any(isinstance(m, ExcludeToolsMiddleware) for m in specs[n]["middleware"])
    assert not any(
        isinstance(m, ExcludeToolsMiddleware) for m in specs["developer"].get("middleware", [])
    )
    assert "3" in specs["developer"]["system_prompt"]
    assert all(sp["permissions"] == SUBAGENT_PERMISSIONS for sp in specs.values())
    assert [t.name for t in specs["researcher"]["tools"]] == ["k", "w"]
    assert [t.name for t in specs["reviewer"]["tools"]] == ["d"]
    assert specs["code_analyst"]["tools"] == [] and specs["developer"]["tools"] == []
    assert "skills" not in specs["researcher"]
    for n in ("code_analyst", "developer", "reviewer"):
        assert specs[n]["skills"] == ["/skills/"]
    assert all(sp["description"] for sp in specs.values())


def test_max_test_attempts_and_paths_injected():
    s = settings_stub(max_test_attempts=5)
    specs = {
        x["name"]: x
        for x in build_subagents(
            s,
            knowledge_tool=_t("k"),
            web_tool=_t("w"),
            diff_tool=_t("d"),
            repo_dir="/workspace/repo",
            shell_repo_dir="C:/ws/repo",
        )
    }
    dev = specs["developer"]["system_prompt"]
    assert "at most 5" in dev
    assert "/workspace/repo" in dev and "C:/ws/repo" in dev
    assert "STATUS: TESTS_PASSING" in dev and "STATUS: TESTS_FAILING" in dev
    assert "/policies/POLICIES.md" in dev
    assert "testing-guidelines" in dev and "coding-conventions" in dev
    assert "git commit" in dev and "git push" in dev


@pytest.mark.parametrize("name", ["researcher", "code_analyst", "developer", "reviewer"])
def test_prompts_format_without_leftover_placeholders(name):
    text = load_prompt(name, repo_dir="/r", shell_repo_dir="/s", max_test_attempts=3)
    assert not re.search(r"\{[A-Za-z_]*\}", text)
    assert text.strip()


def test_description_routing_hints():
    specs = _specs()
    assert "ADR" in specs["researcher"]["description"]
    assert "only when" in specs["researcher"]["description"].lower()
