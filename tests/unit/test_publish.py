"""publish_pull_request: approval/hash guard, branch/PR reuse, CHANGELOG, title/body rules."""

import hashlib
import uuid

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from shipwright.context import ShipwrightState
from shipwright.integrations.github import FileChange
from shipwright.tools.publish import insert_changelog_entry, make_publish_tool
from shipwright.tools.review_gate import make_review_gate_tool
from shipwright.workspace import WorkspaceError
from tests.fakes import FAKE_PR_URL, FakeGitHubGateway, FakeWorkspace, settings_stub

REPO = "owner/taskboard-api"
BRANCH = "shipwright/ship-2"
DIFF = "diff --git a/app/a.py b/app/a.py\n+fixed\n"
H = hashlib.sha256(DIFF.encode()).hexdigest()
FILES = [FileChange("app/a.py", "fixed\n"), FileChange("app/old.py", None)]
CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n- Older entry\n\n## [0.1.0]\n\n- Initial\n"
ARGS = {
    "ticket_key": "SHIP-2",
    "title": "Export tasks as CSV",
    "body": "Implements CSV export.",
    "changelog_entry": "Add CSV export of tasks (SHIP-2)",
}
URL = "https://github.com/owner/taskboard-api/pull/7"


def _ws(**kwargs) -> FakeWorkspace:
    kwargs.setdefault("diff", DIFF)
    kwargs.setdefault("files", FILES)
    kwargs.setdefault("repo_files", {"CHANGELOG.md": CHANGELOG})
    return FakeWorkspace(**kwargs)


async def _publish(ws, gh, state: dict | None = None, **overrides) -> str:
    tool = make_publish_tool(ws, gh, settings_stub(shipwright_repo=REPO))
    state = {"approved_diff_hash": H} if state is None else state
    return await tool.ainvoke({**ARGS, **overrides, "state": state})


def _nothing_written(gh: FakeGitHubGateway) -> bool:
    return gh.branches == [] and gh.pushes == [] and gh.prs == [] and gh.pr_updates == []


def _pushed(gh: FakeGitHubGateway) -> dict[str, str | None]:
    return {f.path: f.content for f in gh.pushes[0][2]}


async def test_publish_refuses_without_approval():
    for state in ({}, {"approved_diff_hash": None}):
        gh = FakeGitHubGateway()
        out = await _publish(_ws(), gh, state=state)
        assert "not approved" in out.lower()
        assert gh.prs == [] and _nothing_written(gh)


async def test_publish_refuses_when_diff_changed_after_approval():
    gh = FakeGitHubGateway()
    out = await _publish(_ws(diff=DIFF + "+more\n"), gh)
    assert "changed since" in out.lower() and _nothing_written(gh)


async def test_publish_refuses_when_approved_changes_disappeared():
    # sandbox expired and was recreated: empty diff, approval from the old tree
    gh = FakeGitHubGateway()
    out = await _publish(_ws(diff="", files=[]), gh)
    assert "changed since" in out.lower() and _nothing_written(gh)


async def test_publish_refuses_empty_changeset_even_if_hash_matches():
    gh = FakeGitHubGateway()
    out = await _publish(_ws(files=[]), gh)
    assert "no changed files" in out.lower() and _nothing_written(gh)


async def test_publish_rechecks_hash_after_reading_files():
    ws = _ws(hashes=[H, "tampered"])
    gh = FakeGitHubGateway()
    out = await _publish(ws, gh)
    assert "changed since" in out.lower() and _nothing_written(gh)
    assert ws.calls.index("changed_files") < len(ws.calls) - 1
    assert ws.calls[-1] == "diff_hash" and ws.calls.count("diff_hash") == 2
    assert "read:CHANGELOG.md" in ws.calls[: ws.calls.index("diff_hash", 1)]


async def test_publish_reports_workspace_error_as_text():
    class Broken(FakeWorkspace):
        async def diff_hash(self) -> str:
            raise WorkspaceError("No valid base sha recorded")

    gh = FakeGitHubGateway()
    out = await _publish(Broken(), gh)
    assert "No valid base sha recorded" in out and _nothing_written(gh)


