"""LangGraph Server entry point (`langgraph.json` -> `make_graph`): `langgraph dev` / deployment.

Verified against langgraph-api: a one-parameter factory receives the run's `RunnableConfig`
and may be async. It is called for EVERY access (runs, state reads, schema reads), with
`configurable["__is_for_execution__"]` True only for runs. The platform injects its own
checkpointer and store, so none are passed.

Without a `thread_id` (Studio loading the schema) or for a non-execution access, a
schema-only graph is built: same structure, a workspace that is never cloned (no sandbox is
created) and services that refuse every call (no MCP / Pinecone connection).
"""

from typing import Any

from deepagents.backends import StateBackend
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph.state import CompiledStateGraph

from shipwright.agent.dependencies import Dependencies, build_dependencies
from shipwright.agent.orchestrator import build_shipwright_agent
from shipwright.core.config import Settings, get_settings
from shipwright.sandbox.workspace import Workspace

SCHEMA_ONLY_ERROR = "schema-only Shipwright graph: no service is connected (no thread_id)"


class _Refuse:
    """Stand-in for Jira / GitHub / knowledge in a schema-only graph: every call raises."""

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


def schema_only_dependencies(settings: Settings, repo: str) -> Dependencies:
    service: Any = _Refuse()
    return Dependencies(
        settings=settings,
        workspace=Workspace(StateBackend(), repo, "schema-only://never-cloned"),
        jira=service,
        github=service,
        searcher=service,
        web_tool=StructuredTool.from_function(func=_refuse_web_search, name="web_search"),
        audit_sink=service,
        model=settings.model_strong,
    )


async def make_graph(config: RunnableConfig) -> CompiledStateGraph:
    """Build the Shipwright graph for one run of the LangGraph Server."""
    settings = get_settings()
    configurable = (config or {}).get("configurable") or {}
    thread_id = configurable.get("thread_id")
    repo = configurable.get("repo") or settings.repo
    executing = configurable.get("__is_for_execution__")
    if not thread_id and executing is True:
        raise ValueError("Shipwright runs require a thread (stateless runs are not supported).")
    if not thread_id or executing is False:
        return build_shipwright_agent(schema_only_dependencies(settings, repo))
    deps = await build_dependencies(settings, thread_id=str(thread_id), repo=repo)
    return build_shipwright_agent(deps)
