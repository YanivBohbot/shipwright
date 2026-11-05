"""Everything the orchestrator needs, picked for the run mode (`SHIPWRIGHT_MODE`).

| dependency   | local (default, demo)                      | live                          |
|--------------|--------------------------------------------|-------------------------------|
| jira         | `LocalJira` (demo/tickets.yaml + JSON)     | `McpJira` (mcp-atlassian)     |
| github       | `LocalGitHub` (bare repo, PR = Markdown)   | `McpGitHub` (GitHub MCP)      |
| workspace    | `LocalShellBackend` under tmp/             | LangSmith sandbox             |
| searcher     | Pinecone if PINECONE_API_KEY, else keywords| same                          |
| web_tool     | Tavily if TAVILY_API_KEY, else disabled    | same                          |
| audit_sink   | tmp/audit.jsonl                            | MongoDB `audit_log`           |

Both modes go through the same gateway protocols, so the agent code is identical. The
checkpointer and store come from the caller (`open_persistence` in main.py; the platform
injects its own under `langgraph.json`, so graph.py passes None).
"""

from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from shipwright.agent.tools.web_tools import build_web_tool
from shipwright.audit import AuditSink, build_audit_sink
from shipwright.core.config import Settings
from shipwright.integrations.github.gateway import GitHubGateway
from shipwright.integrations.jira.gateway import JiraGateway
from shipwright.knowledge.search import build_searcher
from shipwright.sandbox.providers import open_workspace
from shipwright.sandbox.workspace import Workspace


@dataclass
class Dependencies:
    settings: Settings
    workspace: Workspace
    jira: JiraGateway
    github: GitHubGateway
    searcher: Any  # `.search(query, k)`: KnowledgeSearcher | LocalKeywordSearcher
    web_tool: BaseTool
    audit_sink: AuditSink
    model: str | BaseChatModel  # the orchestrator's model (tests pass a scripted fake)
    checkpointer: Any = None
    store: Any = None


async def _gateways(settings: Settings) -> tuple[JiraGateway, GitHubGateway]:
    if settings.mode == "local":
        from shipwright.integrations.github.local_github import LocalGitHub
        from shipwright.integrations.jira.local_jira import LocalJira

        jira = LocalJira(settings.local_tickets, settings.local_jira_state, settings.jira_project)
        return jira, LocalGitHub(settings.local_remote, settings.local_prs_dir, settings.repo)
    from shipwright.integrations.github.mcp_github import McpGitHub
    from shipwright.integrations.jira.mcp_jira import McpJira
    from shipwright.integrations.mcp import load_mcp_tools

    tools = await load_mcp_tools(settings)  # raw MCP tools stay inside the gateways
    return McpJira(tools["atlassian"]), McpGitHub(tools["github"])


async def build_dependencies(
    settings: Settings,
    *,
    thread_id: str,
    checkpointer: Any = None,
    store: Any = None,
    repo: str | None = None,
) -> Dependencies:
    """Open the thread's workspace (cloned now) and connect the services of `settings.mode`."""
    if settings.mode == "local":
        from shipwright.integrations.github.local_github import ensure_bare_remote

        ensure_bare_remote(settings.local_source_repo, settings.local_remote)
    workspace = await open_workspace(settings, thread_id=thread_id, repo=repo)
    await workspace.ensure_cloned()
    jira, github = await _gateways(settings)
    return Dependencies(
        settings=settings,
        workspace=workspace,
        jira=jira,
        github=github,
        searcher=build_searcher(settings),
        web_tool=build_web_tool(settings),
        audit_sink=build_audit_sink(settings),
        model=settings.model_strong,
        checkpointer=checkpointer,
        store=store,
    )
