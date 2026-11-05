# Shipwright

Shipwright is a [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) agent that
turns **one Jira ticket into one reviewed GitHub pull request**. It reads the ticket, researches
the referenced specs, explores the target repository, implements the change and its tests in an
isolated sandbox, self-reviews the diff, and then stops to ask a human. Nothing reaches GitHub or
Jira without an explicit approval, and the pull request that is published is bit-for-bit the diff
the human approved.

Built with the open-source `create_deep_agent` SDK (not Managed Deep Agents), LangChain 1.x
middleware, LangGraph persistence on MongoDB Atlas, Pinecone for the knowledge base, LangSmith
sandboxes / tracing / evals / deployment, and the official GitHub and Atlassian MCP servers.

## How it works

```
                      +------------------------- human (terminal CLI or LangSmith Studio) -------------------------+
                      |  approve / edit / reject (interrupt_on)        approve(diff_hash) / feedback / reject      |
                      |  update_jira_ticket, create_jira_subtasks,     (review gate: request_human_review)         |
                      |  publish_pull_request                                                                      |
                      +------------------------------------------^---------------------------------------------+
                                                                 |
  Jira ticket  --read-->  +--------------------------------------+---------------------------------------+
  (Atlassian MCP)         |  ORCHESTRATOR  (create_deep_agent, gpt strong model)                         |
                          |  write_todos . skills . /memories (per user) . /policies (org, read-only)    |
                          |  middleware: PII . call limit . retries . fallback . audit . policy seed     |
                          +--+-----------+-------------+------------------+------------------------------+
                             | task      | task        | task             | task                  composite tools (only writers)
                             v           v             v                  v                       +-----------------------------+
                       +-----------+ +-------------+ +-----------+ +-----------+                  | read_jira_ticket            |
                       | researcher| | code_analyst| | developer | | reviewer  |  (+ locked       | update_jira_ticket   [HITL] |
                       | knowledge | | read-only   | | write +   | | read-only |   general-       | create_jira_subtasks [HITL] |
                       | + web     | | repo explorer| | execute   | | + diff    |   purpose)       | show_workspace_diff         |
                       +-----+-----+ +------+------+ +-----+-----+ +-----+-----+                  | request_human_review [gate] |
                             |              |              |             |                        | publish_pull_request [HITL] |
                     Pinecone / Tavily      +---- sandbox (cloned repo, tests; NO GitHub write creds)+---------------+---------+
                                                                                                        GitHub MCP (branch, push, PR)
  State: LangGraph checkpointer + store on MongoDB Atlas (local) / platform (LangSmith Deployment); audit_log in MongoDB
```

Nominal flow for a ticket: read ticket -> clarification gate (no acceptance criteria: ask on Jira
and stop) -> size gate (too big: propose sub-tickets and stop) -> plan with `write_todos` ->
`researcher` (only if a spec/ADR or external knowledge is needed) in parallel with `code_analyst`
-> `developer` (code + tests, at most 3 test attempts) -> `reviewer` (at most 2 fix cycles) ->
**review gate** -> `publish_pull_request` -> `update_jira_ticket` (PR link, transition to
`In Review`). Details in [docs/architecture.md](docs/architecture.md).

## Deep Agents building block -> where it lives

