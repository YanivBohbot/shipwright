---
name: testing-guidelines
description: How to write, name, run and report tests in the taskboard-api repo - exact pytest command, one test per acceptance criterion named test_ac<n>_..., regression tests for bugs, strict-xfail convention. Use when implementing or verifying a change.
---

# Testing guidelines

Audience: the **developer** (write and run tests) and the **reviewer** (check test quality).
Policy reminder from `/policies/POLICIES.md`: every behaviour change must come with tests.

## Run command

From the repository root, run exactly:

```bash
uv run pytest -q
```

Run the full suite, not a subset, before reporting. Use the repository path given in your task
prompt as the working directory; do not hard-code paths of your own. The attempt limit given in
your prompt applies to every run of this command.

## One test per acceptance criterion

- Each acceptance criterion (AC) of the ticket gets at least one test, numbered in ticket
  order and named `test_ac<n>_<what_is_verified>`, for example
  `test_ac1_list_tasks_filters_by_status` and `test_ac2_unknown_status_returns_422_problem`.
- Put the tests next to the existing ones (`tests/test_tasks.py`, `tests/test_projects.py`, ...)
  and reuse fixtures from `tests/conftest.py`.
- Assert observable behaviour through the HTTP client: status code, response body, and for
  errors the ADR-001 problem+json shape (`type`, `title`, `status`, `detail`) and the
  `application/problem+json` media type.
- Cover the edge cases the AC implies (empty input, missing resource, boundary values), but keep
  each test small and independent. No network, no sleeps, no dependence on test order.

## Bugs: regression test first

- For a bug ticket, write a test that reproduces the bug **before** fixing it and confirm it
  fails for the right reason. It must pass after the fix.
- Name it after the behaviour (`test_ac1_empty_assignee_returns_422`) and keep it permanently.
- Do not weaken or delete existing tests to make the suite pass.

## Strict xfail convention

The demo repo documents a known bug with `@pytest.mark.xfail(strict=True, reason=...)`, and the
test asserts the **desired** behaviour. Consequences:

- If your change fixes that bug, the test will XPASS and strict mode turns that into a failure.
  **Remove the `xfail` marker** (keep the test as the regression test, renaming it to the
  `test_ac<n>_...` form if it maps to an AC) and update the README sentence about the known bug.
- If your change is unrelated, leave existing xfail tests untouched. Never add a new `xfail`
  to hide a failure of your own.

## Reporting results

In your final report include: the command run, the pass/fail/xfail counts from the pytest
summary line, which tests cover which AC (`AC1 -> test_ac1_...`), and for any failure the test
name and the key assertion message. Be honest: do not claim success unless the last run of the
full suite passed. Follow the status-line format required by your task prompt.
