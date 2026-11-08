"""THE `create_deep_agent` call. Start reading here: every argument is one Deep Agents component.

| argument          | component (file)                                                     |
|-------------------|----------------------------------------------------------------------|
| `tools`           | Jira + GitHub MCP tools (`integrations/mcp.py`) + 2 workspace tools  |
| `system_prompt`   | `prompts/orchestrator.py`                                            |
| `subagents`       | 4 specialists + read-only `general-purpose` (`subagents/`)           |
| `backend`         | sandbox + memories + policies + skills (`backend.py`)                |
| `permissions`     | read-only `/policies/` and `/skills/` (`backend.py`)                 |
| `skills`          | SKILL.md files under `/skills/` (`skills/`)                          |
| `memory`          | AGENTS.md-style files loaded into the prompt each run                |
| `interrupt_on`    | human approval of every MCP write (`hitl.py`)                        |
| `middleware`      | planning, PII, limits, retries, audit (`middleware.py`)              |
| `context_schema`  | `ShipwrightContext(user_id)` (`state.py`)                            |
| `checkpointer`, `store` | threads + long-term memory in MongoDB (`core/persistence.py`)  |

`create_deep_agent` returns a compiled LangGraph graph: there is no StateGraph to write.
"""

from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from shipwright.agent.backend import MEMORY_FILES, PERMISSIONS, SKILLS_ROUTE, build_backend
from shipwright.agent.hitl import INTERRUPT_ON
from shipwright.agent.middleware import build_middleware
from shipwright.agent.prompts.orchestrator import ORCHESTRATOR_PROMPT
from shipwright.agent.state import ShipwrightContext
from shipwright.agent.subagents.code_analyst import build_code_analyst
from shipwright.agent.subagents.developer import build_developer
from shipwright.agent.subagents.general_purpose import build_general_purpose
from shipwright.agent.subagents.researcher import build_researcher
from shipwright.agent.subagents.reviewer import build_reviewer
from shipwright.agent.tools.knowledge_tools import build_knowledge_tool
from shipwright.agent.tools.web_tools import build_web_tool
from shipwright.agent.tools.workspace_tools import build_workspace_tools
from shipwright.audit import AuditSink, build_audit_sink
from shipwright.core.config import Settings
from shipwright.knowledge.search import build_searcher
from shipwright.sandbox.workspace import REPO_DIR, shell_repo_dir

# The graph `create_deep_agent` returns. Its context type parameter is the `context_schema`
# (a bare `CompiledStateGraph` means context `None`, hence the explicit alias).
ShipwrightGraph = CompiledStateGraph[Any, ShipwrightContext | None, Any, Any]


def build_shipwright_agent(
    settings: Settings,
    *,
    mcp_tools: list[BaseTool],
    sandbox: SandboxBackendProtocol,
    model: str | BaseChatModel | None = None,
    audit_sink: AuditSink | None = None,
    checkpointer: Any = None,
    store: Any = None,
) -> ShipwrightGraph:

    show_diff, get_changed_files = build_workspace_tools(sandbox)

    prompt = ORCHESTRATOR_PROMPT.format(
        jira_project=settings.jira_project,
        jira_url=settings.jira_base_url,
        repo=settings.shipwright_repo,
        owner=settings.repo_owner,
        repo_name=settings.repo_name,
        repo_dir=REPO_DIR,
        max_test_attempts=settings.max_test_attempts,
        max_review_cycles=settings.max_review_cycles,
    )

    return create_deep_agent(
        name="shipwright",
        model=model or settings.model_strong,
        tools=[*mcp_tools, show_diff, get_changed_files],
        system_prompt=prompt,
        subagents=[
            build_general_purpose(settings),
            build_researcher(
                settings, build_knowledge_tool(build_searcher(settings)), build_web_tool(settings)
            ),
            build_code_analyst(settings),
            build_developer(settings, shell_repo_dir(sandbox)),
            build_reviewer(settings, show_diff),
        ],
        backend=build_backend(sandbox),
        permissions=PERMISSIONS,
        skills=[SKILLS_ROUTE],
        memory=MEMORY_FILES,
        interrupt_on=INTERRUPT_ON,
        middleware=build_middleware(settings, audit_sink or build_audit_sink(settings)),
        context_schema=ShipwrightContext,
        checkpointer=checkpointer,
        store=store,
    )
