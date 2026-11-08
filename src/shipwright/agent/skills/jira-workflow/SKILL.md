---
name: jira-workflow
description: When and how Shipwright writes to Jira with the mcp-atlassian tools - clarification comment (jira_add_comment), sub-task split (jira_create_issue), post-PR comment and In Review transition (jira_get_transitions + jira_transition_issue). Use before any Jira write.
---

# Jira workflow

Audience: the **orchestrator**. Jira tools come from the mcp-atlassian MCP server:

| tool | kind | use |
|------|------|-----|
| `jira_get_issue(issue_key)` | read | the ticket (summary, description, status) |
| `jira_get_transitions(issue_key)` | read | the transitions available now, with their ids |
| `jira_add_comment(issue_key, body)` | write | a comment (Markdown is fine) |
| `jira_transition_issue(issue_key, transition_id)` | write | move the ticket |
| `jira_create_issue(project_key, summary, issue_type, description, additional_fields)` | write | a sub-task |

Every write is shown to a human first. Write in English, without credentials.

Comment only in these three situations; otherwise stay silent.

## 1. Clarification (ticket unclear)

Ask 2-4 numbered questions a product owner can answer in one line each, only about what blocks
the work. No code, no implementation proposal. Then **STOP**: no research, no code, no PR.

```
jira_add_comment(issue_key="SHIP-12", body="""Hi, before implementing I need a few clarifications:

1. <Question about expected behaviour, with the options you see.>
2. <Question about an edge case or boundary value.>

I will wait for your answers before starting.""")
```

## 2. Sub-task split (too big for one PR)

When the policy size limit (400 changed diff lines) would be exceeded or the ticket mixes
independent concerns, propose 2-5 sub-tasks, each one small PR. Create each one, then comment
the split on the parent and **STOP**.

```
jira_create_issue(
  project_key="SHIP",
  summary="<imperative, specific title>",
  issue_type="Subtask",
  description="Goal: <one sentence>.\nAcceptance criteria covered: <AC list>.",
  additional_fields='{"parent": "SHIP-12"}',
)
```

If `Subtask` is rejected, the project names the type differently (e.g. `Sub-task`): retry once
with that name.

## 3. After the pull request is opened

1. `jira_add_comment` with the PR URL returned by `create_pull_request`:

```
Pull request opened: <PR URL>

Summary: <1-3 sentences on what changed.>
Acceptance criteria: <n>/<n> covered by tests. Tests: <passed counts>.
Self-review: <verdict>; open risks: <short list or "none">.
```

2. `jira_get_transitions`, find the transition named "In Review", then
   `jira_transition_issue(issue_key=..., transition_id=<its id>)`. If there is none, say so in
   the final summary; never use another transition.
