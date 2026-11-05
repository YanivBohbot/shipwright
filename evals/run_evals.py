"""Offline evaluation: `uv run python evals/run_evals.py --repetitions 2` (needs .env keys)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

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
from evals.harness import run_example  # noqa: E402

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
