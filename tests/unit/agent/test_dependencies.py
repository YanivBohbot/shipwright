"""`build_dependencies`: what each run mode wires (local demo vs live services)."""

from typing import Any

from langchain_core.tools import StructuredTool

import shipwright.agent.dependencies as dep_mod
import shipwright.integrations.mcp as mcp_mod
from shipwright.agent.dependencies import build_dependencies
from shipwright.agent.tools.web_tools import DISABLED
from shipwright.audit import JsonlAuditSink, MongoAuditSink
from shipwright.integrations.github.local_github import LocalGitHub
from shipwright.integrations.github.mcp_github import McpGitHub
from shipwright.integrations.jira.local_jira import LocalJira
from shipwright.integrations.jira.mcp_jira import McpJira
from shipwright.knowledge.search import LocalKeywordSearcher
from tests.fakes import FakeWorkspace, make_settings


async def test_local_mode_wires_the_offline_demo(local_settings):
    deps = await build_dependencies(local_settings, thread_id="t-local", store="S")
    assert isinstance(deps.jira, LocalJira) and isinstance(deps.github, LocalGitHub)
    assert isinstance(deps.searcher, LocalKeywordSearcher)
    assert isinstance(deps.audit_sink, JsonlAuditSink)
    assert await deps.web_tool.ainvoke({"query": "x"}) == DISABLED
    assert deps.workspace.repo == "local/taskboard-api"
    assert await deps.workspace.base_sha() == await deps.github.get_branch_sha(
        "local/taskboard-api", "main"
    )
    assert (deps.checkpointer, deps.store, deps.model) == (None, "S", local_settings.model_strong)
    assert (await deps.jira.get_issue("SHIP-1")).summary == "Fix a"


async def test_live_mode_wires_mcp_gateways_and_one_mongo_sink(monkeypatch):
    def stub(name: str) -> StructuredTool:
        return StructuredTool.from_function(func=lambda: "x", name=name, description=name)

    opened: list[dict[str, Any]] = []

    async def fake_open(settings, *, thread_id, repo=None):
        opened.append({"thread_id": thread_id, "repo": repo})
        ws = FakeWorkspace()

        async def ensure_cloned():
            return False

        ws.ensure_cloned = ensure_cloned
        return ws

    async def fake_mcp(settings):
        return {"github": {"x": stub("x")}, "atlassian": {"y": stub("y")}}

    monkeypatch.setattr(dep_mod, "open_workspace", fake_open)
    monkeypatch.setattr(mcp_mod, "load_mcp_tools", fake_mcp)
    settings = make_settings(mode="live")
    first = await build_dependencies(settings, thread_id="t1", repo="acme/other")
    second = await build_dependencies(settings, thread_id="t2")
    assert isinstance(first.jira, McpJira) and isinstance(first.github, McpGitHub)
    assert isinstance(first.audit_sink, MongoAuditSink)
    assert first.audit_sink is second.audit_sink  # one Mongo client per process
    assert opened == [{"thread_id": "t1", "repo": "acme/other"}, {"thread_id": "t2", "repo": None}]
