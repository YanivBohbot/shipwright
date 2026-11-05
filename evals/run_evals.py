"""Offline evaluation: `uv run python evals/run_evals.py --repetitions 2` (needs .env keys).

Each example runs the real agent with the real model on a copy of the demo repo, with fake
Jira/GitHub/web and the scripted human decisions of `tests/scenarios.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import langsmith  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from evals.dataset import DATASET_NAME, upsert_dataset  # noqa: E402
from evals.evaluators import (  # noqa: E402
    criteria_covered,
    no_pr_when_ambiguous,
    outcome_matches,
    tests_green,
    trajectory_subagents,
)
from tests.scenarios import RunOutcome, build_scenario_env, decider_for, drive, is_green  # noqa: E402


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


EVALUATORS = [
    outcome_matches,
    trajectory_subagents,
    no_pr_when_ambiguous,
    tests_green,
    criteria_covered,
]


async def main(repetitions: int, dataset: str) -> None:
    load_dotenv()
    client = langsmith.Client()
    upsert_dataset(client, dataset)
    await langsmith.aevaluate(
        run_example,
        data=dataset,
        evaluators=EVALUATORS,
        experiment_prefix="shipwright",
        num_repetitions=repetitions,
        client=client,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--dataset", default=DATASET_NAME)
    args = parser.parse_args()
    asyncio.run(main(args.repetitions, args.dataset))
