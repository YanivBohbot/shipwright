# Shipwright — learn Deep Agents by reading one real agent

Shipwright is a [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) agent that
turns **one Jira ticket into one reviewed pull request**: it reads the ticket, plans, delegates to
specialist subagents, changes the code and runs the tests in a sandbox, self-reviews, then **stops
and asks a human**. Nothing is written to GitHub or Jira without an approval, and the published PR
contains exactly the diff the human approved.

The project exists to show **where each Deep Agents component goes in a real codebase**. Every
file has one job and a docstring naming the concept it demonstrates.

## Run the demo (local mode, only an OpenAI key)

```bash
uv sync
cp .env.example .env          # set OPENAI_API_KEY; optional: LANGSMITH_* for traces
uv run shipwright run SHIP-1 --user alice
```

Local mode (`SHIPWRIGHT_MODE=local`, the default) replaces every external service with a local one
behind the same interface, so the agent code is identical to live mode:

| | local (demo) | live |
|---|---|---|
| Jira | `demo/tickets.yaml` → writes to `demo/.local_jira.json` | Atlassian MCP (`mcp-atlassian`) |
| GitHub | bare repo `demo/.local_remote.git`; the "PR" is `demo/.local_prs/shipwright/<key>.md` | GitHub MCP |
| Sandbox | `LocalShellBackend` under `tmp/` (scrubbed env, **not** a security boundary) | `LangSmithSandbox` |
| Checkpointer / store | in memory | MongoDB |
| Knowledge | Pinecone if the index exists, else keyword search over `knowledge/` | Pinecone |
| Web search | Tavily if `TAVILY_API_KEY`, else disabled | Tavily |

The target repo is `demo/taskboard-api/` (a small FastAPI service with a known bug). It must exist
as a git repository on `main`.

**What you will see** for `SHIP-1` (empty assignee → HTTP 500):
1. `read_jira_ticket`, a plan with `write_todos`.
2. `task` → `code_analyst` (read-only exploration), then `developer` (edits + `uv run pytest -q`
   in the sandbox), then `reviewer` (structured verdict; on `changes_requested` the loop goes back to
   the developer).
3. **Review gate**: the diff, the test output and the reviewer report are printed. Type `approve`.
4. `publish_pull_request` approval (`approve`) → branch `shipwright/ship-1` pushed to the local
   remote and the PR file written.
5. `update_jira_ticket` approval (`approve`) → comment + `In Review`.

`uv run shipwright run SHIP-4` is an ambiguous ticket ("improve performance", no acceptance
criteria): the agent posts clarifying questions on Jira and stops, no code, no PR.
`demo/tickets.yaml` has 7 scenarios (bugfix, spec-driven feature, ambiguous, too big, ...).

Reset the demo state: delete `demo/.local_remote.git`, `demo/.local_prs/`, `demo/.local_jira.json`
(otherwise a re-run of a ticket publishes a fresh `-r2` branch that "supersedes" the first).

Traces: with `LANGSMITH_TRACING=true` and `LANGSMITH_PROJECT=shipwright`, every run is in LangSmith,
including each subagent and tool call. Pinecone: `uv run python scripts/ingest_knowledge.py` creates
and fills the index (otherwise local keyword search is used).

## Reading order

1. **`src/shipwright/main.py`** — the CLI: builds the dependencies, streams the run, and answers
   interrupts in the terminal (`Command(resume=...)`).
2. **`src/shipwright/agent/orchestrator.py`** — **the single `create_deep_agent(...)` call**. Every
   argument is one component; the table below says where each one lives.
3. The modules it imports, in the order of the arguments.

## Where each Deep Agents component lives