| Building block | Implementation | Where |
|---|---|---|
| Planning | `write_todos` (explicit `TodoListMiddleware`) | `src/shipwright/agent.py` |
| Subagents | `researcher`, `code_analyst`, `developer`, `reviewer` (+ locked `general-purpose`), scoped tools | `src/shipwright/subagents/specs.py`, `subagents/prompts/*.md`, `agent.py` |
| MCP | GitHub MCP + Atlassian MCP via `langchain-mcp-adapters`, wrapped by gateways; raw write tools never reach a model | `integrations/mcp_client.py`, `integrations/github.py`, `integrations/jira.py` |
| Custom tools | `knowledge_search` (Pinecone), `web_search` (Tavily), composite Jira / publish / review-gate / diff tools | `tools/*.py`, `knowledge/search.py` |
| Backends | `CompositeBackend`: sandbox workspace (default) + `StoreBackend` on `/memories/` (per user) and `/policies/` (per org) | `backends.py`, `workspace.py` |
| Sandbox | `LangSmithSandbox`, one per thread, idle TTL; `LocalShellBackend` only for `SHIPWRIGHT_SANDBOX_PROVIDER=local` (dev/tests/evals) | `workspace.py` |
| Permissions | `/policies/**` write-deny; subagents also cannot write `/memories/**` | `permissions.py` |
| Read-only subagents | middleware that removes write tools and `execute` from their model calls | `middleware/tool_filter.py` |
| Skills | `coding-conventions`, `pr-template`, `jira-workflow`, `testing-guidelines` (not inherited: passed explicitly) | `src/shipwright/skills/` |
| Human-in-the-loop | `interrupt_on` on every GitHub/Jira write + custom `review_gate` interrupt | `tools/__init__.py`, `tools/review_gate.py` |
| Middleware | PII redaction / GitHub-token block, model-call limit, retries, model fallback, audit trail, policy seeding | `middleware/stack.py`, `audit.py`, `policy_seed.py` |
| Memory | `/memories/preferences.md` per user (reusable review feedback) | `backends.py`, orchestrator prompt |
| Context schema | `user_id`, `jira_project` | `context.py` |
| Durability | MongoDB checkpointer + store (CLI); `checkpointer.backend = mongo` in `langgraph.json` (deployment) | `persistence.py`, `langgraph.json` |
| Observability | LangSmith tracing (`LANGSMITH_TRACING`, `LANGSMITH_PROJECT`) | `.env` |
| Evaluation | offline LangSmith experiment over 7 scenarios | `evals/`, `src/shipwright/evaluation/` |
| Deployment | `langgraph.json` -> LangSmith Deployment | `langgraph.json`, [docs/deployment.md](docs/deployment.md) |

## Setup

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), accounts for OpenAI, LangSmith,
MongoDB Atlas, Pinecone, LlamaCloud, Tavily, GitHub (PAT) and Jira (API token).

```bash
uv sync
cp .env.example .env     # then fill in every value (see below)
```

`.env` variables (all in `src/shipwright/config.py`): `OPENAI_API_KEY`, `MODEL_STRONG`,
`MODEL_LIGHT`, `LANGSMITH_API_KEY` (+ `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=shipwright`),
`MONGODB_URI`, `MONGODB_DB`, `PINECONE_API_KEY` / `PINECONE_INDEX` / `PINECONE_NAMESPACE`,
`LLAMA_CLOUD_API_KEY`, `TAVILY_API_KEY`, `GITHUB_PAT`, `GITHUB_MCP_URL`, `SHIPWRIGHT_REPO`
(`owner/name`), `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`, `JIRA_PROJECT`,
`SHIPWRIGHT_SANDBOX_PROVIDER` (`langsmith` | `local`), `MAX_TEST_ATTEMPTS`, `MAX_REVIEW_CYCLES`,
`ORCHESTRATOR_MODEL_CALL_LIMIT`, `SANDBOX_IDLE_TTL_SECONDS`, `ORG_ID`.

One-time seeding (these talk to real services):

```bash
uv run python scripts/ingest_knowledge.py   # knowledge/ (specs, ADRs) -> Pinecone
uv run python scripts/seed_policies.py      # org policies -> MongoDB store (CLI runs)
uv run python scripts/seed_jira.py          # demo tickets -> Jira, keys saved in demo/seeded.json
```

Demo repository: the target repo is a **public** FastAPI service (`demo/taskboard-api/`, its own
git history) that you publish as a GitHub repo, for example
`gh repo create <you>/taskboard-api --public --source demo/taskboard-api --push`, then set
`SHIPWRIGHT_REPO=<you>/taskboard-api`. It is public on purpose: the sandbox clones it anonymously
over HTTPS and never holds a GitHub credential. Optionally protect `main` and keep the CI
workflow so the PR shows a green check.

The deployed graph does not need `seed_policies.py`: `PolicySeedMiddleware` writes
`/policies/POLICIES.md` into the runtime store on first use when it is missing.

## Commands

```bash
uv run shipwright run SHIP-2 [--user alice] [--thread my-thread]  # terminal CLI (local mode)
uv run langgraph dev                                              # Agent Server + Studio, hot reload
uv run pytest                                                     # unit tests (offline, default)
uv run pytest -m llm                                              # scenario tests with a real model
uv run pytest -m live                                             # live tests (real services)
uv run python evals/run_evals.py --repetitions 2                  # LangSmith offline evaluation
uv run python scripts/smoke_live.py 2                             # live smoke run + PR/Jira check
uv run ruff check .
```

