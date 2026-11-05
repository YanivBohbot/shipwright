# Shipwright architecture

This document goes deeper than the README. Design rationale and the decisions D1-D8 are in
`docs/superpowers/specs/2026-11-05-shipwright-design.md`.

## 1. Two entry points, one agent

```
 CLI  (shipwright run)                          LangSmith Deployment / langgraph dev
 cli.run_ticket                                 graph.make_graph(config)
   mongo_persistence -> (MongoDBSaver, MongoDBStore)   platform injects checkpointer + store
   open_workspace(thread) + ensure_cloned()            open_workspace(thread) + ensure_cloned()
   build_live_dependencies()                           build_live_dependencies()
   build_agent(settings, deps, workspace, checkpointer, store)   build_agent(settings, deps, workspace)
```

`agent.build_agent` is the single assembly point (`create_deep_agent`). It is called per run
because the workspace (sandbox) is per thread. `make_graph` is the async factory of
`langgraph.json`; the Agent Server also calls it for non-execution accesses (state reads, schema
loading in Studio). Those get a **schema-only** graph: same structure, an uncloned workspace on a
`StateBackend` (no sandbox is created) and stub services that refuse every call. A run without
`thread_id` is refused.

## 2. Components

| Concern | Module | Notes |
|---|---|---|
| Settings | `config.py` | pydantic-settings, every secret is a `SecretStr` |
| Orchestrator | `agent.py`, `prompts/orchestrator.md` | tools: Jira read/update/subtasks, diff, review gate, publish |
| Subagents | `subagents/specs.py` + `prompts/` | dicts with scoped tools, `ExcludeToolsMiddleware` for read-only ones, skills passed explicitly |
| Workspace | `workspace.py` | clone (anonymous HTTPS), `diff_hash()`, changed files, test runs, skills upload |
| Backends | `backends.py` | `CompositeBackend`: default = sandbox, `/memories/` and `/policies/` = `StoreBackend` |
| Permissions | `permissions.py` | filesystem rules for the built-in file tools only |
| Gateways | `integrations/` | `GitHubGateway`, `JiraGateway` Protocols + MCP implementations; fakes in `evaluation/fakes.py` |
| Composite tools | `tools/` | the only code that writes to GitHub/Jira |
| Middleware | `middleware/` | stack, audit, tool filter, policy seeding |
| Persistence | `persistence.py` | MongoDB saver + store for the CLI |
| Knowledge | `knowledge/` | LlamaParse -> chunks -> Pinecone; `KnowledgeSearcher` |
| Evaluation | `evaluation/`, `evals/` | scripted decisions, fakes, harness, LangSmith evaluators |

## 3. Storage and namespaces

- `/memories/preferences.md` -> `StoreBackend`, namespace `("users", context.user_id)`; refuses a
  run without `user_id`. The agent writes reusable human feedback here (scenario 6).
- `/policies/POLICIES.md` -> `StoreBackend`, namespace `("org", settings.org_id)`, write-denied.
  `CompositeBackend` strips the route prefix, so the store key is `/POLICIES.md`.
  Seeding: `scripts/seed_policies.py` (MongoDB, CLI runs; the CLI also seeds when absent) and
  `PolicySeedMiddleware` (`abefore_agent`, writes through `runtime.store` only if the key is
  absent; it runs before deepagents' `MemoryMiddleware` reads the policies).
- Checkpoints: MongoDB (`MongoDBSaver`, database `MONGODB_DB`) for the CLI; the platform's
  checkpointer, configured as `mongo` through `LS_MONGODB_URI`, for a deployment.
- Store collection `store`; audit collection `audit_log` (both in `MONGODB_DB`).

## 4. The approval chain (why the PR equals what the human saw)

1. `developer` leaves its changes in the sandbox working tree.
2. `request_human_review` computes `diff_hash = sha256(raw diff bytes)`, calls `interrupt()` with
   diff + hash + test and review reports.
3. On resume, `approve` must echo the `diff_hash`; it is compared with a hash recomputed at resume
   time (mismatch: `NOT APPROVED`). The tool stores `approved_diff_hash` and `approved_ticket_key`
   in the graph state (`ShipwrightState`).
4. `publish_pull_request` (guarded by `interrupt_on`: approve/reject) refuses unless the hash of
   the current tree equals `approved_diff_hash` **before and after** reading the changed files, the
   ticket key matches, and `main`'s head equals the workspace `base_sha`.
5. It picks the first free branch among `shipwright/<KEY>`, `-r2` ... `-r20`, creates it from
   `main`, verifies the new branch points at `base_sha`, pushes the files plus the `CHANGELOG.md`
   entry, opens the PR (`Supersedes <url>` for earlier open PRs) and clears the approval.

## 5. Failure behaviour

- Tests fail after `MAX_TEST_ATTEMPTS`: the reviewer is skipped, the gate says so explicitly, and
  an approval yields a **draft** PR (scenario 5).
- Reviewer asks for changes: at most `MAX_REVIEW_CYCLES` developer/reviewer cycles, then the
  remaining findings are reported as risks.
- Ambiguous ticket: Jira comment with questions, stop (scenario 4). Oversized ticket: sub-task
  proposal, stop (scenario 7).
- Transient model/network errors are retried (model: always; tools: only read-only ones); a
  fallback to `MODEL_LIGHT` follows. `PIIDetectionError` (a GitHub token in input) aborts the run
  with a clear CLI message.
- A MCP tool error becomes `McpToolError` (never swallowed) so a failed `create_branch` cannot
  fall through to `push_files`.

## 6. Evaluation

`evals/run_evals.py` upserts the `shipwright-scenarios` dataset from `demo/tickets.yaml` and runs
`langsmith.aevaluate` with the real agent + real OpenAI model + fake Jira/GitHub/web + the
`local` sandbox and the scripted decisions of `evaluation/decisions.py` (the same table drives
`tests/integration`). See `evals/README.md`.
