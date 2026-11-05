"""LangSmith dataset: one example per demo ticket (inputs + reference outputs)."""

from __future__ import annotations

import uuid
from typing import Any

import langsmith

from shipwright.demo_tickets import load_demo_tickets
from shipwright.tickets import extract_acceptance_criteria

DATASET_NAME = "shipwright-scenarios"
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "shipwright/evals/scenarios")

# Orchestrator `task` subagents a good run is expected to call, per scenario.
# Scenario 1 is a plain bugfix: no `researcher` (penalised by the trajectory evaluator).
EXPECTED_SUBAGENTS: dict[int, list[str]] = {
    1: ["developer", "reviewer"],
    2: ["researcher", "code_analyst", "developer", "reviewer"],
    3: ["researcher", "developer", "reviewer"],
    4: [],
    5: ["developer"],
    6: ["developer", "reviewer"],
    7: [],
}


def example_id(scenario: int) -> uuid.UUID:
    """Stable example id so re-running the upsert updates instead of duplicating."""
    return uuid.uuid5(_NAMESPACE, f"scenario-{scenario}")


def build_examples(project: str = "SHIP") -> list[dict[str, Any]]:
    """One example per demo ticket; pure (no network)."""
    return [
        {
            "id": example_id(t.scenario),
            "inputs": {"ticket_key": f"{project}-{t.scenario}", "scenario": t.scenario},
            "outputs": {
                "expected": t.expected,
                "expected_subagents": EXPECTED_SUBAGENTS.get(t.scenario, []),
                "acceptance_criteria": extract_acceptance_criteria(t.description),
            },
            "metadata": {"summary": t.summary, "issue_type": t.issue_type},
        }
        for t in sorted(load_demo_tickets(), key=lambda t: t.scenario)
    ]


def upsert_dataset(
    client: langsmith.Client, name: str = DATASET_NAME, project: str = "SHIP"
) -> str:
    """Create the dataset if needed, then create/update its examples. Returns the dataset id."""
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(name, description="Shipwright demo scenarios (1-7)")
    existing = {str(e.id) for e in client.list_examples(dataset_id=dataset.id)}
    examples = build_examples(project)
    updates = [e for e in examples if str(e["id"]) in existing]
    creates = [e for e in examples if str(e["id"]) not in existing]
    if creates:
        client.create_examples(dataset_id=dataset.id, examples=creates)
    if updates:
        client.update_examples(dataset_id=dataset.id, updates=updates)
    return str(dataset.id)
