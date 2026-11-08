"""Workspace helpers over a fake sandbox: clone, diff, changed files."""

import pytest

from shipwright.sandbox.workspace import (
    REPO_DIR,
    WorkspaceError,
    changed_files,
    clone_repo,
    workspace_diff,
)
from tests.fakes import FakeSandbox


async def test_clone_is_skipped_when_repo_exists():
    box = FakeSandbox().on("test -d", exit_code=0)
    await clone_repo(box, "acme/api")
    assert not any("git clone" in c for c in box.executed)


async def test_clone_clones_and_disables_push():
    box = FakeSandbox().on("test -d", exit_code=1)
    await clone_repo(box, "acme/api")
    assert any('git clone --depth 50 "https://github.com/acme/api.git"' in c for c in box.executed)
    assert any("set-url --push origin" in c for c in box.executed)


async def test_failed_command_raises():
    box = FakeSandbox().on("test -d", exit_code=1).on("git clone", "fatal: not found", 128)
    with pytest.raises(WorkspaceError, match="exit 128"):
        await clone_repo(box, "acme/api")


async def test_diff_includes_new_files():
    box = FakeSandbox().on("diff --no-color HEAD", "diff --git a/x b/x")
    assert await workspace_diff(box) == "diff --git a/x b/x"
    assert any("add -N ." in c for c in box.executed)


async def test_changed_files_returns_contents_and_deletions():
    status = "M\tapp/main.py\nA\ttests/test_new.py\nD\told.py\n"
    box = FakeSandbox(
        {f"{REPO_DIR}/app/main.py": b"print(1)\n", f"{REPO_DIR}/tests/test_new.py": b"ok\n"}
    ).on("--name-status", status)
    result = await changed_files(box)
    assert result == {
        "files": [
            {"path": "app/main.py", "content": "print(1)\n"},
            {"path": "tests/test_new.py", "content": "ok\n"},
        ],
        "deleted": ["old.py"],
    }


async def test_changed_file_that_cannot_be_read_raises():
    box = FakeSandbox().on("--name-status", "M\tmissing.py\n")
    with pytest.raises(WorkspaceError, match="missing.py"):
        await changed_files(box)
