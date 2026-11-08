"""LangGraph Server entry point (`langgraph.json` -> `make_graph`): `langgraph dev` / deployment.

The platform calls this factory with the run's config and injects its own checkpointer and
store. A run gets the thread's sandbox (cloned repo); any other access (Studio reading the
schema) gets an in-state backend, so no sandbox is created for it.
"""

from typing import cast

from deepagents.backends import StateBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.runnables import RunnableConfig

from shipwright.agent.orchestrator import ShipwrightGraph, build_shipwright_agent
from shipwright.core.config import get_settings
from shipwright.integrations.mcp import load_mcp_tools
from shipwright.sandbox.workspace import clone_repo, open_sandbox


async def make_graph(config: RunnableConfig) -> ShipwrightGraph:
    settings = get_settings()
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    if thread_id and configurable.get("__is_for_execution__"):
        sandbox = await open_sandbox(settings, str(thread_id))
        await clone_repo(sandbox, settings.shipwright_repo)
    else:
        # Schema reads only: nothing executes, so a backend without `execute` is enough.
        sandbox = cast(SandboxBackendProtocol, StateBackend())
    mcp_tools = await load_mcp_tools(settings)
    return build_shipwright_agent(settings, mcp_tools=mcp_tools, sandbox=sandbox)