| Component | `create_deep_agent` arg | File |
|---|---|---|
| Model (strong / light, fallback) | `model` | `core/config.py`, `agent/dependencies.py` |
| System prompt (Markdown) | `system_prompt` | `agent/prompts/orchestrator.md`, `agent/prompts/loader.py` |
| Planning (`write_todos`) | built in | prompt asks for it; visible in the CLI output |
| Subagents (`task`) | `subagents` | `agent/subagents/researcher.py`, `code_analyst.py`, `developer.py`, `reviewer.py`; `general_purpose.py` overrides the built-in one (no tools) |
| Structured subagent output | subagent `response_format` | `agent/subagents/reviewer.py` (`ReviewReport`) |
| Custom tools | `tools` | `agent/tools/jira_tools.py`, `review_tools.py`, `github_tools.py`, `knowledge_tools.py`, `web_tools.py` |
| MCP | wrapped by gateways, raw MCP tools never reach a model | `integrations/mcp.py`, `integrations/jira/mcp_jira.py`, `integrations/github/mcp_github.py` |
| Backend (CompositeBackend) | `backend` | `agent/backend.py`: `/` sandbox, `/memories/` per user, `/policies/` per org, `/skills/` read-only |
| Sandbox | default route of the backend | `sandbox/providers.py` (local / LangSmith), `sandbox/workspace.py` |
| Permissions | `permissions` | `agent/backend.py` (`PERMISSIONS`, `SUBAGENT_PERMISSIONS`) |
| Skills (SKILL.md, loaded on demand) | `skills=["/skills/"]` | `agent/skills/*/SKILL.md` |
| Memory (always in the prompt) | `memory` | `/memories/preferences.md` (per user), `/policies/POLICIES.md` (`agent/policies/`) |
| Human-in-the-loop | `interrupt_on` + `interrupt()` in a tool | `agent/hitl.py`; the review gate in `agent/tools/review_tools.py` |
| Middleware | `middleware` | `agent/middleware.py`: PII, model-call limit, model/tool retries, model fallback, policy seed, audit, read-only tool filter |
| State / context schemas | `state_schema`, `context_schema` | `agent/state.py` (`approved_diff_hash` is `OmitFromInput`) |
| Checkpointer / store | `checkpointer`, `store` | `core/persistence.py` |
| Deployment | `langgraph.json` → `graph.py:make_graph` | `src/shipwright/graph.py`, `docs/deployment.md` |
| Evals | LangSmith `aevaluate` | `evals/` |

The rest is plain application code: `domain/` (ticket parsing, changelog), `integrations/` (the
`JiraGateway` / `GitHubGateway` protocols with a local and an MCP implementation each),
`knowledge/` (ingestion and search), `audit.py`.

```
src/shipwright/
  main.py  graph.py  audit.py
  core/          config.py  persistence.py
  domain/        tickets.py  changelog.py
  agent/         orchestrator.py  dependencies.py  state.py  backend.py  middleware.py  hitl.py
                 subagents/  prompts/  tools/  skills/  policies/
  integrations/  mcp.py  jira/{gateway,local_jira,mcp_jira}.py  github/{gateway,local_github,mcp_github}.py
  sandbox/       workspace.py  providers.py  shell.py  git_diff.py
  knowledge/     ingest.py  search.py
```

## Security model (what the code guarantees)

- **No external write without a human.** `update_jira_ticket`, `create_jira_subtasks` and
  `publish_pull_request` are in `interrupt_on`; `publish_pull_request` additionally refuses unless
  the review gate (`request_human_review`) was approved.
- **The PR is exactly the approved diff.** Approving echoes the `diff_hash` shown to the human; it
  is stored in state (`approved_diff_hash`, not settable from input). At publish time the diff is
  recomputed and must hash the same, and every pushed file's git blob sha must equal the blob id in
  that diff (`sandbox/git_diff.py`). This blocks tricks like clean filters or `assume-unchanged`
  that make the working tree differ from the diff.
- **No credentials in the sandbox.** The sandbox clone cannot push; publishing goes through the
  gateway, outside the sandbox. The local shell runs with a scrubbed environment.
- **Read-only where it should be.** `/policies/` and `/skills/` are write-denied; `code_analyst`,
  `reviewer` and `researcher` have their write tools and `execute` removed by middleware.
- **Audit trail** of every write-capable tool call (`tmp/audit.jsonl` locally, MongoDB live),
  with secrets redacted.

## Live mode

Set `SHIPWRIGHT_MODE=live` and fill `.env` (GitHub PAT + `SHIPWRIGHT_REPO`, Jira URL/user/token,
`MONGODB_URI`, LangSmith key for the sandbox; Pinecone recommended). Seed the Jira project with
`uv run python scripts/seed_jira.py`, then run the same CLI command, or `uv run langgraph dev` to use
LangSmith Studio. Deployment: `docs/deployment.md`.

## Tests and evals

```bash
uv run pytest            # unit tests (fake model, real git, local gateways); `-m llm` for real-model tests
uv run ruff check .
uv run python evals/run_evals.py   # LangSmith experiment over the 7 scenarios (real model)
```
