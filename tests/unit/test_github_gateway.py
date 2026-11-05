import json

import pytest

from shipwright.integrations.github import FileChange, McpGitHubGateway
from tests.fakes import FAKE_BASE_SHA, FakeGitHubGateway, FakeJiraGateway, FakeTool

REPO = "acme/taskboard-api"
PR_URL = "https://github.com/acme/taskboard-api/pull/9"


async def test_create_branch_splits_repo():
    tool = FakeTool(result="{}")
    gw = McpGitHubGateway(tools={"create_branch": tool})
    await gw.create_branch(REPO, "feat/x", from_branch="develop")
    assert tool.calls == [
        {"owner": "acme", "repo": "taskboard-api", "branch": "feat/x", "from_branch": "develop"}
    ]


async def test_bad_repo_format():
    gw = McpGitHubGateway(tools={"create_branch": FakeTool()})
    with pytest.raises(ValueError):
        await gw.create_branch("noslash", "b")


async def test_push_files_passes_contents():
    push = FakeTool(result=json.dumps({"ref": "refs/heads/feat/x", "object": {"sha": "abc123"}}))
    gw = McpGitHubGateway(tools={"push_files": push})
    files = [FileChange("a.py", "print(1)"), FileChange("b/c.md", "# hi")]
    sha = await gw.push_files(REPO, "feat/x", files, "feat: add")
    assert sha == "abc123"
    assert push.calls == [
        {
            "owner": "acme",
            "repo": "taskboard-api",
            "branch": "feat/x",
            "files": [
                {"path": "a.py", "content": "print(1)"},
                {"path": "b/c.md", "content": "# hi"},
            ],
            "message": "feat: add",
        }
    ]


async def test_push_files_deletions_use_delete_file():
    push = FakeTool(result=json.dumps({"object": {"sha": "s1"}}))
    delete = FakeTool(result=json.dumps({"commit": {"sha": "s2"}}))
    gw = McpGitHubGateway(tools={"push_files": push, "delete_file": delete})
    files = [FileChange("keep.py", "x"), FileChange("old.py", None)]
    assert await gw.push_files(REPO, "b", files, "chore") == "s2"
    assert push.calls[0]["files"] == [{"path": "keep.py", "content": "x"}]
    assert delete.calls == [
        {
            "owner": "acme",
            "repo": "taskboard-api",
            "branch": "b",
            "path": "old.py",
            "message": "chore",
        }
    ]


async def test_push_only_deletions_skips_push_files():
    push = FakeTool()
    delete = FakeTool(result=json.dumps({"commit": {"sha": "s2"}}))
    gw = McpGitHubGateway(tools={"push_files": push, "delete_file": delete})
    assert await gw.push_files(REPO, "b", [FileChange("old.py", None)], "m") == "s2"
    assert push.calls == []


async def test_create_pull_request_returns_url():
    tool = FakeTool(result=json.dumps({"id": "1", "url": PR_URL}))
    gw = McpGitHubGateway(tools={"create_pull_request": tool})
    url = await gw.create_pull_request(REPO, "feat/x", "main", "T", "B", draft=True)
    assert url == PR_URL
    assert tool.calls == [
        {
            "owner": "acme",
            "repo": "taskboard-api",
            "head": "feat/x",
            "base": "main",
            "title": "T",
            "body": "B",
            "draft": True,
        }
    ]


async def test_branch_exists():
    tool = FakeTool(result=json.dumps([{"name": "main"}, {"name": "feat/x"}]))
    gw = McpGitHubGateway(tools={"list_branches": tool})
    assert await gw.branch_exists(REPO, "feat/x") is True
    assert await gw.branch_exists(REPO, "nope") is False
    assert tool.calls[0]["owner"] == "acme"


async def test_branch_exists_paginates():
    pages = {1: [{"name": f"b{i}"} for i in range(100)], 2: [{"name": "target"}]}
    tool = FakeTool(result=lambda a: json.dumps(pages.get(a["page"], [])))
    gw = McpGitHubGateway(tools={"list_branches": tool})
    assert await gw.branch_exists(REPO, "target") is True
    assert [c["page"] for c in tool.calls] == [1, 2]


async def test_find_open_pull_request():
    tool = FakeTool(result=json.dumps([{"number": 9, "html_url": PR_URL}]))
    gw = McpGitHubGateway(tools={"list_pull_requests": tool})
    assert await gw.find_open_pull_request(REPO, "feat/x") == PR_URL
    assert tool.calls == [
        {"owner": "acme", "repo": "taskboard-api", "head": "acme:feat/x", "state": "open"}
    ]


async def test_find_open_pull_request_none():
    gw = McpGitHubGateway(tools={"list_pull_requests": FakeTool(result="[]")})
    assert await gw.find_open_pull_request(REPO, "feat/x") is None


async def test_update_pull_request_uses_number_from_url():
    tool = FakeTool(result="{}")
    gw = McpGitHubGateway(tools={"update_pull_request": tool})
    await gw.update_pull_request(REPO, PR_URL, "T2", "B2")
    assert tool.calls == [
        {"owner": "acme", "repo": "taskboard-api", "pullNumber": 9, "title": "T2", "body": "B2"}
    ]
    with pytest.raises(ValueError):
        await gw.update_pull_request(REPO, "https://example.com/x", "T", "B")


async def test_fakes_behave_like_gateways():
    gh = FakeGitHubGateway(existing_branches=["a"], open_prs={"h": "u"})
    assert await gh.branch_exists(REPO, "a") and not await gh.branch_exists(REPO, "z")
    assert await gh.find_open_pull_request(REPO, "h") == "u"
    await gh.create_branch(REPO, "z")
    assert await gh.branch_exists(REPO, "z")
    url = await gh.create_pull_request(REPO, "z", "main", "t", "b")
    assert url == "https://github.com/fake/repo/pull/1"
    assert gh.prs[0]["head"] == "z"
    await gh.update_pull_request(REPO, url, "t2", "b2")
    assert gh.pr_updates == [(REPO, url, "t2", "b2")]
    jira = FakeJiraGateway({})
    assert await jira.create_issue("SHIP", "s", "d", "Bug") == "SHIP-201"


async def test_branch_exists_raises_when_too_many_branches():
    tool = FakeTool(
        result=lambda a: json.dumps([{"name": f"b{a['page']}-{i}"} for i in range(100)])
    )
    gw = McpGitHubGateway(tools={"list_branches": tool})
    with pytest.raises(RuntimeError):
        await gw.branch_exists(REPO, "missing")


async def test_get_branch_sha_reads_commit_sha_from_list_branches():
    sha = "c" * 40
    tool = FakeTool(
        result=json.dumps([{"name": "feat/x"}, {"name": "main", "commit": {"sha": sha}}])
    )
    gw = McpGitHubGateway(tools={"list_branches": tool})
    assert await gw.get_branch_sha(REPO, "main") == sha
    with pytest.raises(RuntimeError, match="not found"):
        await gw.get_branch_sha(REPO, "nope")
    with pytest.raises(RuntimeError, match="no commit sha"):
        await gw.get_branch_sha(REPO, "feat/x")


async def test_fake_get_branch_sha_is_configurable():
    gh = FakeGitHubGateway(branch_shas={"main": "d" * 40})
    assert await gh.get_branch_sha(REPO, "main") == "d" * 40
    assert await FakeGitHubGateway().get_branch_sha(REPO, "main") == FAKE_BASE_SHA
