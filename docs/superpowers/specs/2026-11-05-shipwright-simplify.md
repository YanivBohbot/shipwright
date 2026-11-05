# Shipwright — Simplification & restructure (v2)

*Date: 2026-11-05 · Branch: `refactor/simplify` (from `feat/shipwright`)*

## Why
The v1 code works and is well tested, but it is hard to learn from: big files (workspace.py ~500 lines,
cli.py ~360, evaluation fakes ~440), generic names, test helpers shipped in `src/`, and many defensive
layers mixed with the Deep Agents wiring. The project's purpose is **learning how to integrate every
important Deep Agents component correctly and run it**. v2 keeps the features and the security
invariants, but reorganises the code so a reader can open one folder per concept.

Reference layout: `YanivBohbot/incidentflow` (`apps/api/src/incidentflow`): `core/` (config),
`agent/` (`agents_specialist/` one file per specialist, `prompts/` one file per agent, `tools/` one
file per domain, `mcp_clients/` one file per service, `middleware.py`, `guardrails/`),
`knowledge/` (one file per store), `domain/`, `main.py` entry point.

## Goals
- One concept per folder, one responsibility per file, clear names. Target: no source file above
  ~200 lines; most under 120.
- Reading order is obvious: `main.py` → `agent/orchestrator.py` (the single `create_deep_agent` call)
  → each component it imports.
- **A demo that actually runs** with only `OPENAI_API_KEY` (+ optional LangSmith tracing, Pinecone,
  Tavily), on this Windows machine, end to end: ticket → plan → subagents → tests in a sandbox →
  human review → "PR" → Jira comment.
- Keep every Deep Agents building block from the v1 spec §4.4: planning, 4 subagents (+ locked
  general-purpose), MCP, custom tools, CompositeBackend (sandbox + per-user `/memories/` + read-only
  `/policies/`), permissions, skills, HITL (`interrupt()` review gate + `interrupt_on`), middleware
  (PII, limits, retries, audit, read-only tool filter), memory, context/state schemas, checkpointer,
  LangSmith tracing, evals, `langgraph.json` deployment.
- Keep the security invariants: no external write without HITL; the PR contains exactly the approved
  diff (diff-hash approval + **blob-sha verification of every pushed file** — closes the open v1
  final-review hole); no credentials reachable by agent tools; raw MCP tools never exposed.

## Non-goals
- New features. Studio UI work. Multi-agent platform.

## Two run modes (new)
| | `SHIPWRIGHT_MODE=local` (default, demo) | `SHIPWRIGHT_MODE=live` |
|---|---|---|
| Jira | `LocalJira`: tickets from `demo/tickets.yaml`, comments/transitions/subtasks written to `demo/.local_jira.json` | `McpJira` (mcp-atlassian) |
| GitHub | `LocalGitHub`: branch + commit in a local bare "remote" of the demo repo; the "PR" is a Markdown file under `demo/.local_prs/` | `McpGitHub` (remote GitHub MCP) |
| Sandbox | `LocalShellBackend` in `tmp/` with scrubbed env, cloned from the local demo repo | `LangSmithSandbox` |
| Checkpointer/Store | in-memory (`InMemorySaver` / `InMemoryStore`) | MongoDB |
| Knowledge | Pinecone if `PINECONE_API_KEY` set, else local keyword search over `knowledge/` | Pinecone |
| Web search | Tavily if `TAVILY_API_KEY` set, else tool disabled | Tavily |

Both modes go through the same `JiraGateway` / `GitHubGateway` protocols, so the agent code is
identical — this itself demonstrates the "gateway behind an interface" pattern.

