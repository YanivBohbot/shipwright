"""THE `create_deep_agent` call. Start reading here: every argument is one Deep Agents component.

| argument          | component (file)                                                     |
|-------------------|----------------------------------------------------------------------|
| `tools`           | the orchestrator's own: `jira_get_issue` (read the ticket)           |
| `system_prompt`   | `prompts/orchestrator.py`                                            |
| `subagents`       | 6 specialists, each with its own tools (`subagents/`)                |
| `backend`         | sandbox + memories + policies + skills (`backend.py`)                |
| `permissions`     | read-only `/policies/` and `/skills/` (`backend.py`)                 |
| `skills`          | SKILL.md files under `/skills/` (`skills/`)                          |
| `memory`          | AGENTS.md-style files loaded into the prompt each run                |
| `interrupt_on`    | human approval of every non-read-only MCP tool, inherited by the     |
|                   | subagents (`integrations/mcp.py`; answered in `hitl.py`)             |
| `middleware`      | planning, PII, limits, retries, fallback (`middleware.py`)           |
| `context_schema`  | `ShipwrightContext(user_id)` (`state.py`)                            |
| `checkpointer`, `store` | threads + long-term memory in MongoDB (`core/persistence.py`)  |

| subagent          | its tools                                                            |
|-------------------|----------------------------------------------------------------------|
| `researcher`      | knowledge_search, web_search, jira_get_issue, jira_search            |
| `code_analyst`    | file tools (read) + list_commits, list_pull_requests, search_code    |
| `developer`       | file tools + execute (tests in the sandbox)                          |
| `reviewer`        | file tools (read) + show_workspace_diff; structured `ReviewReport`   |
| `github_publisher`| get_changed_files, list_branches, create_branch*, push_files*,       |
|                   | create_pull_request*                                                 |
| `jira_agent`      | jira_get_issue, jira_get_transitions, jira_add_comment*,             |
|                   | jira_transition_issue*, jira_create_issue*                           |
(* = paused for human approval). `create_deep_agent` returns a compiled LangGraph graph: there
is no StateGraph to write.
"""

from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from shipwright.agent.backend import MEMORY_FILES, PERMISSIONS, SKILLS_ROUTE, build_backend
from shipwright.agent.middleware import build_middleware
from shipwright.agent.prompts.orchestrator import ORCHESTRATOR_PROMPT
from shipwright.agent.state import ShipwrightContext
from shipwright.agent.subagents.code_analyst import build_code_analyst
from shipwright.agent.subagents.developer import build_developer
from shipwright.agent.subagents.general_purpose import build_general_purpose
from shipwright.agent.subagents.github_publisher import build_github_publisher
from shipwright.agent.subagents.jira_agent import build_jira_agent
from shipwright.agent.subagents.researcher import build_researcher
from shipwright.agent.subagents.reviewer import build_reviewer
from shipwright.agent.tools.knowledge_tools import build_knowledge_tool
from shipwright.agent.tools.web_tools import build_web_tool
from shipwright.agent.tools.workspace_tools import build_workspace_tools
from shipwright.core.config import Settings
from shipwright.integrations.mcp import interrupt_on_for
from shipwright.knowledge.search import build_searcher
from shipwright.sandbox.workspace import REPO_DIR, shell_repo_dir


def build_shipwright_agent(
    settings: Settings,
    *,
    mcp: dict[str, BaseTool],
    sandbox: SandboxBackendProtocol,
    model: str | BaseChatModel | None = None,
    checkpointer: Any = None,
    store: Any = None,
):
    """`model` replaces every model (tests pass a scripted one); else the settings' models."""
    strong = model or settings.model_strong
    light = model or settings.model_light
    show_diff, get_changed_files = build_workspace_tools(sandbox)
    knowledge = build_knowledge_tool(build_searcher(settings))
    prompt = ORCHESTRATOR_PROMPT.format(
        jira_project=settings.jira_project,
        jira_url=settings.jira_base_url,
        repo=settings.shipwright_repo,
        repo_dir=REPO_DIR,
        max_test_attempts=settings.max_test_attempts,
        max_review_cycles=settings.max_review_cycles,
    )
    return create_deep_agent(
        name="shipwright",
        model=strong,
        tools=[mcp["jira_get_issue"]],
        system_prompt=prompt,
        subagents=[
            build_general_purpose(light),
            build_researcher(settings, light, mcp, knowledge, build_web_tool(settings)),
            build_code_analyst(settings, light, mcp),
            build_developer(settings, strong, shell_repo_dir(sandbox)),
            build_reviewer(light, show_diff),
            build_github_publisher(settings, light, mcp, get_changed_files),
            build_jira_agent(settings, light, mcp),
        ],
        backend=build_backend(sandbox),
        permissions=PERMISSIONS,
        skills=[SKILLS_ROUTE],
        memory=MEMORY_FILES,
        # One rule for every agent: declarative subagents inherit the top-level interrupt_on.
        interrupt_on=interrupt_on_for(list(mcp.values())),
        middleware=build_middleware(settings),
        context_schema=ShipwrightContext,
        checkpointer=checkpointer,
        store=store,
    )
