"""Workspace against real git through LocalShellBackend (offline; cmd.exe on Windows)."""

import os
import subprocess
from pathlib import Path

import pytest

from shipwright.integrations.github.gateway import FileChange
from shipwright.sandbox.providers import open_workspace
from shipwright.sandbox.shell import parse_exit_code, q
from shipwright.sandbox.workspace import Workspace, WorkspaceError
from tests.fakes import FakeSandbox, make_settings


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
async def real_ws(tmp_path) -> tuple[Workspace, Path]:
    origin = tmp_path / "origin"
    (origin / "app").mkdir(parents=True)
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "core.autocrlf", "false")
    (origin / ".gitignore").write_text(".pytest_cache/\n__pycache__/\n", newline="\n")
    (origin / "app" / "a.py").write_text("print('a')\n", newline="\n")
    (origin / "app" / "c.py").write_text("print('c')\n", newline="\n")
    git(origin, "add", ".")
    git(origin, "commit", "-q", "-m", "init")
    parent = tmp_path / "parent"  # the workspace lives inside an unrelated git repo
    parent.mkdir()
    git(parent, "init", "-q")
    ws = await open_workspace(
        make_settings(), thread_id="t1", clone_url=origin.as_uri(), local_root=parent / "ws"
    )
    assert await ws.ensure_cloned() is True
    return ws, parent / "ws" / "t1" / "workspace" / "repo"


async def test_clone_records_base_sha_outside_repo(real_ws):
    ws, repo = real_ws
    assert await ws.base_sha() == git(repo, "rev-parse", "HEAD").strip()
    assert (repo.parent / ".shipwright" / "base_sha").is_file()
    assert await ws.ensure_cloned() is False


async def test_changed_files_added_modified_deleted_and_ignored(real_ws):
    ws, repo = real_ws
    (repo / "app" / "a.py").write_text("print('a2')\n", newline="\n")
    (repo / "app" / "b.py").write_text("print('b')\n", newline="\n")
    (repo / "app" / "c.py").unlink()
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert await ws.changed_files() == [
        FileChange("app/a.py", "print('a2')\n"),
        FileChange("app/b.py", "print('b')\n"),
        FileChange("app/c.py", None),
    ]
    diff, digest = await ws.diff_and_hash()
    assert "+print('a2')" in diff and len(digest) == 64


async def test_diff_survives_developer_commit(real_ws):
    ws, repo = real_ws
    (repo / "app" / "a.py").write_text("print('a2')\n", newline="\n")
    git(repo, "commit", "-qam", "dev commit")
    diff, _ = await ws.diff_and_hash()
    assert "+print('a2')" in diff


async def test_files_matching_approval_returns_exactly_the_approved_files(real_ws):
    ws, repo = real_ws
    (repo / "app" / "a.py").write_text("print('café')\n", encoding="utf-8", newline="\n")
    (repo / "app" / "empty.py").write_bytes(b"")
    (repo / "app" / "c.py").unlink()
    _, approved = await ws.diff_and_hash()
    files = await ws.files_matching_approval(approved)
    assert [f.path for f in files] == ["app/a.py", "app/c.py", "app/empty.py"]


async def test_files_matching_approval_refuses_changes_after_approval(real_ws):
    ws, repo = real_ws
    (repo / "app" / "a.py").write_text("print('a2')\n", newline="\n")
    _, approved = await ws.diff_and_hash()
    (repo / "app" / "a.py").write_text("print('a3')\n", newline="\n")
    with pytest.raises(WorkspaceError, match="changed since"):
        await ws.files_matching_approval(approved)


async def test_v1_hole_clean_filter_is_refused(real_ws, tmp_path):
    ws, repo = real_ws
    attrs = tmp_path / "attrs"
    attrs.write_text("app/a.py filter=x\n", newline="\n")
    git(repo, "config", "core.attributesFile", attrs.as_posix())  # repo-local: agent-reachable
    git(repo, "config", "filter.x.clean", "git hash-object --stdin")
    (repo / "app" / "a.py").write_text("import os; os.system('evil')\n", newline="\n")
    diff, approved = await ws.diff_and_hash()
    assert "evil" not in diff  # what the human saw
    with pytest.raises(WorkspaceError, match=r"app/a\.py: content \(blob"):
        await ws.files_matching_approval(approved)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
