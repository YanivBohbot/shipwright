"""push_files must push exactly the sandbox's changed files (found by the real SHIP-2 run)."""

from types import SimpleNamespace

import pytest

from shipwright.agent.middleware import PushMatchesWorkspaceMiddleware
from shipwright.sandbox.workspace import REPO_DIR
from tests.fakes import FakeSandbox

FILES = {"app/models.py": b"x = 1\n", "tests/test_tasks.py": b"def test(): ...\n"}


def sandbox():
    box = FakeSandbox({f"{REPO_DIR}/{p}": c for p, c in FILES.items()})
    return box.on("--name-status", "".join(f"M\t{p}\n" for p in FILES))


def push(files):
    args = {"owner": "acme", "repo": "taskboard-api", "branch": "shipwright/x", "files": files}
    return SimpleNamespace(tool_call={"name": "push_files", "id": "c1", "args": args})


async def handler(request):
    return "pushed"


EXACT = [{"path": p, "content": c.decode()} for p, c in FILES.items()]


async def test_exact_files_are_pushed():
    assert (
        await PushMatchesWorkspaceMiddleware(sandbox()).awrap_tool_call(push(EXACT), handler)
        == "pushed"
    )


@pytest.mark.parametrize(
    ("files", "problem"),
    [
        # The real SHIP-2 mistake: the right content under the path ".".
        ([EXACT[0], {"path": ".", "content": EXACT[1]["content"]}], "unknown path '.'"),
        ([EXACT[0]], "missing tests/test_tasks.py"),
        ([EXACT[0], {**EXACT[1], "content": "def test(): pass\n"}], "content differs"),
    ],
)
async def test_any_copy_mistake_is_refused(files, problem):
    result = await PushMatchesWorkspaceMiddleware(sandbox()).awrap_tool_call(push(files), handler)
    assert result.status == "error" and "NOT PUSHED" in result.content
    assert problem in result.content


async def test_other_tools_pass_through():
    call = SimpleNamespace(tool_call={"name": "create_branch", "id": "c1", "args": {}})
    assert (
        await PushMatchesWorkspaceMiddleware(sandbox()).awrap_tool_call(call, handler) == "pushed"
    )
