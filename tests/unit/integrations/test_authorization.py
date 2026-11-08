"""The authorization and audit interceptors, called the way MultiServerMCPClient calls them."""

from types import SimpleNamespace

import pytest
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult, TextContent

from shipwright.integrations.authorization import audit_interceptor, authorize_interceptor
from tests.fakes import MemoryAuditSink, make_settings

REPO = {"owner": "acme", "repo": "taskboard-api"}
OK = CallToolResult(content=[TextContent(type="text", text="done")], isError=False)


async def call(name, args, server, interceptor):
    sent = []

    async def handler(request):
        sent.append(request)
        return OK

    result = await interceptor(
        MCPToolCallRequest(name=name, args=args, server_name=server), handler
    )
    return result, sent


def authorize():
    return authorize_interceptor(make_settings(jira_project="SHIP"))


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("list_commits", REPO),
        ("search_code", {"query": "assignee repo:acme/taskboard-api"}),
        ("create_branch", {**REPO, "branch": "shipwright/ship-1", "from_branch": "main"}),
        ("push_files", {**REPO, "branch": "shipwright/ship-1", "files": [], "message": "m"}),
        ("create_pull_request", {**REPO, "head": "shipwright/ship-1", "base": "main"}),
    ],
)
async def test_github_calls_inside_the_scope_are_sent(name, args):
    result, sent = await call(name, args, "github", authorize())
    assert result is OK and len(sent) == 1


@pytest.mark.parametrize(
    ("name", "args", "reason"),
    [
        ("list_commits", {"owner": "evil", "repo": "x"}, "only the repository"),
        ("search_code", {"query": "token"}, "limited to repo:"),
        ("search_code", {"query": "x repo:acme/taskboard-api org:acme"}, "limited to repo:"),
        ("push_files", {**REPO, "branch": "main", "files": []}, "shipwright/*"),
        ("create_branch", {**REPO, "branch": "shipwright/x", "from_branch": "dev"}, "from main"),
        ("create_pull_request", {**REPO, "head": "feature", "base": "main"}, "into main"),
        ("create_pull_request", {**REPO, "head": "shipwright/x", "base": "prod"}, "into main"),
    ],
)
async def test_github_calls_outside_the_scope_are_refused(name, args, reason):
    result, sent = await call(name, args, "github", authorize())
    assert sent == []  # never reaches GitHub
    assert result.isError and "NOT AUTHORIZED" in result.content[0].text
    assert reason in result.content[0].text


@pytest.mark.parametrize(
    ("name", "args", "allowed"),
    [
        ("jira_get_issue", {"issue_key": "SHIP-4"}, True),
        ("jira_add_comment", {"issue_key": "ship-4", "body": "hi"}, True),
        ("jira_add_comment", {"issue_key": "HR-1", "body": "hi"}, False),
        ("jira_transition_issue", {"issue_key": "SHIP-4 OR 1=1"}, False),
        ("jira_create_issue", {"project_key": "SHIP", "summary": "s"}, True),
        ("jira_create_issue", {"project_key": "OPS", "summary": "s"}, False),
        ("jira_search", {"jql": "project = SHIP AND text ~ csv"}, True),
        ("jira_search", {"jql": "text ~ salary"}, False),
    ],
)
async def test_jira_scope(name, args, allowed):
    result, sent = await call(name, args, "atlassian", authorize())
    assert (len(sent) == 1) is allowed
    assert bool(result.isError) is not allowed


async def test_audit_records_writes_with_user_and_thread_and_skips_reads():
    sink = MemoryAuditSink()
    audit = audit_interceptor(sink, {"jira_add_comment"})
    runtime = SimpleNamespace(
        context=SimpleNamespace(user_id="alice"), config={"configurable": {"thread_id": "t1"}}
    )

    async def handler(request):
        return OK

    write = MCPToolCallRequest(
        name="jira_add_comment",
        args={"issue_key": "SHIP-1", "body": "token ghp_" + "a" * 36},
        server_name="atlassian",
        runtime=runtime,
    )
    await audit(write, handler)
    read = MCPToolCallRequest(name="jira_get_issue", args={}, server_name="atlassian")
    await audit(read, handler)
    (event,) = sink.events
    assert (event.tool, event.outcome, event.user_id, event.thread_id) == (
        "jira_add_comment",
        "ok",
        "alice",
        "t1",
    )
    assert "ghp_" not in event.args["body"]  # secrets redacted


async def test_a_refused_write_is_audited_as_an_error():
    sink = MemoryAuditSink()
    audit = audit_interceptor(sink, {"push_files"})
    authorized = authorize()

    async def chain(request, handler=None):
        async def real(req):
            return OK

        return await authorized(request, real)

    request = MCPToolCallRequest(
        name="push_files", args={**REPO, "branch": "main"}, server_name="github"
    )
    await audit(request, chain)
    assert sink.events[0].outcome == "error"
