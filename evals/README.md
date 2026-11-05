# Shipwright offline evaluations

One LangSmith experiment over the 7 demo scenarios (`demo/tickets.yaml`). Each example runs the
real agent with a real OpenAI model, fake Jira/GitHub/web, the `local` sandbox provider and the
scripted human decisions of `shipwright.evaluation.decisions` (same table as the integration tests).

## Run

Needs a `.env` with `OPENAI_API_KEY`, `MODEL_*`, `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true`,
`LANGSMITH_PROJECT=shipwright`.

```
uv run python evals/run_evals.py --repetitions 2
```

This upserts the dataset `shipwright-scenarios` (stable example ids, safe to re-run), then calls
`langsmith.aevaluate(run_example, ...)` with `experiment_prefix="shipwright"`.

## Evaluators (`evals/evaluators.py`)

| key | measures | reference threshold |
| --- | --- | --- |
| `outcome` | harness outcome (`pr` / `questions` / `subtasks` / `stopped`) matches `expected`; `draft_or_stop` accepts a draft PR or a clean stop | = 1.0 |
| `no_pr_when_ambiguous` | scenario 4 only: no PR was created (`pr_body is None`) | = 1.0 |
| `trajectory_subagents` | recall of the expected subagents; -0.5 if `researcher` is called on scenario 1 | >= 0.8 |
| `criteria_covered` | LLM judge (`model_light`): fraction of reference acceptance criteria ticked in the PR body WITH evidence | >= 0.8 |
| `tests_green` | tests passed at the last review gate (only where `expected == pr`) | informative |

A score of `None` means the evaluator does not apply to that scenario.

## Scoring rules

- `trajectory_subagents`: score = recall of `expected_subagents` (scenario 5 expects no reviewer: it
  is skipped when tests fail). Penalties: `researcher` called on scenario 1 costs 0.5; on scenarios
  4 and 7 (ask / split only) any `developer` or `reviewer` call gives 0.
- `outcome` on scenario 5 (`draft_or_stop`) accepts a draft PR, a clean stop, or a Jira comment
  followed by abandoning (`questions`). A non-draft PR scores 0. A scenario 5 average below 1.0 can
  be benign (the agent may legitimately stop earlier or later); read the traces before acting.
- `criteria_covered`: the judge answers per criterion index, so paraphrased criterion text still
  counts; a tick without evidence does not.

## Scoring rules

- `trajectory_subagents`: score = recall of `expected_subagents` (scenario 5 expects no reviewer: it
  is skipped when tests fail). Penalties: `researcher` called on scenario 1 costs 0.5; on scenarios
  4 and 7 (ask / split only) any `developer` or `reviewer` call gives 0.
- `outcome` on scenario 5 (`draft_or_stop`) accepts a draft PR, a clean stop, or a Jira comment
  followed by abandoning (`questions`). A non-draft PR scores 0. A scenario 5 average below 1.0 can
  be benign (the agent may legitimately stop earlier or later); read the traces before acting.
- `criteria_covered`: the judge answers per criterion index, so paraphrased criterion text still
  counts; a tick without evidence does not.
