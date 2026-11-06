# Deployment (LangSmith Deployment / Agent Server)

Status: configuration checked offline (`uv run langgraph validate` passes); the actual deployment
and the Studio replay are **not done yet** (they need keys, an Atlas cluster and an explicit
go-ahead because they are outward actions).

## `langgraph.json`

```json
{
  "dependencies": ["."],
  "graphs": { "shipwright": "./src/shipwright/graph.py:make_graph" },
  "env": ".env",
  "python_version": "3.12",
  "checkpointer": { "backend": "mongo" }
}
```

- `dependencies: ["."]` installs the project from `pyproject.toml` (`uv.lock` is present).
- `graphs.shipwright` is the async factory `make_graph(config)`. It needs a `thread_id` to execute.
- `env: ".env"`: `langgraph dev` loads it; `langgraph deploy` reads the same file and turns every
  non-empty variable into a **deployment secret** (it skips the reserved ones, see below).
- `checkpointer.backend = "mongo"`: the Agent Server stores checkpoints in MongoDB. The connection
  string is NOT in the file: the server resolves `LS_MONGODB_URI`, then `MONGODB_URI`
  (`langgraph_api/config/_parse.py`). Atlas replica sets are supported. The **database name must be
  part of the URI** (`mongodb+srv://user:pass@cluster.mongodb.net/shipwright?retryWrites=true`).
- The store of a deployment is the platform's (Postgres); there is no Mongo store there. Org
  policies are therefore seeded at run time by `PolicySeedMiddleware`; no manual step.
- The audit trail still goes to MongoDB (`MONGODB_URI` + `MONGODB_DB`, collection `audit_log`).

## Secrets to configure

All variables of `.env.example` (they are the fields of `shipwright.core.config.Settings`):

| Variable | Required | Note |
|---|---|---|
| `OPENAI_API_KEY` | yes | `MODEL_STRONG` / `MODEL_LIGHT` default to `openai:gpt-5.5` / `openai:gpt-5.4-mini` |
| `LANGSMITH_API_KEY` / `SHIPWRIGHT_LANGSMITH_API_KEY` | one of them | `LANGSMITH_API_KEY` is reserved by the platform: `langgraph deploy` does not upload it. `Settings` accepts either name, so if the platform does not inject `LANGSMITH_API_KEY` into the graph, set `SHIPWRIGHT_LANGSMITH_API_KEY` as a deployment secret (same value; add it to `.env` before `deploy`) |
| `LANGSMITH_TRACING`, `LANGSMITH_PROJECT` | optional | `LANGSMITH_PROJECT` is reserved (set by the platform) |
| `MONGODB_URI`, `MONGODB_DB` | yes | audit log + Settings; also the fallback for the checkpointer URI |
| `LS_MONGODB_URI` | yes | same Atlas URI **including the database name**; used by the Mongo checkpointer |
| `PINECONE_API_KEY`, `PINECONE_INDEX`, `PINECONE_NAMESPACE` | recommended | run `scripts/ingest_knowledge.py` first; without a key, local keyword search over `knowledge/` |
| `LLAMA_CLOUD_API_KEY`, `TAVILY_API_KEY` | optional | PDF parsing at ingestion / `web_search` (disabled without it) |
| `GITHUB_PAT`, `GITHUB_MCP_URL`, `SHIPWRIGHT_REPO` | yes | PAT with PR/contents write on the demo repo |
| `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`, `JIRA_PROJECT` | yes | |
| `SHIPWRIGHT_MODE` | must be `live` | `local` (LocalShellBackend, local Jira/GitHub) is for the terminal demo only |
| `MAX_TEST_ATTEMPTS`, `MAX_REVIEW_CYCLES`, `ORCHESTRATOR_MODEL_CALL_LIMIT`, `SANDBOX_IDLE_TTL_SECONDS`, `ORG_ID` | optional | defaults 3 / 2 / 150 / 3600 / `default-org` |

Network: the Atlas cluster must be reachable from the LangSmith data plane (add the data plane's
egress IPs to the Atlas IP access list, or `0.0.0.0/0` for a throwaway demo cluster). Atlas must be
a replica set (all Atlas clusters are).

## Commands (flags checked with `uv run langgraph --help`, `deploy --help`, `dev --help`)

```bash
uv run langgraph validate                 # check langgraph.json
uv run langgraph dev                      # local Agent Server + Studio on 127.0.0.1:2024
uv run langgraph dev --no-browser --port 2024
```

`langgraph dev` uses an in-memory checkpointer (the `mongo` backend is a deployment feature), loads
`.env`, and calls `make_graph` per run: pass a `thread_id`, so use a thread in Studio.

```bash
# from the repo root, with LANGSMITH_API_KEY in the environment or .env
uv run langgraph deploy --name shipwright                    # build (remote if no Docker) and deploy
uv run langgraph deploy --name shipwright --no-wait          # do not wait for the status
uv run langgraph deploy --name shipwright --deployment-type dedicated   # default is serverless
uv run langgraph deploy list                                 # list deployments
uv run langgraph deploy logs --help                          # fetch deployment logs
uv run langgraph deploy revisions list --help
```

Other useful flags of `deploy`: `--deployment-id` (update an existing deployment), `--remote /
--no-remote`, `-t/--tag`, `--image`, `--json`, `--no-input`, `--verbose`.

## Verification after deploying (live checklist)

1. Open the deployment in Studio, create a thread, send `Process Jira ticket SHIP-<n>.` with a
   `user_id` in the run context (`{"user_id": "demo"}`; the Jira project comes from `JIRA_PROJECT`).
2. Replay scenario 2 (`demo/tickets.yaml`, seeded with `scripts/seed_jira.py`); expected: PR on the demo repo, Jira comment and
   `In Review`.
3. In Atlas, check the checkpointer collections receive documents (the langgraph-checkpoint-mongodb defaults are `checkpoints` and `checkpoint_writes`; the Agent Server may use its own names or database, so also look at the database in `LS_MONGODB_URI`) and that `audit_log` in `MONGODB_DB`
   contains the `request_human_review`, `publish_pull_request` and `update_jira_ticket` events.
4. Check `/policies/POLICIES.md` is readable in a run (the policy seed middleware wrote it).
