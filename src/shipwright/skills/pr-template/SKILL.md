---
name: pr-template
description: Exact pull request title and body template (Summary, Linked ticket, Acceptance criteria with evidence, Tests, Self-review, Notes) for Shipwright PRs. Use when the orchestrator writes the PR before publishing.
---

# Pull request template

Audience: the **orchestrator**, when preparing the title and body passed to the publish tool.
Fill every section; write `None.` rather than deleting a section. Keep the headings exactly as
below, in this order, because reviewers and tooling rely on them. Respect `/policies/POLICIES.md`
(no credentials in PR text, size limit, ADR-001 reporting).

## Title

Format: `[KEY] <imperative summary>`, for example `[TB-12] Add status filter to task listing`.

The publish tool **adds the `[KEY]` prefix automatically**. Pass only the imperative summary
(`Add status filter to task listing`) so the key is not duplicated. Keep it under about 70
characters, in the imperative mood, with no trailing period.

## Body

The body must have exactly this structure:

```markdown
## Summary
<2-4 sentences: what changed and why, from the user's point of view.>

## Linked ticket
<KEY> - {jira_url}/browse/{key}

## Acceptance criteria
- [x] AC1: <criterion text> - evidence: `test_ac1_...` passes (<file>)
- [x] AC2: <criterion text> - evidence: <test name or behaviour verified>
- [ ] AC3: <criterion text> - not met: <reason>

## Tests
<Command run, result line (passed/failed/xfail counts), and the new or changed tests.>

## Self-review
Verdict: <pass | changes_requested, copied verbatim from the reviewer>
Risks: <risks and open points reported by the reviewer, or "None identified.">

## Notes
<Follow-ups, assumptions, ticket ambiguities resolved, anything a human reviewer should know.
"None." if empty.>
```

### Filling the sections

- **Linked ticket**: `{jira_url}` is the base URL of the Jira site and is given to you in the
  orchestrator prompt; `{key}` is the ticket key (for example `TB-12`). Substitute both so the
  line reads like `TB-12 - https://example.atlassian.net/browse/TB-12`. Never leave the braces
  in the final text and never invent a URL.
- **Acceptance criteria**: one checkbox per AC from the ticket, in order, using the ticket's
  wording. Tick `[x]` only with concrete evidence (a test name that passes, or a verified
  behaviour). Leave `[ ]` with the reason if an AC is not fully met; do not tick on faith.
- **Tests**: the command is `uv run pytest -q` from the repository root, with the counts from
  the last run reported by the developer.
- **Self-review**: copy the reviewer's verdict and its risks faithfully, including any that
  remain unresolved after the fix cycles. Do not soften them.
- **Notes**: mention anything outside the AC scope you deliberately left alone.

The PR body is part of what the human approves, so it must match the diff exactly: do not
describe changes that are not in the diff.
