# Demo guide: scenarios 2 -> 4 -> 6

Three runs show the nominal path (2), a clean stop on an ambiguous ticket (4) and the feedback
loop with memory (6). Ticket keys come from `demo/seeded.json` (`scripts/seed_jira.py`); the
examples below call them `SHIP-<n>`. Use the same `--user` for all three runs (memory is per user).

## Before the demo (once)

- `.env` filled, knowledge ingested, Jira tickets seeded, demo repo public (`SHIPWRIGHT_REPO`).
- `uv run python scripts/smoke_live.py 2` passed at least once (it opens a PR on the demo repo;
  that is fine, the demo run will open a `-r2` revision, see "Show" below).
- Open side by side: a terminal (CLI) or Studio, the Jira ticket, the GitHub repo's PR list and the
  LangSmith project `shipwright`.
- Optional reset between rehearsals: close the old PRs and delete the `shipwright/*` branches.

## Where to type decisions

- **CLI** (`uv run shipwright run SHIP-2 --user demo`): the terminal asks. Review gate:
  `approve` / `feedback` / `reject`. Tool approvals: `approve` / `edit` / `reject` (publish only
  `approve` / `reject`). `approve` at the gate automatically echoes the `diff_hash`.
- **Studio** (`uv run langgraph dev` or the deployment): the run pauses on an interrupt; paste
  the resume value in the interrupt box:

  | Interrupt | Resume value |
  |---|---|
  | review gate | `{"type": "approve", "diff_hash": "<copy diff_hash from the interrupt payload>"}` |
  | review gate (changes) | `{"type": "feedback", "message": "<what to change>"}` |
  | review gate (stop) | `{"type": "reject", "message": "<why>"}` |
  | `publish_pull_request`, `update_jira_ticket`, `create_jira_subtasks` | `{"decisions": [{"type": "approve"}]}` (or `{"type": "reject", "message": "..."}`; Jira tools also accept `{"type": "edit", "edited_action": {"name": "<tool>", "args": {...}}}`) |

  Approving the gate **without** the `diff_hash` is refused: that is the point.

## Scenario 2: CSV export (the "wow", about 5 minutes)

Ticket: "Export the tasks of a project as CSV", six acceptance criteria, contract `EXPORT-CSV-v2`.

Type: `uv run shipwright run SHIP-<n2> --user demo` (Studio: message `Process Jira ticket SHIP-<n2>.`).

Show while it runs:
- The todo list (`write_todos`) and the parallel `task` calls to `researcher` and `code_analyst`
  (the ticket references a spec, so the researcher runs and cites `EXPORT-CSV-v2` and ADR-001).
- In LangSmith, the trace tree: orchestrator -> subagents -> tool calls, and the sandbox `execute`
  calls of the `developer` (tests).

Decisions:
1. Review gate: read the diff, tests and reviewer report (each AC mapped to evidence). Choose
   `approve`.
2. `publish_pull_request`: look at the arguments (branch, title, body, `changelog_entry`);
   `approve`.
3. `update_jira_ticket`: the comment holds the PR URL and `transition_to` is `In Review`;
   `approve`.

Show after: the PR on GitHub (template body, `CHANGELOG.md` entry, CI check running), the Jira
ticket (comment + `In Review`), the `audit_log` documents in Atlas. If an earlier smoke PR exists,
point out the branch `shipwright/<key>-r2` (lowercase key, e.g. `shipwright/ship-2-r2`) and the line `Supersedes <old PR>`: Shipwright never
pushes onto an existing branch.

## Scenario 4: ambiguous ticket (about 2 minutes)

Ticket: "Improve performance" (no acceptance criteria).

Type: `uv run shipwright run SHIP-<n4> --user demo`.

Show: the agent reads the ticket and stops at the clarification gate. No subagent is delegated, no
sandbox code, no PR.

Decision: `update_jira_ticket` (a comment with 2-4 precise questions, no transition): `approve`.
(Try `edit` once to show the human can reword the comment.)

Show after: the Jira comment with the questions; the PR list is unchanged; in the trace there is no
`publish_pull_request` call.

## Scenario 6: feedback loop and memory (about 5 minutes)

Ticket: "Detailed health endpoint" (contract `HEALTH-v1`, five acceptance criteria).

Type: `uv run shipwright run SHIP-<n6> --user demo`.

Decisions:
1. First review gate: choose `feedback` with the message
   `Use a dataclass for the health payload, not a dict`.
2. Watch the new cycle: the `developer` reworks the diff, the `reviewer` runs again, and a second
   review gate appears with a new diff and a new `diff_hash`. Choose `approve`.
3. Approve `publish_pull_request` and `update_jira_ticket` as in scenario 2.

Show: the diff before and after the feedback (dataclass); the orchestrator recording the reusable
preference in `/memories/preferences.md` (visible in the trace as an `edit_file`/`write_file` call,
and in the Mongo `store` collection under namespace `["users", "demo"]`). To demonstrate the
memory, run any other ticket with `--user demo` (for example scenario 1): the agent reads the
preference at the start and applies it without being told.

## If something goes wrong on stage

- Re-run with the same `--thread <id>` to resume a paused run (the CLI prints the thread id).
- `NOT APPROVED: the working tree changed...` or `REFUSED: main has moved...`: the safety checks
  fired; request review again / restart the ticket (a fresh `-rN` branch is used).
- The PII guard aborts a run if a GitHub token appears in the input: remove it and retry.
