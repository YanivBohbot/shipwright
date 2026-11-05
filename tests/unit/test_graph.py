"""`graph.make_graph` (LangGraph Server factory) and langgraph.json."""

import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

import shipwright.agent.orchestrator as orch
import shipwright.graph as graph_mod
from shipwright.agent.state import ShipwrightState
from tests.fakes import make_settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-tests")
    monkeypatch.setattr(
        "langchain.agents.middleware.model_fallback.init_chat_model",
        lambda name: GenericFakeChatModel(messages=iter([])),
    )
    monkeypatch.setattr(graph_mod, "get_settings", lambda: make_settings(mode="live"))


@pytest.fixture
def spy(monkeypatch):
    calls: list[dict[str, Any]] = []
    real = orch.create_deep_agent

    def recorder(**kwargs: Any):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(orch, "create_deep_agent", recorder)
    return calls


async def _never(*args: Any, **kwargs: Any):
    raise AssertionError("a schema-only graph must not open a sandbox or connect any service")


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"configurable": {}},
        {"configurable": {"thread_id": "t1", "__is_for_execution__": False}},
    ],
)
async def test_schema_only_graph_creates_no_sandbox(monkeypatch, spy, config):
    monkeypatch.setattr(graph_mod, "build_dependencies", _never)
    assert await graph_mod.make_graph(config) is not None
    kw = spy[0]
    assert kw["checkpointer"] is None and kw["store"] is None  # the platform provides them
    assert kw["state_schema"] is ShipwrightState and kw["skills"] == ["/skills/"]
    assert [t.name for t in kw["tools"]][-1] == "publish_pull_request"


async def test_threadless_execution_is_refused(monkeypatch):
    monkeypatch.setattr(graph_mod, "build_dependencies", _never)
    with pytest.raises(ValueError, match="require a thread"):
        await graph_mod.make_graph({"configurable": {"__is_for_execution__": True}})


async def test_schema_only_services_refuse_every_call():
    deps = graph_mod.schema_only_dependencies(make_settings(), "acme/x")
    with pytest.raises(RuntimeError, match="schema-only"):
        await deps.jira.get_issue("SHIP-1")
    with pytest.raises(RuntimeError, match="schema-only"):
        deps.web_tool.invoke({"query": "x"})


async def test_execution_builds_dependencies_for_the_thread(monkeypatch):
    seen: list[dict[str, Any]] = []
    built = object()

    async def fake_build(settings, *, thread_id, repo=None, **kw):
        seen.append({"thread_id": thread_id, "repo": repo, **kw})
        return "deps"

    monkeypatch.setattr(graph_mod, "build_dependencies", fake_build)
    monkeypatch.setattr(graph_mod, "build_shipwright_agent", lambda deps: (deps, built))
    result = await graph_mod.make_graph({"configurable": {"thread_id": "t-42", "repo": "acme/o"}})
    assert result == ("deps", built)
    assert seen == [{"thread_id": "t-42", "repo": "acme/o"}]  # no checkpointer/store passed
    await graph_mod.make_graph({"configurable": {"thread_id": "t-1"}})
    assert seen[-1]["repo"] == "acme/taskboard-api"  # default: settings.repo


def test_langgraph_json_points_to_make_graph():
    data = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    assert data["graphs"] == {"shipwright": "./src/shipwright/graph.py:make_graph"}
    assert data["dependencies"] == ["."] and data["env"] == ".env"
    assert data["checkpointer"] == {"backend": "mongo"}
    path, _, attr = data["graphs"]["shipwright"].partition(":")
    assert (ROOT / path).is_file() and attr == "make_graph"
    assert inspect.iscoroutinefunction(graph_mod.make_graph)
    from langgraph_api._factory_utils import _classify_factory

    hook = _classify_factory(graph_mod.make_graph)
    assert hook({"configurable": {"thread_id": "x"}}, object()) == {
        "config": {"configurable": {"thread_id": "x"}}
    }
