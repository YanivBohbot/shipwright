import json

import pytest
from langchain_core.tools import StructuredTool, ToolException

from shipwright.integrations.github import FileChange, McpGitHubGateway
from shipwright.integrations.mcp_client import McpToolError
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


async def test_get_branch_sha_paginates():
    sha = "e" * 40
    pages = {
        1: [{"name": f"b{i}"} for i in range(100)],
        2: [{"name": "target", "commit": {"sha": sha}}],
    }
    tool = FakeTool(result=lambda a: json.dumps(pages.get(a["page"], [])))
    gw = McpGitHubGateway(tools={"list_branches": tool})
    assert await gw.get_branch_sha(REPO, "target") == sha
    assert [c["page"] for c in tool.calls] == [1, 2]
    assert tool.calls[0]["owner"] == "acme"


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


async def test_fakes_behave_like_gateways():
    gh = FakeGitHubGateway(existing_branches=["a"], open_prs={"h": "u"})
    assert await gh.list_branch_names(REPO) == {"a"}
    assert await gh.find_open_pull_request(REPO, "h") == "u"
    await gh.create_branch(REPO, "z")
    assert await gh.list_branch_names(REPO) == {"a", "z"}
    url = await gh.create_pull_request(REPO, "z", "main", "t", "b")
    assert url == "https://github.com/fake/repo/pull/1"
    assert gh.prs[0]["head"] == "z"
    assert set(gh.repos) == {REPO}
    jira = FakeJiraGateway({})
    assert await jira.create_issue("SHIP", "s", "d", "Bug") == "SHIP-201"


async def test_get_branch_sha_raises_when_too_many_branches():
    tool = FakeTool(
        result=lambda a: json.dumps([{"name": f"b{a['page']}-{i}"} for i in range(100)])
    )
    gw = McpGitHubGateway(tools={"list_branches": tool})
    with pytest.raises(RuntimeError):
        await gw.get_branch_sha(REPO, "missing")


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


@pytest.mark.parametrize(
    "tool",
    [
        FakeTool(name="create_branch", raises=ToolException("422 Reference already exists")),
        FakeTool(name="create_branch", error_content="422 Reference already exists"),
    ],
)
async def test_create_branch_error_raises_mcp_tool_error(tool):
    gw = McpGitHubGateway(tools={"create_branch": tool})
    with pytest.raises(McpToolError, match="Reference already exists") as ei:
        await gw.create_branch(REPO, "shipwright/ship-2")
    assert ei.value.tool_name == "create_branch"


def _structured(handle_tool_error: bool) -> StructuredTool:
    async def create_branch(**kwargs) -> tuple[str, None]:
        raise ToolException("422 Reference already exists")

    return StructuredTool(
        name="create_branch",
        description="d",
        args_schema={"type": "object", "properties": {}},
        coroutine=create_branch,
        response_format="content_and_artifact",
        handle_tool_error=handle_tool_error,
    )


@pytest.mark.parametrize("handled", [True, False])
async def test_real_structured_tool_errors_are_detected_either_way(handled):
    # same StructuredTool shape the MCP adapter builds, with and without handle_tool_errors
    gw = McpGitHubGateway(tools={"create_branch": _structured(handled)})
    with pytest.raises(McpToolError, match="Reference already exists"):
        await gw.create_branch(REPO, "shipwright/ship-2")


async def test_push_files_error_raises_mcp_tool_error():
    gw = McpGitHubGateway(tools={"push_files": FakeTool(error_content="409 conflict")})
    with pytest.raises(McpToolError):
        await gw.push_files(REPO, "b", [FileChange("a.py", "x")], "m")


async def test_list_branch_names_pages_once_through_all_branches():
    pages = {1: [{"name": f"b{i}"} for i in range(100)], 2: [{"name": "main"}]}
    tool = FakeTool(result=lambda a: json.dumps(pages.get(a["page"], [])))
    gw = McpGitHubGateway(tools={"list_branches": tool})
    names = await gw.list_branch_names(REPO)
    assert "main" in names and "b99" in names and len(names) == 101
    assert [c["page"] for c in tool.calls] == [1, 2]
    assert await FakeGitHubGateway(existing_branches={"x"}).list_branch_names(REPO) == {"x"}
