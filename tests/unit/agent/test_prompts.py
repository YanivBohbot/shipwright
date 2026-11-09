"""Rules the prompts must keep: each one comes from a real run that went wrong.

SHIP-5 (an acceptance criterion requiring real calls to `partner.example.invalid` during the
tests): the clarification gate let it through, the developer mocked the service to get green
tests, and after the last review cycle the orchestrator published a normal (non-draft) PR.
"""

from shipwright.agent.prompts.developer import DEVELOPER_PROMPT
from shipwright.agent.prompts.orchestrator import ORCHESTRATOR_PROMPT


def flat(text: str) -> str:
    return " ".join(text.split())


def test_clarification_gate_catches_criteria_that_cannot_be_met_as_written():
    prompt = flat(ORCHESTRATOR_PROMPT)
    assert "cannot be met as written" in prompt
    assert "`.invalid`" in prompt


def test_unmet_criterion_after_the_last_review_cycle_makes_the_pr_a_draft():
    prompt = flat(ORCHESTRATOR_PROMPT)
    assert "still reports an acceptance criterion as not met, the PR is a draft" in prompt


def test_developer_never_fakes_what_a_criterion_requires():
    prompt = flat(DEVELOPER_PROMPT)
    assert "Never mock, fake or skip a dependency that a criterion says must really be" in prompt
    assert "STATUS: TESTS_FAILING" in prompt
