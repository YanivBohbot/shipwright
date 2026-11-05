"""Smoke-test verification logic (fake gateways, no network)."""

import json

import pytest

from shipwright.smoke import comment_bodies, key_for_scenario, verify_outcome
from shipwright.tickets import Ticket
from tests.fakes import FakeGitHubGateway

REPO = "me/taskboard-api"
URL = "https://github.com/me/taskboard-api/pull/7"


def _ticket(status="In Review"):
    return Ticket("SHIP-2", "s", "d", status, [], [], "Story")


async def test_ok_picks_newest_revision_with_open_pr():
    gh = FakeGitHubGateway(
        existing_branches={"shipwright/SHIP-2", "shipwright/SHIP-2-r2", "shipwright/SHIP-20"},
        open_prs={"shipwright/SHIP-2": "old", "shipwright/SHIP-2-r2": URL},
    )
    r = await verify_outcome(gh, REPO, _ticket(), [f"PR opened: {URL}"])
    assert r.ok and r.pr_url == URL and r.branch == "shipwright/SHIP-2-r2"


async def test_no_branch():
    r = await verify_outcome(FakeGitHubGateway(), REPO, _ticket(), [])
    assert not r.ok and r.pr_url is None and "no branch" in r.problems[0]


async def test_branch_without_open_pr():
    gh = FakeGitHubGateway(existing_branches={"shipwright/SHIP-2"})
    r = await verify_outcome(gh, REPO, _ticket(), [])
    assert any("no open PR" in p for p in r.problems)


async def test_missing_comment_and_wrong_status():
    gh = FakeGitHubGateway(
        existing_branches={"shipwright/SHIP-2"}, open_prs={"shipwright/SHIP-2": URL}
    )
    r = await verify_outcome(gh, REPO, _ticket("To Do"), ["unrelated"])
    assert len(r.problems) == 2
    assert any("comment" in p for p in r.problems) and any("To Do" in p for p in r.problems)


def test_comment_bodies():
    assert comment_bodies({"comments": [{"body": "a"}, "b", {"x": 1}, 3]}) == ["a", "b"]
    assert comment_bodies(None) == [] and comment_bodies({"comments": None}) == []


def test_key_for_scenario(tmp_path):
    p = tmp_path / "seeded.json"
    with pytest.raises(ValueError, match="seed_jira"):
        key_for_scenario(2, p)
    p.write_text(json.dumps({"2": "SHIP-2"}), encoding="utf-8")
    assert key_for_scenario(2, p) == "SHIP-2"
    with pytest.raises(ValueError, match="Scenario 9"):
        key_for_scenario(9, p)
