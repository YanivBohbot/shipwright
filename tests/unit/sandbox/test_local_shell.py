"""The local sandbox for real: LocalShellBackend + git on this machine (cmd.exe on Windows)."""

import shutil
import subprocess

import pytest

import shipwright.sandbox.workspace as ws
from tests.fakes import make_settings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def sh(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
async def local_box(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "LOCAL_ROOT", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    box = await ws.open_sandbox(make_settings(sandbox_provider="local"), "t-1")
    repo = tmp_path / "t-1" / "workspace" / "repo"
    repo.mkdir(parents=True)
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@t")
    sh(repo, "config", "user.name", "t")
    (repo / "app.py").write_bytes(b"x = 1\n")
    (repo / "gone.py").write_bytes(b"old\n")
    sh(repo, "add", ".")
    sh(repo, "commit", "-q", "-m", "base")
    sh(repo, "update-ref", "refs/remotes/origin/main", "HEAD")  # what a clone would have
    return box, repo


async def test_shell_path_is_the_host_path(local_box):
    box, repo = local_box
    assert ws.shell_repo_dir(box) == str(repo)


async def test_local_shell_gets_no_secret(local_box):
    box, _ = local_box
    out = await ws.run(box, "set" if ws.os.name == "nt" else "env")
    assert "sk-must-not-leak" not in out
    assert "PATH" in out.upper()


async def test_changed_files_and_diff_with_real_git(local_box):
    box, repo = local_box
    (repo / "app.py").write_bytes(b"x = 2\n")
    (repo / "new.py").write_bytes(b"y = 3\n")
    (repo / "gone.py").unlink()
    sh(repo, "commit", "-q", "-am", "stray local commit")  # must not hide the change
    result = await ws.changed_files(box)
    assert result["files"] == [
        {"path": "app.py", "content": "x = 2\n"},
        {"path": "new.py", "content": "y = 3\n"},
    ]
    assert result["deleted"] == ["gone.py"]
    diff = await ws.workspace_diff(box)
    assert "+x = 2" in diff and "+y = 3" in diff


async def test_clone_is_skipped_when_the_repo_is_there(local_box):
    box, _ = local_box
    await ws.clone_repo(box, "acme/never-fetched")  # would fail offline if it tried