async def test_publish_rejects_invalid_ticket_key():
    gh = FakeGitHubGateway()
    out = await _publish(_ws(), gh, ticket_key="../main")
    assert "not found in project SHIP" in out and _nothing_written(gh)


async def test_publish_requires_changelog_entry():
    gh = FakeGitHubGateway()
    out = await _publish(_ws(), gh, changelog_entry="  ")
    assert "changelog_entry" in out and _nothing_written(gh)


async def test_publish_creates_branch_files_changelog_and_pr():
    gh = FakeGitHubGateway()
    out = await _publish(_ws(), gh, ticket_key="ship-2")
    assert out == FAKE_PR_URL
    assert gh.branches == [(REPO, BRANCH, "main")]
    assert "CHANGELOG.md" in [f.path for f in gh.pushes[0][2]]
    repo, branch, _, message = gh.pushes[0]
    assert (repo, branch) == (REPO, BRANCH) and "SHIP-2" in message
    pushed = _pushed(gh)
    assert pushed["app/a.py"] == "fixed\n" and pushed["app/old.py"] is None
    assert pushed["CHANGELOG.md"].startswith(
        "# Changelog\n\n## [Unreleased]\n\n- Add CSV export of tasks (SHIP-2)\n- Older entry\n"
    )
    (pr,) = gh.prs
    assert pr["title"].startswith("[SHIP-2] ") and "SHIP-2" in pr["body"]
    assert pr["title"] == "[SHIP-2] Export tasks as CSV"
    assert pr["body"] == "Linked ticket: SHIP-2\n\nImplements CSV export."
    assert (pr["repo"], pr["head"], pr["base"], pr["draft"]) == (REPO, BRANCH, "main", False)


async def test_publish_reuses_existing_branch_and_pr():
    gh = FakeGitHubGateway(existing_branches={BRANCH}, open_prs={BRANCH: URL})
    out = await _publish(_ws(), gh)
    assert out == URL
    assert gh.branches == [] and gh.prs == []
    assert len(gh.pushes) == 1 and gh.pushes[0][1] == BRANCH
    assert gh.pr_updates == [
        (
            REPO,
            URL,
            "[SHIP-2] Export tasks as CSV",
            "Linked ticket: SHIP-2\n\nImplements CSV export.",
        )
    ]


async def test_publish_existing_branch_without_pr_creates_pr():
    gh = FakeGitHubGateway(existing_branches={BRANCH})
    assert await _publish(_ws(), gh) == FAKE_PR_URL
    assert gh.branches == [] and len(gh.prs) == 1


async def test_publish_does_not_double_prefix_title_or_body():
    gh = FakeGitHubGateway()
    await _publish(_ws(), gh, title="[SHIP-2] Export tasks as CSV", body="Closes SHIP-2.")
    assert gh.prs[0]["title"] == "[SHIP-2] Export tasks as CSV"
    assert gh.prs[0]["body"] == "Closes SHIP-2."
    gh2 = FakeGitHubGateway()
    await _publish(_ws(), gh2, title="[ship-2]   Export")
    assert gh2.prs[0]["title"] == "[SHIP-2] Export"


async def test_publish_draft_flag():
    gh = FakeGitHubGateway()
    await _publish(_ws(), gh, draft=True)
    assert gh.prs[0]["draft"] is True


async def test_publish_creates_changelog_when_absent():
    gh = FakeGitHubGateway()
    await _publish(_ws(repo_files={}), gh)
    assert _pushed(gh)["CHANGELOG.md"] == (
        "# Changelog\n\n## [Unreleased]\n\n- Add CSV export of tasks (SHIP-2)\n"
    )