## Target layout
```
src/shipwright/
  main.py                 # CLI entry: `shipwright run SHIP-2 [--user alice]` (HITL in terminal)
  graph.py                # LangGraph Server entry for langgraph.json (live mode)
  core/
    config.py             # Settings (mode, models, keys, limits)
    persistence.py        # open_persistence(settings) -> (checkpointer, store): memory | mongo
  domain/
    tickets.py            # Ticket, parse_ticket_key, acceptance criteria / spec refs, branch_name
  agent/
    orchestrator.py       # build_shipwright_agent(deps): THE create_deep_agent call, nothing else
    dependencies.py       # Dependencies dataclass + build_dependencies(settings) (local | live)
    state.py              # ShipwrightContext, ShipwrightState (approved_* = OmitFromInput)
    backend.py            # build_backend(): CompositeBackend + PERMISSIONS
    middleware.py         # build_middleware(): PII, limits, retry, fallback, audit, ReadOnlyTools
    hitl.py               # INTERRUPT_ON + build_resume() helpers used by main.py
    subagents/            # researcher.py, code_analyst.py, developer.py, reviewer.py,
                          # general_purpose.py — each exposes `build_<name>(...) -> SubAgent`
    prompts/              # orchestrator.md, researcher.md, code_analyst.md, developer.md, reviewer.md
                          # + loader.py (load_prompt(name, **values))
    tools/
      jira_tools.py       # read_jira_ticket, update_jira_ticket, create_jira_subtasks
      review_tools.py     # show_workspace_diff, request_human_review (interrupt)
      github_tools.py     # publish_pull_request (approval + blob-sha guarded)
      knowledge_tools.py  # knowledge_search
      web_tools.py        # web_search
    skills/               # 4 SKILL.md (unchanged content)
    policies/POLICIES.md
  integrations/
    mcp.py                # MultiServerMCPClient config + call_tool()/McpToolError
    jira/      gateway.py (Protocol + errors)  mcp_jira.py  local_jira.py
    github/    gateway.py (Protocol + FileChange)  mcp_github.py  local_github.py
  sandbox/
    workspace.py          # Workspace: ensure_cloned, diff_and_hash, changed_files
    providers.py          # open_workspace(): local | langsmith
    git_diff.py           # pure functions: parse --raw, refuse unsafe modes, blob sha, verify
  knowledge/
    ingest.py             # LlamaParse/markdown -> chunks -> vector store
    search.py             # KnowledgeSearcher: Pinecone or local keyword fallback
  audit.py                # AuditSink (jsonl in local, Mongo in live) — small
scripts/   ingest_knowledge.py  seed_jira.py   (keep only what the README uses)
evals/     run_evals.py  evaluators.py  dataset.py   (reuse tests' fakes via tests/, not src/)
tests/     unit/<mirrors src layout>  fakes.py (test-only)  integration/ (llm-marked)
```
Deleted/merged from v1: `evaluation/` package (fakes back to tests/, runner folded into evals/),
`smoke.py` + `scripts/smoke_live.py` (the CLI demo replaces them), `demo_tickets.py` (→ LocalJira +
seed script), `-rN` branch-suffix machinery is kept but lives in `github_tools.py` as one small helper.

## Publish guard (fixes the open v1 hole)
`publish_pull_request` pushes only files whose content hashes (`git hash-object` formula:
`sha1(b"blob %d\0" % len(data) + data)`) equal the `index <old>..<new>` blob ids in the approved,
hashed `--full-index` diff; deletions must have `<new> == 0{40}`. Any mismatch or missing path →
refusal, nothing pushed. Implemented as pure functions in `sandbox/git_diff.py` with unit tests,
including the v1 reproduction (clean filter / assume-unchanged makes worktree ≠ diff → refused).

## Definition of done
1. `uv run pytest` green, `ruff` clean, no source file > ~200 lines (report the largest).
2. `uv run shipwright run SHIP-1 --user alice` in local mode with the real OpenAI key completes the
   bugfix scenario end to end: plan → subagents → tests pass in sandbox → review gate (approve) →
   local PR file + local branch commit → local Jira comment. Same for scenario 4 (asks questions,
   no PR). Verified by actually running it.
3. LangSmith trace visible for the run (project `shipwright`).
4. README rewritten as a learning guide: "where each Deep Agents component lives", run instructions
   for local and live modes.
