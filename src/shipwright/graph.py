"""Graph factory for `langgraph.json` (LangSmith Deployment / `langgraph dev`).

Verified against langgraph-api 0.17.1 (`_factory_utils._classify_factory`, `graph.get_graph`):
a one-parameter factory not annotated `ServerRuntime` receives the run's `RunnableConfig`; a
coroutine result is awaited. The factory is called for EVERY access (runs, but also state reads,
assistant/schema reads), with `configurable["__is_for_execution__"]` True only for
`threads.create_run`. The platform injects its own checkpointer and store into the returned
graph, so none are passed here.

Without a `thread_id` (schema loading in Studio) or for a non-execution access, a schema-only
graph is built: same structure, a workspace that is never cloned (no sandbox is created) and
stub services that refuse any call (no MCP / Pinecone connection).
"""

from __future__ import annotations

import functools
from typing import Any

from deepagents.backends import StateBackend
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph.state import CompiledStateGraph

from shipwright.agent import Dependencies, build_agent
from shipwright.config import Settings, get_settings
from shipwright.integrations.github import McpGitHubGateway
from shipwright.integrations.jira import McpJiraGateway
from shipwright.integrations.mcp_client import load_mcp_tools
from shipwright.knowledge.ingest import build_vector_store
from shipwright.knowledge.search import KnowledgeSearcher
from shipwright.middleware.audit import MemoryAuditSink, MongoAuditSink
from shipwright.tools.research import make_web_search_tool
from shipwright.workspace import Workspace, open_workspace

SCHEMA_ONLY_ERROR = "schema-only Shipwright graph: no service is connected (no thread_id)"


class _SchemaOnlyService:
    """Stand-in for Jira/GitHub/knowledge services in a schema-only graph: refuses every call."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):  # copy/pickle/introspection probes
            raise AttributeError(name)

        async def refuse(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(SCHEMA_ONLY_ERROR)

        return refuse


def _refuse_web_search(query: str) -> str:
    """Search the web for external/public library knowledge.

    Args:
        query: What to search for.
    """
    raise RuntimeError(SCHEMA_ONLY_ERROR)


def schema_only_dependencies() -> Dependencies:
    service: Any = _SchemaOnlyService()
    web_tool: BaseTool = StructuredTool.from_function(func=_refuse_web_search, name="web_search")
    return Dependencies(
        jira=service,
        github=service,
        searcher=service,
        web_tool=web_tool,
        audit_sink=MemoryAuditSink(),
    )


async def build_live_dependencies(settings: Settings) -> Dependencies:
    """Real services: MCP gateways (raw MCP tools stay inside them), Pinecone, Tavily, Mongo."""
    mcp = await load_mcp_tools(settings)
    return Dependencies(
        jira=McpJiraGateway(mcp["atlassian"]),
        github=McpGitHubGateway(mcp["github"]),
        searcher=KnowledgeSearcher(build_vector_store(settings)),
        web_tool=make_web_search_tool(settings),
        audit_sink=shared_audit_sink(settings.mongodb_uri.get_secret_value(), settings.mongodb_db),
    )


@functools.cache
def shared_audit_sink(uri: str, db: str) -> MongoAuditSink:
    """One audit sink (hence one lazily created AsyncMongoClient) per process, not per run.

    The factory runs for every execution on a long-lived Agent Server; a sink per run would
    leak one never-closed client (connection pool + monitor tasks) each time.
    """
    return MongoAuditSink(uri, db)


async def make_graph(config: RunnableConfig) -> CompiledStateGraph:
    """Build the Shipwright graph for one run (entry point of `langgraph.json`)."""
    settings = get_settings()
    configurable = (config or {}).get("configurable") or {}
    thread_id = configurable.get("thread_id")
    repo = configurable.get("repo") or settings.shipwright_repo
    if not thread_id and configurable.get("__is_for_execution__") is True:
        raise ValueError("Shipwright runs require a thread (stateless runs are not supported).")
    if not thread_id or configurable.get("__is_for_execution__") is False:
        workspace = Workspace(StateBackend(), repo)  # never cloned: no sandbox exists
        return build_agent(settings, schema_only_dependencies(), workspace)

    workspace = await open_workspace(settings, thread_id=str(thread_id), repo=repo)
    await workspace.ensure_cloned()
    deps = await build_live_dependencies(settings)
    return build_agent(settings, deps, workspace)