async def test_publish_uses_developer_edited_changelog_once():
    edited = "# Changelog\n\n## [Unreleased]\n\n- Dev note\n"
    files = [*FILES, FileChange("CHANGELOG.md", edited)]
    ws = _ws(files=files)
    gh = FakeGitHubGateway()
    await _publish(ws, gh)
    paths = [f.path for f in gh.pushes[0][2]]
    assert paths.count("CHANGELOG.md") == 1
    assert _pushed(gh)["CHANGELOG.md"] == (
        "# Changelog\n\n## [Unreleased]\n\n- Add CSV export of tasks (SHIP-2)\n- Dev note\n"
    )
    assert "read:CHANGELOG.md" not in ws.calls


async def test_publish_reports_github_errors_as_text():
    class Broken(FakeGitHubGateway):
        async def push_files(self, repo, branch, files, message):
            raise RuntimeError("403 Resource not accessible")

    gh = Broken()
    out = await _publish(_ws(), gh)
    assert "403 Resource not accessible" in out and gh.prs == []


# ---------------------------------------------------------------- CHANGELOG insertion


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (None, "# Changelog\n\n## [Unreleased]\n\n- New\n"),
        ("", "# Changelog\n\n## [Unreleased]\n\n- New\n"),
        (
            "# Changelog\n\n## [Unreleased]\n\n- Old\n",
            "# Changelog\n\n## [Unreleased]\n\n- New\n- Old\n",
        ),
        (
            "# Changelog\n\n## [Unreleased]\n### Added\n- Old\n",
            "# Changelog\n\n## [Unreleased]\n\n- New\n\n### Added\n- Old\n",
        ),
        (
            "# Changelog\n\nAll notable changes.\n\n## [0.1.0]\n- Initial\n",
            "# Changelog\n\n## [Unreleased]\n\n- New\n\n"
            "All notable changes.\n\n## [0.1.0]\n- Initial\n",
        ),
        ("## [0.1.0]\n- Initial\n", "## [Unreleased]\n\n- New\n\n## [0.1.0]\n- Initial\n"),
        (
            "# Changelog\r\n\r\n## [unreleased]\r\n\r\n- Old\r\n",
            "# Changelog\r\n\r\n## [unreleased]\r\n\r\n- New\r\n- Old\r\n",
        ),
        (  # already present: unchanged (re-publication)
            "# Changelog\n\n## [Unreleased]\n\n- New\n",
            "# Changelog\n\n## [Unreleased]\n\n- New\n",
        ),
    ],
)
def test_insert_changelog_entry(before, after):
    assert insert_changelog_entry(before, "New") == after


def test_insert_changelog_entry_normalises_bullet_and_newlines():
    out = insert_changelog_entry("", "- Fix crash\non empty title")
    assert out == "# Changelog\n\n## [Unreleased]\n\n- Fix crash on empty title\n"
    assert insert_changelog_entry("", "* Starred").endswith("- Starred\n")


# ---------------------------------------------------------------- end to end in a graph


async def test_gate_approval_flows_to_publish_through_graph_state():
    ws = _ws()
    gh = FakeGitHubGateway()
    builder = StateGraph(ShipwrightState)
    tools = [
        make_review_gate_tool(ws),
        make_publish_tool(ws, gh, settings_stub(shipwright_repo=REPO)),
    ]
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}

    def call(name: str, args: dict, cid: str) -> dict:
        return {
            "messages": [
                AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])
            ]
        }

    # publishing before approval is refused
    result = await graph.ainvoke(call("publish_pull_request", ARGS, "p0"), cfg)
    assert "not approved" in result["messages"][-1].content.lower() and gh.pushes == []

    gate_args = {"summary": "s", "test_report": "ok", "review_report": "pass"}
    await graph.ainvoke(call("request_human_review", gate_args, "g1"), cfg)
    await graph.ainvoke(Command(resume={"type": "approve"}), cfg)
    result = await graph.ainvoke(call("publish_pull_request", ARGS, "p1"), cfg)
    last = result["messages"][-1]
    assert isinstance(last, ToolMessage) and last.content == FAKE_PR_URL
    assert len(gh.prs) == 1
