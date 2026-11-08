# Shipwright offline evaluations

The dataset (`evals/dataset.py`, one example per demo ticket of `demo/tickets.yaml`) and the
evaluators (`evals/evaluators.py`) are kept and unit-tested.

The runner that drove the agent against a local fake Jira/GitHub (`evals/run_evals.py`) was
removed with the local mode. A new runner must run the agent against a test Jira project and a
test GitHub repo, with scripted human decisions, and produce for each run the outputs the
evaluators read: `outcome`, `subagents`, `tests_passed`, `pr_body`, `draft`.

## Evaluators (`evals/evaluators.py`)

| key | measures | reference threshold |
| --- | --- | --- |
| `outcome` | run outcome (`pr` / `questions` / `subtasks` / `stopped`) matches `expected`; `draft_or_stop` accepts a draft PR or a clean stop | = 1.0 |
| `no_pr_when_ambiguous` | scenario 4 only: no PR was created (`pr_body is None`) | = 1.0 |
| `trajectory_subagents` | recall of the expected subagents; penalties in "Scoring rules" below | >= 0.8 |
| `criteria_covered` | LLM judge (`model_light`): fraction of reference acceptance criteria ticked in the PR body WITH evidence | >= 0.8 |
| `tests_green` | tests passed on the last run (only where `expected == pr`) | informative |

A score of `None` means the evaluator does not apply to that scenario.

## Scoring rules

- `trajectory_subagents`: score = recall of `expected_subagents` (scenario 5 expects no reviewer: it
  is skipped when tests fail). Penalties: `researcher` called on scenario 1 costs 0.5; on scenarios
  4 and 7 (ask / split only) any `developer` or `reviewer` call gives 0.
- `outcome` on scenario 5 (`draft_or_stop`) accepts a draft PR, a clean stop, or a Jira comment
  followed by abandoning (`questions`). A non-draft PR scores 0.
- `criteria_covered`: the judge answers per criterion index, so paraphrased criterion text still
  counts; a tick without evidence does not.
