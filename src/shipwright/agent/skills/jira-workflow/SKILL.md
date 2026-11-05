---
name: jira-workflow
description: When and how Shipwright talks to Jira - clarification comment, post-PR comment with In Review transition, and sub-task split templates, with update_jira_ticket and create_jira_subtasks usage. Use when a ticket is unclear, too big, or a PR was opened.
---

# Jira workflow

Audience: the **orchestrator**. All Jira writes go through two tools and each write needs human
approval. Write in English, without credentials (`/policies/POLICIES.md`).

Tools:

- `update_jira_ticket(key, comment, transition_to=None)` - add a comment, optionally move the
  ticket.
- `create_jira_subtasks(parent_key, subtasks=[{summary, description}])` - create sub-tasks under
  a parent ticket.

## When to comment

Comment only in these three situations; otherwise stay silent:

1. The ticket is ambiguous or incomplete and you cannot implement it safely (clarification).
2. A pull request has been published (post-PR comment).
3. The ticket is too large for one pull request (sub-task split).

In v1 the **only** transition ever used is `In Review`, set together with the post-PR comment.
Never transition to `In Progress` or anything else. For clarification and split comments leave
`transition_to` unset.

## 1. Clarification comment (ticket unclear)

Ask 2-4 numbered, specific questions that a product owner can answer in one line each. Ask only
what blocks the work; do not ask about things you can decide from the repo, ADRs or policies.
**No code, no pseudo-code, no implementation proposals.** After posting, **STOP**: do not
research further, do not code, do not open a PR. Wait for the human answer.

```
update_jira_ticket(key="TB-12", comment="""
Hi, before implementing I need a few clarifications:

1. <Question about expected behaviour, with the options you see if useful.>
2. <Question about an edge case or boundary value.>
3. <Question about scope or compatibility, if needed.>

I will wait for your answers before starting.
""")
```

## 2. Post-PR comment

Post it after the pull request is published, with the PR URL returned by the publish tool and a
short summary. Set `transition_to="In Review"`.

```
update_jira_ticket(key="TB-12", transition_to="In Review", comment="""
Pull request opened: <PR URL>

Summary: <1-3 sentences on what changed.>
Acceptance criteria: <n>/<n> covered by tests. Tests: <passed counts>.
Self-review: <verdict>; open risks: <short list or "none">.
""")
```

## 3. Sub-task split

Use it when the policy size limit (400 changed diff lines) would be exceeded or the ticket mixes
independent concerns. Do not publish an oversized PR. Propose 2-5 sub-tasks, each independently
deliverable as one small PR, with a self-contained description and the acceptance criteria it
covers. Then post a comment explaining the split and **stop**.

```
create_jira_subtasks(parent_key="TB-12", subtasks=[
  {"summary": "<imperative, specific title>",
   "description": "Goal: <one sentence>.\nAcceptance criteria covered: <AC list>.\nNotes: <dependencies, order>."},
  {"summary": "<second sub-task>", "description": "..."}
])

update_jira_ticket(key="TB-12", comment="""
This ticket is too large for one pull request, so I split it into sub-tasks:
- <sub-task 1 summary>
- <sub-task 2 summary>
Please review the split; I will implement them one at a time.
""")
```
