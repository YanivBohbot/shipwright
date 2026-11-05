"""LangSmith evaluators `(run, example) -> {"key", "score"}`.

`run.outputs` is the dict returned by `evals.harness.run_example`; `example.outputs` holds the
reference values of `evals.dataset`. A `score` of None means "not applicable" to the scenario.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

UNNEEDED_RESEARCHER_PENALTY = 0.5
BUGFIX_SCENARIO = 1
AMBIGUOUS_SCENARIO = 4
NO_CODE_SCENARIOS = (4, 7)  # ask / split only: developer and reviewer must not run


def _outputs(obj: Any) -> dict[str, Any]:
    return dict(getattr(obj, "outputs", None) or {})


def _scenario(example: Any) -> int | None:
    return (getattr(example, "inputs", None) or {}).get("scenario")


def outcome_matches(run: Any, example: Any) -> dict[str, Any]:
    out, ref = _outputs(run), _outputs(example)
    expected, outcome = ref.get("expected"), out.get("outcome")
    if expected == "draft_or_stop":
        # a stop is a clean stop or a comment-and-abandon; a PR is only fine as a draft
        ok = outcome in ("stopped", "questions") or (outcome == "pr" and bool(out.get("draft")))
    else:
        ok = outcome == expected
    return {"key": "outcome", "score": int(ok)}


def trajectory_subagents(run: Any, example: Any) -> dict[str, Any]:
    """Recall of the expected subagents; `researcher` called on the bugfix scenario is penalised."""
    called = set(_outputs(run).get("subagents") or [])
    expected = set(_outputs(example).get("expected_subagents") or [])
    score = len(expected & called) / len(expected) if expected else 1.0
    comment = f"called={sorted(called)} expected={sorted(expected)}"
    if _scenario(example) == BUGFIX_SCENARIO and "researcher" in called:
        score = max(0.0, score - UNNEEDED_RESEARCHER_PENALTY)
        comment += " (unneeded researcher)"
    if _scenario(example) in NO_CODE_SCENARIOS and called & {"developer", "reviewer"}:
        score = 0.0
        comment += " (developer/reviewer must not run)"
    return {"key": "trajectory_subagents", "score": score, "comment": comment}


def no_pr_when_ambiguous(run: Any, example: Any) -> dict[str, Any]:
    if _scenario(example) != AMBIGUOUS_SCENARIO:
        return {"key": "no_pr_when_ambiguous", "score": None, "comment": "n/a"}
    return {"key": "no_pr_when_ambiguous", "score": int(_outputs(run).get("pr_body") is None)}


def tests_green(run: Any, example: Any) -> dict[str, Any]:
    """Only meaningful where a green PR is expected (`expected == "pr"`)."""
    if _outputs(example).get("expected") != "pr":
        return {"key": "tests_green", "score": None, "comment": "n/a"}
    return {"key": "tests_green", "score": int(bool(_outputs(run).get("tests_passed")))}


tests_green.__test__ = False  # name starts with "tests_": keep pytest from collecting it


# ------------------------------------------------------------------ LLM judge

JUDGE_RUBRIC = """\
You are a strict reviewer of pull request descriptions written by an autonomous coding agent.

You receive the pull request description and the reference acceptance criteria (AC) of the Jira
ticket, numbered from 1. For EACH acceptance criterion decide whether the description ticks it
off WITH EVIDENCE.

A criterion is covered only if all of the following hold:
- the description contains a checked item (for example `- [x]`) for that criterion, and
- that item points to concrete proof: a test name or file, a command output, or a precise
  behaviour or code location. A bare tick, or a vague claim such as "done" or "implemented",
  is NOT evidence.

Return one verdict per criterion, identified by its number in `index` (you may paraphrase the
criterion text). Put the quoted proof in `evidence`, or an empty string when there is none.
"""


class CriterionVerdict(BaseModel):
    index: int = Field(description="1-based number of the acceptance criterion in the input list.")
    criterion: str = Field(default="", description="The criterion text (informative only).")
    covered: bool = Field(description="True only if ticked off in the PR body with evidence.")
    evidence: str = Field(default="", description="Quoted proof from the PR body, or empty.")


class CriteriaVerdict(BaseModel):
    verdicts: list[CriterionVerdict]


def _default_judge() -> Any:
    from langchain.chat_models import init_chat_model

    from shipwright.config import get_settings

    return init_chat_model(get_settings().model_light)


def make_criteria_covered(judge: Any | None = None) -> Callable[[Any, Any], dict[str, Any]]:
    """Build the `criteria_covered` LLM-judge evaluator.

    `judge` is any chat model supporting `with_structured_output`; default is
    `init_chat_model(settings.model_light)`, created lazily on first use.
    """
    chain: Any = None

    def criteria_covered(run: Any, example: Any) -> dict[str, Any]:
        nonlocal chain
        pr_body = _outputs(run).get("pr_body")
        criteria = list(_outputs(example).get("acceptance_criteria") or [])
        if pr_body is None or not criteria:
            return {"key": "criteria_covered", "score": None, "comment": "n/a"}
        if chain is None:
            model = judge if judge is not None else _default_judge()
            chain = model.with_structured_output(CriteriaVerdict)
        listing = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, start=1))
        prompt = f"Acceptance criteria:\n{listing}\n\nPull request description:\n{pr_body}"
        result = chain.invoke([SystemMessage(content=JUDGE_RUBRIC), HumanMessage(content=prompt)])
        covered = {v.index for v in result.verdicts if v.covered and v.evidence.strip()}
        hits = sum(i in covered for i in range(1, len(criteria) + 1))
        return {
            "key": "criteria_covered",
            "score": hits / len(criteria),
            "comment": f"{hits}/{len(criteria)} criteria covered with evidence",
        }

    return criteria_covered


criteria_covered = make_criteria_covered()
