"""LocalGitHub against a real bare repo (offline)."""

import subprocess
from pathlib import Path

import pytest

from shipwright.integrations.github.gateway import FileChange
from shipwright.integrations.github.local_github import LocalGitHub, ensure_bare_remote
from shipwright.sandbox.git_diff import git_blob_sha

REPO = "local/taskboard-api"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def gh(tmp_path) -> LocalGitHub:
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    git(source, "init", "-q", "-b", "main")
    (source / "app" / "a.py").write_bytes(b"a = 1\n")
    (source / "app" / "old.py").write_bytes(b"old\n")
    git(source, "add", ".")
    git(source, "commit", "-q", "-m", "init")
    remote = ensure_bare_remote(source, tmp_path / "demo" / ".local_remote.git")
    assert ensure_bare_remote(source, remote) == remote  # idempotent
    return LocalGitHub(remote, tmp_path / "demo" / ".local_prs")


async def test_branches_and_shas(gh):
    assert await gh.list_branch_names(REPO) == {"main"}
    main = await gh.get_branch_sha(REPO, "main")
    assert main == git(gh.remote, "rev-parse", "main")
    await gh.create_branch(REPO, "shipwright/ship-1")
    assert await gh.list_branch_names(REPO) == {"main", "shipwright/ship-1"}
    assert await gh.get_branch_sha(REPO, "shipwright/ship-1") == main
    with pytest.raises(RuntimeError, match="already exists"):
        await gh.create_branch(REPO, "shipwright/ship-1")
    with pytest.raises(RuntimeError, match="not found"):
        await gh.get_branch_sha(REPO, "nope")


async def test_push_files_commits_exact_blobs(gh):
    await gh.create_branch(REPO, "shipwright/ship-1")
    files = [
        FileChange("app/a.py", "a = 2  # café\r\n"),
        FileChange("app/new.py", ""),
        FileChange("app/old.py", None),
    ]
    sha = await gh.push_files(REPO, "shipwright/ship-1", files, "fix: SHIP-1")
    assert await gh.get_branch_sha(REPO, "shipwright/ship-1") == sha
    assert git(gh.remote, "log", "-1", "--format=%s|%an", sha) == "fix: SHIP-1|Shipwright Bot"
    tree = dict(
        reversed(line.split(" ")[2].split("\t"))
        for line in git(gh.remote, "ls-tree", "-r", sha).splitlines()
    )
    assert tree == {
        "app/a.py": git_blob_sha("a = 2  # café\r\n".encode()),  # no eol/filter conversion
        "app/new.py": git_blob_sha(b""),
    }
    assert git(gh.remote, "rev-parse", f"{sha}^") == await gh.get_branch_sha(REPO, "main")


@pytest.mark.parametrize("path", ["../x", "/abs", ".git/config", "a//b", "a\\b", "C:/x", ""])
async def test_push_files_refuses_unsafe_paths(gh, path):
    await gh.create_branch(REPO, "b1")
    before = await gh.get_branch_sha(REPO, "b1")
    with pytest.raises(ValueError, match="Invalid repo file path"):
        await gh.push_files(REPO, "b1", [FileChange(path, "x")], "m")
    assert await gh.get_branch_sha(REPO, "b1") == before


async def test_pull_request_is_a_markdown_file(gh):
    await gh.create_branch(REPO, "shipwright/ship-1")
    await gh.push_files(REPO, "shipwright/ship-1", [FileChange("app/a.py", "a = 2\n")], "m")
    assert await gh.find_open_pull_request(REPO, "shipwright/ship-1") is None
    url = await gh.create_pull_request(REPO, "shipwright/ship-1", "main", "Fix SHIP-1", "Body")
    path = gh.prs_dir / "shipwright" / "ship-1.md"
    assert url == path.resolve().as_uri()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Fix SHIP-1") and "- `M\tapp/a.py`" in text and "Body" in text
    assert await gh.find_open_pull_request(REPO, "shipwright/ship-1") == url
    with pytest.raises(RuntimeError, match="already exists"):
        await gh.create_pull_request(REPO, "shipwright/ship-1", "main", "again", "")


async def test_other_repo_and_bad_branch_names_are_refused(gh):
    with pytest.raises(ValueError, match="serves"):
        await gh.list_branch_names("acme/other")
    with pytest.raises(RuntimeError, match="check-ref-format"):
        await gh.create_branch(REPO, "bad..name")
