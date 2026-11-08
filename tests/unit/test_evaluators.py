"""Pure evaluators, the LLM judge (scripted model), and dataset building. Offline."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.runnables import RunnableLambda

from evals.dataset import TICKETS, build_examples, example_id, upsert_dataset
from evals.evaluators import (
    CriteriaVerdict,
    CriterionVerdict,
    make_criteria_covered,
    no_pr_when_ambiguous,
    outcome_matches,
    tests_green,
    trajectory_subagents,
)
from shipwright.domain.tickets import load_demo_tickets as _load


def load_demo_tickets():
    return _load(TICKETS)


def ex(scenario: int, **outputs):
    inputs = {"ticket_key": f"SHIP-{scenario}", "scenario": scenario}
    return SimpleNamespace(inputs=inputs, outputs=outputs)


def run(**outputs):
    return SimpleNamespace(outputs=outputs)


# ------------------------------------------------------------------ outcome


@pytest.mark.parametrize(
    ("expected", "out", "score"),
    [
        ("pr", {"outcome": "pr"}, 1),
        ("pr", {"outcome": "questions"}, 0),
        ("questions", {"outcome": "questions"}, 1),
        ("questions", {"outcome": "pr"}, 0),
        ("subtasks", {"outcome": "subtasks"}, 1),
        ("subtasks", {"outcome": "stopped"}, 0),
        ("draft_or_stop", {"outcome": "stopped"}, 1),
        ("draft_or_stop", {"outcome": "pr", "draft": True}, 1),
        ("draft_or_stop", {"outcome": "pr", "draft": False}, 0),
        ("draft_or_stop", {"outcome": "questions"}, 1),
        ("draft_or_stop", {"outcome": "subtasks"}, 0),
    ],
)
def test_outcome_matches(expected, out, score):
    result = outcome_matches(run(**out), ex(5, expected=expected))
    assert result["key"] == "outcome"
    assert result["score"] == score


# ------------------------------------------------------------------ trajectory


def test_trajectory_is_recall_of_expected_subagents():
    example = ex(2, expected_subagents=["researcher", "code_analyst", "developer", "reviewer"])
    full = trajectory_subagents(
        run(subagents=["researcher", "code_analyst", "developer", "reviewer"]), example
    )
    half = trajectory_subagents(run(subagents=["developer", "reviewer"]), example)
    assert full["key"] == "trajectory_subagents"
    assert full["score"] == 1.0
    assert half["score"] == 0.5


def test_trajectory_penalizes_unneeded_researcher():
    example = ex(1, expected_subagents=["developer", "reviewer"])
    clean = trajectory_subagents(run(subagents=["developer", "reviewer"]), example)
    noisy = trajectory_subagents(run(subagents=["researcher", "developer", "reviewer"]), example)
    assert clean["score"] == 1.0
    assert noisy["score"] < clean["score"]
    assert noisy["score"] < 0.8  # below the reference threshold


@pytest.mark.parametrize("scenario", [4, 7])
def test_trajectory_penalizes_code_agents_on_ask_or_split_scenarios(scenario):
    example = ex(scenario, expected_subagents=[])
    assert trajectory_subagents(run(subagents=["developer"]), example)["score"] == 0.0
    assert trajectory_subagents(run(subagents=["code_analyst"]), example)["score"] == 1.0


def test_trajectory_researcher_allowed_outside_scenario_1():
    example = ex(3, expected_subagents=["researcher", "developer"])
    result = trajectory_subagents(run(subagents=["researcher", "developer", "reviewer"]), example)
    assert result["score"] == 1.0


def test_trajectory_with_nothing_expected_scores_one():
    assert trajectory_subagents(run(subagents=[]), ex(4, expected_subagents=[]))["score"] == 1.0


# ------------------------------------------------------------------ no PR / tests


def test_no_pr_when_ambiguous():
    assert no_pr_when_ambiguous(run(pr_body=None), ex(4))["score"] == 1
    assert no_pr_when_ambiguous(run(pr_body="## Summary"), ex(4))["score"] == 0
    assert no_pr_when_ambiguous(run(pr_body="## Summary"), ex(2))["score"] is None  # n/a


def test_tests_green():
    assert tests_green(run(tests_passed=True), ex(1, expected="pr"))["score"] == 1
    assert tests_green(run(tests_passed=False), ex(1, expected="pr"))["score"] == 0
    # failing tests are the expected situation of scenario 5, and moot without a PR
    assert tests_green(run(tests_passed=False), ex(5, expected="draft_or_stop"))["score"] is None
    assert tests_green(run(tests_passed=False), ex(4, expected="questions"))["score"] is None


# ------------------------------------------------------------------ LLM judge (scripted)


class ScriptedJudge:
    """Chat-model double: `with_structured_output(schema)` returns a runnable yielding `verdict`."""

    def __init__(self, verdict):
        self.verdict = verdict
        self.prompts: list = []

    def with_structured_output(self, schema):
        assert schema is CriteriaVerdict

        def answer(messages):
            self.prompts.append(messages)
            return self.verdict

        return RunnableLambda(answer)


def verdict(*items):
    return CriteriaVerdict(verdicts=[CriterionVerdict(**i) for i in items])


def test_criteria_covered_is_fraction_with_evidence():
    judge = ScriptedJudge(
        verdict(
            {
                "index": 1,
                "criterion": "AC-1",
                "covered": True,
                "evidence": "- [x] AC-1 (tests/test_a.py)",
            },
            {
                "index": 2,
                "criterion": "AC-2",
                "covered": True,
                "evidence": "",
            },  # ticked without proof
            {"index": 3, "criterion": "AC-3", "covered": False, "evidence": ""},
            {
                "index": 4,
                "criterion": "AC-4",
                "covered": True,
                "evidence": "- [x] AC-4 via test_csv",
            },
        )
    )
    evaluator = make_criteria_covered(judge)
    example = ex(2, acceptance_criteria=["AC-1", "AC-2", "AC-3", "AC-4"])
    result = evaluator(run(pr_body="the body"), example)

    assert result["key"] == "criteria_covered"
    assert result["score"] == 0.5
    sent = str(judge.prompts[0])
    assert "the body" in sent and "3. AC-3" in sent and "evidence" in sent.lower()


def test_criteria_covered_missing_verdict_counts_as_uncovered():
    judge = ScriptedJudge(
        verdict({"index": 1, "criterion": "AC-1", "covered": True, "evidence": "proof"})
    )
    result = make_criteria_covered(judge)(
        run(pr_body="b"), ex(2, acceptance_criteria=["AC-1", "AC-2"])
    )
    assert result["score"] == 0.5


def test_criteria_covered_matches_by_index_so_paraphrases_count():
    judge = ScriptedJudge(
        verdict({"index": 2, "criterion": "reworded text", "covered": True, "evidence": "proof"})
    )
    result = make_criteria_covered(judge)(
        run(pr_body="b"), ex(2, acceptance_criteria=["AC-1", "AC-2"])
    )
    assert result["score"] == 0.5


def test_criteria_covered_not_applicable_without_pr_or_criteria():
    judge = ScriptedJudge(verdict())
    evaluator = make_criteria_covered(judge)
    assert evaluator(run(pr_body=None), ex(7, acceptance_criteria=["AC-1"]))["score"] is None
    assert evaluator(run(pr_body="b"), ex(4, acceptance_criteria=[]))["score"] is None
    assert judge.prompts == []  # the judge is not called


# ------------------------------------------------------------------ dataset


def test_build_examples_one_per_demo_ticket():
    tickets = load_demo_tickets()
    examples = build_examples("SHIP")
    assert len(examples) == len(tickets)
    by_scenario = {e["inputs"]["scenario"]: e for e in examples}
    assert by_scenario[2]["inputs"] == {"ticket_key": "SHIP-2", "scenario": 2}
    out2 = by_scenario[2]["outputs"]
    assert out2["expected"] == "pr"
    assert out2["acceptance_criteria"][0].startswith("AC-1")
    assert len(out2["acceptance_criteria"]) == 6
    assert {"researcher", "code_analyst"} <= set(out2["expected_subagents"])
    assert by_scenario[3]["outputs"]["expected"] == "pr"
    assert by_scenario[4]["outputs"]["expected_subagents"] == []
    assert "researcher" not in by_scenario[1]["outputs"]["expected_subagents"]
    assert by_scenario[2]["id"] == example_id(2)


class FakeLsClient:
    def __init__(self, existing_scenarios=()):
        self.datasets: dict[str, SimpleNamespace] = {}
        self.created: list = []
        self.updated: list = []
        self.existing = [SimpleNamespace(id=example_id(s)) for s in existing_scenarios]

    def has_dataset(self, *, dataset_name):
        return dataset_name in self.datasets

    def create_dataset(self, dataset_name, **kwargs):
        ds = SimpleNamespace(id="ds-1", name=dataset_name)
        self.datasets[dataset_name] = ds
        return ds

    def read_dataset(self, *, dataset_name):
        return self.datasets[dataset_name]

    def list_examples(self, *, dataset_id):
        return iter(self.existing)

    def create_examples(self, *, dataset_id, examples):
        self.created.extend(examples)

    def update_examples(self, *, dataset_id, updates):
        self.updated.extend(updates)


def test_upsert_dataset_creates_then_updates_idempotently():
    client = FakeLsClient()
    assert upsert_dataset(client, "demo-set") == "ds-1"
    assert len(client.created) == len(load_demo_tickets()) and not client.updated

    again = FakeLsClient(existing_scenarios=[1, 2])
    again.datasets["demo-set"] = SimpleNamespace(id="ds-1", name="demo-set")
    upsert_dataset(again, "demo-set")
    assert {u["id"] for u in again.updated} == {example_id(1), example_id(2)}
    assert all(c["id"] not in {example_id(1), example_id(2)} for c in again.created)


def test_expected_subagents_follow_the_spec():
    from evals.dataset import EXPECTED_SUBAGENTS as table

    for scenario in (1, 3, 6):
        assert "code_analyst" in table[scenario]
    assert table[5] == ["code_analyst", "developer"]
    assert table[4] == table[7] == []