`shipwright run` streams todos, subagent activity and tool calls, and asks for your decision in
the terminal at every pause. Re-running with the same `--thread` resumes a paused run. Deployment:
[docs/deployment.md](docs/deployment.md). Demo script: [docs/demo-guide.md](docs/demo-guide.md).

## Security model

- **Every write is approved by a human.** `publish_pull_request`, `update_jira_ticket` and
  `create_jira_subtasks` are guarded by `interrupt_on`; the arguments are shown and can be edited
  (Jira tools) or rejected. Raw MCP write tools are never exposed to a model.
- **Review gate bound to the exact diff.** `request_human_review` shows the full diff and its
  SHA-256 `diff_hash`. Approving requires echoing that hash; it is re-checked when the run
  resumes and again, before and after reading the files, inside `publish_pull_request`. If the
  working tree changed, or the approval belongs to another ticket, or `main` moved since the
  clone, publication is refused. The approval is cleared after publishing.
- **The sandbox holds no GitHub write credentials** (D2). The repo is public and cloned over
  anonymous HTTPS; the developer cannot `git push`. After approval, the orchestrator reads the
  changed files from the sandbox and pushes them through the GitHub MCP server on the host side.
  In local mode the shell environment is scrubbed of every `Settings` variable.
- **Fresh branches only.** Shipwright never pushes onto an existing branch. A re-run uses the
  first free `shipwright/<KEY>`, `shipwright/<KEY>-r2`, ... created from `main`, and the new PR
  body says `Supersedes <previous PR URL>`. The PR therefore always equals the approved diff.
- **Read-only subagents.** `researcher`, `code_analyst` and `reviewer` have the write tools and
  `execute` stripped from their model calls; the auto-added `general-purpose` subagent is
  replaced by a tool-less, read-only one of the same name so it cannot inherit
  `publish_pull_request` / Jira write tools. Only `developer` writes files and runs commands, and
  it cannot commit or push.
- **Org policies are read-only** (`/policies/**` write-deny, seeded outside the model's reach);
  subagents also cannot write `/memories/**`.
- **Input guards and audit.** Emails are redacted from input, GitHub tokens in input abort the
  run, `ModelCallLimitMiddleware(150)` bounds runaway loops, only read-only tools are retried,
  and every write-capable tool call is logged (secrets redacted) in the MongoDB `audit_log`
  collection.

## Known limitations

- **Live behaviour is not yet verified end to end.** The offline suite and fakes cover the logic;
  MCP tool names/result shapes, the LangSmith sandbox image (git + uv), Pinecone/LlamaParse
  quality and the deployment are validated only by the live smoke run (`scripts/smoke_live.py`).
- Local `LocalShellBackend` mode is for dev/tests/evals only: its environment scrubbing is a
  default, not a security boundary. Never use it with untrusted tickets.
- `langgraph dev` runs on an in-memory checkpointer (the `mongo` backend applies to a real
  deployment). A deployment uses the platform store (Postgres); the audit log stays in MongoDB.
- Stateless runs are not supported: every run needs a `thread_id`.
- A superseded PR is not closed automatically; if `main` moves during a run, publishing is
  refused and the run must be repeated.
- Shipwright does not watch the GitHub Actions run of the PR; the CI check is for the human.
- API clients can write arbitrary state keys on a thread; the approval keys are only trustworthy
  for threads driven through the CLI/Studio flow (no input filter on `approved_*`).
- Human rejections at `interrupt_on` leave no audit event (only executed writes are logged).
- `shipwright.evaluation` fakes ship in the deployment image but are unreachable from production
  code paths (guarded by a unit test).

## Repository layout

```
src/shipwright/   agent.py graph.py cli.py config.py workspace.py backends.py permissions.py ...
  subagents/ tools/ middleware/ integrations/ knowledge/ skills/ prompts/ policies/ evaluation/
scripts/          ingest_knowledge.py seed_policies.py seed_jira.py smoke_live.py build_knowledge_pdf.py
knowledge/        specs and ADRs of the demo repo (source of the Pinecone index)
demo/             tickets.yaml (7 scenarios), taskboard-api/ (target repo)
evals/            LangSmith dataset, evaluators, runner      tests/   unit + integration (llm/live)
docs/             architecture.md  deployment.md  demo-guide.md
```
