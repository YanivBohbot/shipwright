# Shipwright — learn Deep Agents by reading one real agent

Shipwright is a [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) agent that
turns **one Jira ticket into one reviewed pull request**: it reads the ticket, plans, delegates to
specialist subagents, changes the code and runs the tests in a sandbox, self-reviews, then opens
the PR and updates the ticket. **Every Jira and GitHub write stops for a human first** (approve,
edit or reject), through Deep Agents' `interrupt_on`.

The project exists to show **where each Deep Agents component goes in a real codebase**. Every
file has one job and a docstring naming the concept it demonstrates.

## Setup

1. `uv sync`, then `cp .env.example .env` and fill it. Required: `OPENAI_API_KEY`, `GITHUB_PAT`,
   `SHIPWRIGHT_REPO`, `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`, `MONGODB_URI`. Optional:
   `LANGSMITH_*` (traces), `PINECONE_API_KEY` (knowledge base), `TAVILY_API_KEY` (web search).
2. The target repository: a **public** GitHub repo holding `demo/taskboard-api` (a small FastAPI
   service with a known bug), e.g. `YanivBohbot/taskboard-api`. `demo/taskboard-api` is
   gitignored here; to create your own copy: `cd demo/taskboard-api && gh repo create
   <you>/taskboard-api --public --source . --push`, then set `SHIPWRIGHT_REPO=<you>/taskboard-api`.
3. The Jira project (`JIRA_PROJECT`, default `SHIP`) with the demo tickets:
   `uv run python scripts/seed_jira.py` (idempotent; keys in `demo/seeded.json`).
4. Optional: `uv run python scripts/ingest_knowledge.py` fills the Pinecone index with the specs
   and ADRs of `knowledge/` (without it, keyword search over the same files).

## Run

```bash
uv run shipwright run SHIP-1 --user alice
```

**What you will see** for `SHIP-1` (empty assignee → HTTP 500):
1. `jira_get_issue`, a plan with `write_todos`.
2. `task` → `code_analyst` (code + recent commits / open PRs), then `developer` (edits + tests
   in the sandbox), then `reviewer` (structured verdict; on `changes_requested` back to the
   developer).
3. `task` → `github_publisher`: one approval per write, `create_branch`, `push_files` (you see
   the exact files pushed), `create_pull_request`.
4. `task` → `jira_agent`: approvals for `jira_add_comment` and `jira_transition_issue` (In Review).

Answer `approve`, `edit` (new JSON arguments) or `reject` (your reason goes back to the
subagent; rejecting `push_files` with a reason = review feedback for the developer).

`SHIP-4` is ambiguous ("improve performance", no acceptance criteria): the `jira_agent` posts
the questions and the run stops. `demo/tickets.yaml` has 7 scenarios. A paused run
resumes with `--thread <id>` (threads are in MongoDB).

The sandbox (`SANDBOX_PROVIDER`): `local` (default) is a `LocalShellBackend` under
`tmp/sandboxes/<thread>/`: **not isolated**, the developer's commands run on your machine (with a
minimal environment, no secrets). `langsmith` is an isolated LangSmith sandbox; the feature must
be enabled on your LangSmith organization.

## Reading order

1. **`src/shipwright/main.py`** — the CLI: loads the MCP tools, opens the sandbox, streams the run,
   and answers each approval in the terminal (`Command(resume={"decisions": [...]})`).
2. **`src/shipwright/agent/orchestrator.py`** — **the single `create_deep_agent(...)` call**. Every
   argument is one component; the table below says where each one lives. It returns a compiled
   LangGraph graph: there is no `StateGraph` to write.
3. The modules it imports, in the order of the arguments.

## Where each Deep Agents component lives

