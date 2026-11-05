"""THE `create_deep_agent` call. Start reading here: every argument is one Deep Agents component.

| argument                      | component (file)                                             |
|-------------------------------|--------------------------------------------------------------|
| `tools`                       | custom wrapper tools, never raw MCP tools (`tools/`)          |
| `system_prompt`               | `prompts/orchestrator.md`                                     |
| `subagents`                   | 4 specialists + locked `general-purpose` (`subagents/`)       |
| `backend`, `permissions`      | CompositeBackend + read-only routes (`backend.py`)            |
| `skills`                      | SKILL.md files under `/skills/` (`skills/`)                   |
| `memory`                      | AGENTS.md-style files loaded into the prompt each run         |
| `interrupt_on`                | human approval of every Jira/GitHub write (`hitl.py`)         |
| `middleware`                  | planning, PII, limits, retries, audit... (`middleware.py`)    |
| `state_schema`, `context_schema` | `ShipwrightState`, `ShipwrightContext` (`state.py`)        |
| `checkpointer`, `store`       | threads + long-term memory (`core/persistence.py`)            |

Verified against deepagents 0.7.32 (`create_deep_agent(model, tools, *, system_prompt,
middleware, subagents, skills, memory, permissions, backend, interrupt_on, response_format,
state_schema, context_schema, checkpointer, store, debug, name, cache)`).
"""

from deepagents import create_deep_agent
from langgraph.graph.state import CompiledStateGraph

from shipwright.agent.backend import PERMISSIONS, SKILLS_ROUTE, build_backend
from shipwright.agent.dependencies import Dependencies
from shipwright.agent.hitl import INTERRUPT_ON
from shipwright.agent.middleware import build_middleware
from shipwright.agent.prompts.loader import load_prompt
from shipwright.agent.state import ShipwrightContext, ShipwrightState
from shipwright.agent.subagents.code_analyst import build_code_analyst
from shipwright.agent.subagents.developer import build_developer
from shipwright.agent.subagents.general_purpose import build_general_purpose
from shipwright.agent.subagents.researcher import build_researcher
from shipwright.agent.subagents.reviewer import build_reviewer
from shipwright.agent.tools.github_tools import build_github_tools
from shipwright.agent.tools.jira_tools import build_jira_tools
from shipwright.agent.tools.knowledge_tools import build_knowledge_tool
from shipwright.agent.tools.review_tools import build_review_tools
from shipwright.sandbox.workspace import REPO_DIR

AGENT_NAME = "shipwright"
# `memory=` tolerates a missing file (a new user has no preferences yet).
MEMORY = ["/memories/preferences.md", "/policies/POLICIES.md"]


def build_shipwright_agent(deps: Dependencies) -> CompiledStateGraph:
    settings, workspace = deps.settings, deps.workspace
    project = settings.jira_project
    show_diff, review_gate = build_review_tools(workspace, project)
    return create_deep_agent(
        name=AGENT_NAME,
        model=deps.model,
        tools=[
            *build_jira_tools(deps.jira, project),
            show_diff,
            review_gate,
            *build_github_tools(workspace, deps.github, project),
        ],
        system_prompt=load_prompt(
            "orchestrator",
            jira_url=settings.jira_base_url,
            jira_project=project,
            max_review_cycles=settings.max_review_cycles,
            max_test_attempts=settings.max_test_attempts,
            repo_dir=REPO_DIR,
            shell_repo_dir=workspace.shell_repo_dir,
        ),
        subagents=[
            build_general_purpose(settings),
            build_researcher(
                settings,
                knowledge_tool=build_knowledge_tool(deps.searcher),
                web_tool=deps.web_tool,
            ),
            build_code_analyst(settings),
            build_developer(settings, shell_repo_dir=workspace.shell_repo_dir),
            build_reviewer(settings, diff_tool=show_diff),
        ],
        backend=build_backend(workspace, settings),
        permissions=PERMISSIONS,
        skills=[SKILLS_ROUTE],
        memory=list(MEMORY),
        interrupt_on=INTERRUPT_ON,
        middleware=build_middleware(settings, deps.audit_sink),
        state_schema=ShipwrightState,
        context_schema=ShipwrightContext,
        checkpointer=deps.checkpointer,
        store=deps.store,
    )
