"""Assemble the Shipwright orchestrator with `create_deep_agent`.

Verified against deepagents 0.7.32:
- `create_deep_agent(model, tools, *, system_prompt, middleware, subagents, skills, memory,
  permissions, backend, interrupt_on, response_format, state_schema, context_schema,
  checkpointer, store, debug, name, cache)`.
- `memory=` tolerates a missing file: `MemoryMiddleware` skips `file_not_found` responses (a
  first-time user has no `/memories/preferences.md`; StoreBackend reports `file_not_found`).
- The default stack has NO `write_todos` (only some harness profiles add `TodoListMiddleware`),
  so it is added explicitly.
- A `general-purpose` subagent is auto-added unless one with that name is supplied, and it
  inherits ALL orchestrator tools (publish_pull_request, update_jira_ticket...) with the
  orchestrator's permissions. Shipwright supplies a tool-less, read-only override instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from shipwright.backends import build_backend
from shipwright.config import Settings
from shipwright.context import ShipwrightContext, ShipwrightState
from shipwright.integrations.github import GitHubGateway
from shipwright.integrations.jira import JiraGateway
from shipwright.knowledge.search import KnowledgeSearcher
from shipwright.middleware.audit import AuditSink
from shipwright.middleware.stack import build_orchestrator_middleware
from shipwright.middleware.tool_filter import READ_ONLY_EXCLUDED, ExcludeToolsMiddleware
from shipwright.permissions import PERMISSIONS, SUBAGENT_PERMISSIONS
from shipwright.subagents.specs import SKILLS_SOURCE, build_subagents
from shipwright.tools import HITL_INTERRUPTS
from shipwright.tools.jira_tools import make_jira_tools
from shipwright.tools.publish import make_publish_tool
from shipwright.tools.research import make_knowledge_search_tool
from shipwright.tools.review_gate import make_review_gate_tool
from shipwright.tools.workspace_tools import make_diff_tool
from shipwright.workspace import REPO_DIR, Workspace

AGENT_NAME = "shipwright"
MEMORY_SOURCES = ["/memories/preferences.md", "/policies/POLICIES.md"]
ORCHESTRATOR_TOOL_NAMES = (
    "read_jira_ticket",
    "update_jira_ticket",
    "create_jira_subtasks",
    "show_workspace_diff",
    "request_human_review",
    "publish_pull_request",
)

GENERAL_PURPOSE_PROMPT = (
    "You are a read-only helper of Shipwright. Answer the question you were given using the "
    "read-only file tools (`ls`, `read_file`, `glob`, `grep`) only. You cannot write files, run "
    "commands, publish or talk to Jira. Return a concise, factual answer."
)


@dataclass
class Dependencies:
    """External services the orchestrator's tools talk to (real or fake)."""

    jira: JiraGateway
    github: GitHubGateway
    searcher: KnowledgeSearcher
    web_tool: BaseTool
    audit_sink: AuditSink


def orchestrator_prompt(settings: Settings, workspace: Workspace) -> str:
    """`prompts/orchestrator.md` with its placeholders filled."""
    text = (resources.files("shipwright") / "prompts" / "orchestrator.md").read_text(
        encoding="utf-8"
    )
    return text.format(
        jira_url=settings.jira_url.rstrip("/"),
        jira_project=settings.jira_project,
        max_review_cycles=settings.max_review_cycles,
        max_test_attempts=settings.max_test_attempts,
        repo_dir=REPO_DIR,
        shell_repo_dir=workspace.shell_repo_dir,
    )


def locked_general_purpose(settings: Settings) -> dict[str, Any]:
    """Override of the auto-added `general-purpose` subagent: no tools, read-only, no memory."""
    return {
        "name": "general-purpose",
        "description": (
            "Read-only helper for a quick factual lookup in the workspace files. Prefer the "
            "specialised subagents (code_analyst, researcher, developer, reviewer); this one "
            "cannot write files, run commands, publish or use Jira."
        ),
        "system_prompt": GENERAL_PURPOSE_PROMPT,
        "model": settings.model_light,
        "tools": [],
        "middleware": [ExcludeToolsMiddleware(READ_ONLY_EXCLUDED)],
        "permissions": SUBAGENT_PERMISSIONS,
    }


def build_agent(
    settings: Settings,
    deps: Dependencies,
    workspace: Workspace,
    *,
    checkpointer: Any = None,
    store: Any = None,
    model: str | BaseChatModel | None = None,
) -> CompiledStateGraph:
    """Build the orchestrator graph.

    Args:
        settings: Application settings.
        deps: Jira/GitHub gateways, knowledge searcher, web tool and audit sink.
        workspace: The thread's workspace (its backend becomes the default backend).
        checkpointer: Local runs only; a deployment's platform injects its own.
        store: Local runs only (needed by the `/memories/` and `/policies/` routes).
        model: Override of `settings.model_strong` (tests pass a scripted fake model).
    """
    jira_tools = {t.name: t for t in make_jira_tools(deps.jira, settings)}
    diff_tool = make_diff_tool(workspace)
    tools = [
        jira_tools["read_jira_ticket"],
        jira_tools["update_jira_ticket"],
        jira_tools["create_jira_subtasks"],
        diff_tool,
        make_review_gate_tool(workspace, jira_project=settings.jira_project),
        make_publish_tool(workspace, deps.github, settings),
    ]
    subagents = [
        locked_general_purpose(settings),
        *build_subagents(
            settings,
            knowledge_tool=make_knowledge_search_tool(deps.searcher),
            web_tool=deps.web_tool,
            diff_tool=diff_tool,
            repo_dir=REPO_DIR,
            shell_repo_dir=workspace.shell_repo_dir,
        ),
    ]
    return create_deep_agent(
        name=AGENT_NAME,
        model=model if model is not None else settings.model_strong,
        tools=tools,
        system_prompt=orchestrator_prompt(settings, workspace),
        subagents=subagents,
        backend=build_backend(workspace, settings),
        permissions=PERMISSIONS,
        skills=list(SKILLS_SOURCE),
        memory=list(MEMORY_SOURCES),
        interrupt_on=HITL_INTERRUPTS,
        middleware=[
            TodoListMiddleware(),
            *build_orchestrator_middleware(settings, deps.audit_sink),
        ],
        context_schema=ShipwrightContext,
        state_schema=ShipwrightState,
        checkpointer=checkpointer,
        store=store,
    )
