"""Eval target: run one demo scenario offline (fake Jira/GitHub/web, local provider)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from shipwright.evaluation.decisions import decider_for
from shipwright.evaluation.runner import RunOutcome, build_scenario_env, drive, is_green


def summarize_run(env: Any, outcome: RunOutcome) -> dict[str, Any]:
    """Reduce a finished run to the dict the evaluators read.

    outcome: `pr` (a PR was created), `subtasks` (Jira sub-tasks created), `questions` (only
    Jira comments, the review gate was never reached), otherwise `stopped`.
    """
    gates = [i for i in outcome.interrupts if i.get("kind") == "review_gate"]
    prs = env.github.prs
    if prs:
        kind = "pr"
    elif env.jira.subtasks:
        kind = "subtasks"
    elif env.jira.comments and not gates:
        kind = "questions"
    else:
        kind = "stopped"
    return {
        "outcome": kind,
        "subagents": list(outcome.subagents_called),
        "tests_passed": bool(gates) and is_green(str(gates[-1].get("test_report", ""))),
        "pr_body": prs[0]["body"] if prs else None,
        "draft": bool(prs[0].get("draft")) if prs else False,
    }


async def run_example(inputs: dict) -> dict:
    """aevaluate target: `inputs = {"ticket_key", "scenario"}`."""
    scenario = int(inputs["scenario"])
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = await build_scenario_env(Path(tmp), scenario)
        outcome = await drive(
            env.agent,
            env.key,
            decide=decider_for(scenario),
            config=env.new_config(),
            context=env.context,
        )
        return summarize_run(env, outcome)