async def test_v1_hole_index_flags_are_refused(real_ws, flag):
    ws, repo = real_ws
    (repo / "app" / "a.py").write_text("print('approved')\n", newline="\n")
    git(repo, "add", "app/a.py")
    git(repo, "update-index", flag, "app/a.py")
    (repo / "app" / "a.py").write_text("print('secret')\n", newline="\n")
    diff, approved = await ws.diff_and_hash()
    assert "approved" in diff and "secret" not in diff
    with pytest.raises(WorkspaceError, match=r"app/a\.py: content"):
        await ws.files_matching_approval(approved)


async def test_unsafe_modes_and_info_attributes_are_refused(real_ws):
    ws, repo = real_ws
    git(repo, "update-index", "--chmod=+x", "app/a.py")
    git(repo, "commit", "-q", "-m", "chmod")
    for call in (ws.diff_and_hash, ws.changed_files):
        with pytest.raises(WorkspaceError, match=r"app/a\.py \(mode change 100644 -> 100755\)"):
            await call()
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* -diff\n", newline="\n")
    with pytest.raises(WorkspaceError, match="info/attributes"):
        await ws.diff_and_hash()


async def test_embedded_repo_is_refused(real_ws):
    ws, repo = real_ws
    nested = repo / "vendor" / "lib"
    nested.mkdir(parents=True)
    git(nested, "init", "-q")
    (nested / "x.py").write_text("x\n", newline="\n")
    git(nested, "add", ".")
    git(nested, "commit", "-q", "-m", "x")
    with pytest.raises(WorkspaceError, match=r"vendor/lib \(gitlink"):
        await ws.changed_files()


async def test_minus_diff_attribute_cannot_hide_text_changes(real_ws):
    ws, repo = real_ws
    (repo / ".gitattributes").write_text("* -diff\n", newline="\n")
    (repo / "app" / "a.py").write_text("print('a2')\n", newline="\n")
    diff, approved = await ws.diff_and_hash()
    assert "+print('a2')" in diff and "Binary files" not in diff
    assert [f.path for f in await ws.files_matching_approval(approved)] == [
        ".gitattributes",
        "app/a.py",
    ]


async def test_local_git_sees_no_user_global_or_system_config(real_ws):
    ws, _ = real_ws
    out = await ws._exec("git config --global --list")
    assert out.strip() in ("", "<no output>")
    origins = await ws._git("config --show-origin --list")
    assert str(Path.home() / ".gitconfig") not in origins and "credential.helper" not in origins


async def test_read_base_file_reads_the_base_commit_not_the_worktree(real_ws):
    ws, _ = real_ws
    (Path(ws.shell_repo_dir) / "app" / "a.py").write_text("changed\n", newline="\n")
    assert await ws.read_base_file("app/a.py") == "print('a')\n"
    assert await ws.read_base_file("app/missing.py") is None
    for bad in ("../x", "/etc/passwd", "C:/x", "a//b", ""):
        with pytest.raises(ValueError):
            await ws.read_base_file(bad)


def test_shell_helpers():
    assert parse_exit_code("x\n[Command failed with exit code 3]") == 3
    assert parse_exit_code("Exit code: 0") == 0 and parse_exit_code("nothing") == -1
    assert q("a b") == '"a b"'
    for bad in ('a"b', "a%b", "a$b", "a\nb"):
        with pytest.raises(WorkspaceError, match="Unsafe"):
            q(bad)


async def test_fake_sandbox_failure_and_corrupt_workspace():
    sb = FakeSandbox(structured_exit=False).on("git clone", exit_code=128, output="denied")
    ws = Workspace(sb, "acme/taskboard-api", "https://github.com/acme/taskboard-api.git")
    with pytest.raises(WorkspaceError, match="exit 128"):
        await ws.ensure_cloned()
    corrupt = Workspace(FakeSandbox({"/workspace/repo/.git/HEAD": b"ref"}), "a/b", "u")
    with pytest.raises(WorkspaceError, match="corrupt"):
        await corrupt.ensure_cloned()
    assert os.name in ("nt", "posix")


async def test_sandbox_cannot_push_to_the_clone_origin(real_ws, tmp_path):
    """Publishing goes through the gateway only: a stray `git push` in the sandbox fails."""
    ws, repo = real_ws
    origin = tmp_path / "origin"
    before = git(origin, "for-each-ref")
    (repo / "app" / "a.py").write_text("print('pushed?')\n", newline="\n")
    git(repo, "commit", "-qam", "agent commit")
    for target in ("HEAD:refs/heads/main", "HEAD:refs/heads/sneaky"):
        with pytest.raises(WorkspaceError, match="exit"):
            await ws._git(f"push origin {target}")
    assert git(origin, "for-each-ref") == before