| Component | `create_deep_agent` arg | File |
|---|---|---|
| Model (strong / light, fallback) | `model` | `core/config.py` |
| System prompts (Python constants) | `system_prompt` | `agent/prompts/<agent>.py` |
| Planning (`write_todos`) | `middleware` | `TodoListMiddleware` in `agent/middleware.py` |
| MCP tools (Jira, GitHub) | `tools`, subagent `tools` | `integrations/mcp.py`: `MultiServerMCPClient` + an allowlist by tool name |
| MCP authorization + audit | `tool_interceptors` of the MCP client | `integrations/authorization.py` |
| Custom tools | subagent `tools` | `agent/tools/workspace_tools.py` (diff, changed files), `knowledge_tools.py`, `web_tools.py` |
| Subagents (`task`), each with its own tools | `subagents` | `agent/subagents/` (table below); `general_purpose.py` overrides the built-in one |
| Structured subagent output | subagent `response_format` | `agent/subagents/reviewer.py` (`ReviewReport`) |
| Human-in-the-loop | `interrupt_on` (inherited by subagents) | `integrations/mcp.interrupt_on_for` (from MCP `readOnlyHint`); `agent/hitl.py` answers in the terminal |
| Backend (CompositeBackend) | `backend` | `agent/backend.py`: `/` sandbox, `/memories/` per user, `/policies/`, `/skills/` |
| Sandbox | default route of the backend | `sandbox/workspace.py` (local shell or LangSmith, clone, diff) |
| Permissions | `permissions` | `agent/backend.py` (`PERMISSIONS`, `SUBAGENT_PERMISSIONS`) |
| Skills (SKILL.md, loaded on demand) | `skills=["/skills/"]` | `agent/skills/*/SKILL.md` |
| Memory (always in the prompt) | `memory` | `/memories/preferences.md` (per user), `/policies/POLICIES.md` |
| Middleware | `middleware` | `agent/middleware.py`: planning, PII, call limit, retries, fallback, read-only filter |
| Context schema | `context_schema` | `agent/state.py` (`ShipwrightContext(user_id)`) |
| Checkpointer / store | `checkpointer`, `store` | `core/persistence.py` (MongoDB) |
| Deployment | `langgraph.json` → `graph.py:make_graph` | `src/shipwright/graph.py`, `docs/deployment.md` |

| Subagent | Its tools (* = human approval) |
|---|---|
| `researcher` | `knowledge_search`, `web_search`, `jira_get_issue`, `jira_search` |
| `code_analyst` | file tools (read) + `list_commits`, `list_pull_requests`, `search_code` |
| `developer` | file tools + `execute` (tests in the sandbox) |
| `reviewer` | file tools (read) + `show_workspace_diff` |
| `github_publisher` | `get_changed_files`, `list_branches`, `create_branch`*, `push_files`*, `create_pull_request`* |
| `jira_agent` | `jira_get_issue`, `jira_get_transitions`, `jira_add_comment`*, `jira_transition_issue`*, `jira_create_issue`* |

The orchestrator itself only has `jira_get_issue` (and `task`, `write_todos`). The built-in
file tools come with the backend; a subagent's `tools` key lists the extra ones (a subagent
without that key would inherit every orchestrator tool).

```
src/shipwright/
  main.py  graph.py  audit.py
  core/          config.py  persistence.py
  domain/        tickets.py
  integrations/  mcp.py  authorization.py
  sandbox/       workspace.py
  knowledge/     ingest.py  search.py
  agent/         orchestrator.py  state.py  backend.py  middleware.py  hitl.py
                 subagents/  prompts/  tools/  skills/  policies/
```

## Security model

Three layers around the MCP tools (`integrations/mcp.py`):
1. **Allowlist.** Only 13 named MCP tools are loaded; each subagent gets its own subset.
2. **Human approval.** Every tool the server does not mark `readOnlyHint: true` is in
   `interrupt_on` (fail-safe: no annotation = approval), inherited by all subagents. The human
   approves the exact arguments: approving `push_files` approves the exact files pushed.
3. **Authorization interceptor.** Every MCP call, after approval (so including arguments a
   human edited), must target the configured repo, write only `shipwright/*` branches, open PRs
   into `main`, and touch only the configured Jira project; otherwise it is refused
   (`NOT AUTHORIZED: ...`) and nothing is sent.

Also:
- **Read-only where it should be.** Only the developer can write files or run commands; the
  built-in `general-purpose` subagent is replaced by a read-only one.
- `/policies/` and `/skills/` are write-denied for everyone.
- **No credentials in the sandbox.** The clone cannot push (publishing is MCP only); the local
  shell gets a minimal environment without API keys or tokens.
- **Audit trail**: an MCP interceptor records every write call of every agent, allowed or
  refused, in MongoDB (`audit_log`), with secrets redacted.

Limits: file deletions cannot be published (`push_files` writes files only), and the local shell
is not an isolation boundary.

## Tests

```bash
uv run pytest            # unit tests: scripted model, fake MCP tools, real git for the local shell
uv run ruff check .
```

Evals: `evals/` keeps the dataset and the evaluators; see `evals/README.md`.
