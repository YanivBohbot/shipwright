---
name: pr-template
description: How Shipwright publishes with the GitHub MCP tools (get_changed_files, list_branches, create_branch, push_files, create_pull_request) and the exact PR title and body template. Use before opening a pull request.
---

# Publishing a pull request

Audience: the **orchestrator**. Each GitHub write is shown to a human first; what they approve
is exactly what is sent, so the PR text must match the diff.

## Steps

1. `get_changed_files()` returns `{"files": [{"path", "content"}], "deleted": [...]}`.
2. `list_branches(owner, repo)`: the branch is `shipwright/<key lowercase>` (for example
   `shipwright/ship-12`); if it exists, use `shipwright/ship-12-r2`, then `-r3`...
3. `create_branch(owner, repo, branch=<branch>, from_branch="main")`.
4. `push_files(owner, repo, branch=<branch>, files=<files from step 1, unchanged>,
   message="SHIP-12: <imperative summary>")`. Never retype or edit file contents yourself.
5. `create_pull_request(owner, repo, head=<branch>, base="main", title=..., body=...)`, with
   `draft=true` when the tests are failing.

## Title

`[KEY] <imperative summary>`, e.g. `[SHIP-12] Add status filter to task listing`: under about
70 characters, imperative mood, no trailing period.

## Body

Keep these headings, in this order; write `None.` rather than deleting a section.

```markdown
## Summary
<2-4 sentences: what changed and why, from the user's point of view.>

## Linked ticket
<KEY> - <jira_url>/browse/<KEY>

## Acceptance criteria
- [x] AC1: <criterion text> - evidence: `test_ac1_...` passes (<file>)
- [ ] AC2: <criterion text> - not met: <reason>

## Tests
<Command run (`uv run pytest -q`), result line (passed/failed/xfail counts), new tests.>

## Self-review
Verdict: <pass | changes_requested, copied verbatim from the reviewer>
Risks: <the reviewer's risks, or "None identified.">

## Notes
<Follow-ups, assumptions, ambiguities resolved. "None." if empty.>
```

- **Linked ticket**: use the Jira site URL from your prompt; never invent one.
- **Acceptance criteria**: one checkbox per AC, in ticket order and wording. Tick only with
  concrete evidence.
- **Self-review**: copy the verdict and risks faithfully, do not soften them. If the tests
  failed, say "Not reviewed: tests failing." and explain in Notes.
