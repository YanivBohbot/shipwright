"""Workspace sandbox tests: canned FakeSandbox + real git through LocalShellBackend (offline)."""

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest
from deepagents.backends import LangSmithSandbox, LocalShellBackend

from shipwright.integrations.github import FileChange
from shipwright.tools.workspace_tools import make_diff_tool
from shipwright.workspace import (
    BASE_SHA_FILE,
    REPO_DIR,
    Workspace,
    WorkspaceError,
    clean_shell_env,
    open_workspace,
    parse_exit_code,
)
from tests.fakes import FakeSandbox, FakeSandboxClient, FakeSandboxInfo, settings_stub

SHA = "a" * 40
STATE = "/workspace/.shipwright"


def _fake_ws(sandbox: FakeSandbox, skills: Path | None = None) -> Workspace:
    return Workspace(sandbox, "acme/taskboard-api", skills_source=skills or Path("does-not-exist"))


def _cloned_sandbox(**kwargs) -> FakeSandbox:
    return FakeSandbox(
        {f"{REPO_DIR}/.git/HEAD": b"ref: refs/heads/main\n", BASE_SHA_FILE: SHA.encode()},
        **kwargs,
    )


# ---------------------------------------------------------------- parse_exit_code


def test_parse_exit_code():
    assert parse_exit_code("4\n[Command succeeded with exit code 0]") == 0
    assert parse_exit_code("boom\n[Command failed with exit code 2]") == 2


def test_parse_exit_code_local_shell_format_and_unknown():
    assert parse_exit_code("[stderr] fatal: nope\n\nExit code: 128") == 128
    assert parse_exit_code("no marker at all") == -1  # unknown -> treated as failure


# ---------------------------------------------------------------- ensure_cloned


async def test_ensure_cloned_clones_once_and_uploads_skills(tmp_path):
    skills = tmp_path / "skills"
    (skills / "pr-template").mkdir(parents=True)
    (skills / "pr-template" / "SKILL.md").write_text("---\nname: pr-template\n---\n", newline="\n")

    def _clone(sb: FakeSandbox) -> None:
        sb.files[f"{REPO_DIR}/.git/HEAD"] = b"ref: refs/heads/main\n"

    sb = FakeSandbox().on("git clone", effect=_clone).on("rev-parse HEAD", output=SHA + "\n")
    ws = _fake_ws(sb, skills)

    assert await ws.ensure_cloned() is True
    clone = next(c for c in sb.executed if "git clone" in c)
    assert clone.startswith("git clone --depth 50 ")
    assert '"https://github.com/acme/taskboard-api.git" "/workspace/repo"' in clone
    assert "core.autocrlf=false" in clone
    assert sb.count('config user.name "Shipwright Bot"') == 1
    assert sb.count('config user.email "shipwright-bot@users.noreply.github.com"') == 1
    assert sb.files["/skills/pr-template/SKILL.md"].startswith(b"---")
    assert sb.files[BASE_SHA_FILE] == SHA.encode()
    assert await ws.base_sha() == SHA

    assert await ws.ensure_cloned() is False
    assert sb.count("git clone") == 1
    assert sb.count("rev-parse HEAD") == 1  # base sha is never re-recorded


async def test_ensure_cloned_raises_on_clone_failure():
    sb = FakeSandbox().on("git clone", output="fatal: repository not found", exit_code=128)
    with pytest.raises(WorkspaceError, match="repository not found"):
        await _fake_ws(sb).ensure_cloned()


async def test_ensure_cloned_reports_corrupt_workspace_without_base_sha():
    sb = FakeSandbox({f"{REPO_DIR}/.git/HEAD": b"ref: refs/heads/main\n"})
    with pytest.raises(WorkspaceError, match="corrupt"):
        await _fake_ws(sb).ensure_cloned()
    assert sb.count("git clone") == 0


async def test_base_sha_rejects_garbage():
    sb = FakeSandbox({BASE_SHA_FILE: b"not-a-sha"})
    with pytest.raises(WorkspaceError):
        await _fake_ws(sb).base_sha()


# ---------------------------------------------------------------- canned diff / files


async def test_changed_files_handles_added_modified_deleted():
    sb = _cloned_sandbox()
    sb.on("--name-status", redirect=b"M\x00app/a.py\x00D\x00app/c.py\x00")
    sb.on("ls-files --others", redirect=b"app/b.py\x00")
    sb.files[f"{REPO_DIR}/app/a.py"] = b"print('a2')\n"
    sb.files[f"{REPO_DIR}/app/b.py"] = b"print('b')\n"

    files = await _fake_ws(sb).changed_files()

    assert files == [
        FileChange(path="app/a.py", content="print('a2')\n"),
        FileChange(path="app/b.py", content="print('b')\n"),
        FileChange(path="app/c.py", content=None),
    ]
    name_status = next(c for c in sb.executed if "--name-status" in c)
    assert f"-z {SHA}" in name_status and "--no-renames" in name_status
    assert sb.count("--exclude-standard -z") == 1


async def test_diff_reads_redirected_file_and_hashes_it():
    patch = b"diff --git a/app/a.py b/app/a.py\n+x\n"
    sb = _cloned_sandbox(structured_exit=False)
    sb.on("diff --no-color", redirect=patch)
    ws = _fake_ws(sb)

    assert await ws.diff() == patch.decode()
    assert await ws.diff_hash() == hashlib.sha256(patch).hexdigest()
    assert sb.count("add -N .") == 2
    diff_cmd = next(c for c in sb.executed if "diff --no-color" in c)
    assert "--full-index" in diff_cmd
    targets = re.findall(rf'{SHA} > "{STATE}/(diff\.patch-[0-9a-f]{{32}})"$', diff_cmd)
    assert len(targets) == 1
    # capture files are unique per call and removed after reading
    assert len({d for d in sb.deleted if "diff.patch-" in d}) == 2
    assert set(sb.files) == {f"{REPO_DIR}/.git/HEAD", BASE_SHA_FILE}


async def test_diff_raises_when_git_fails():
    sb = _cloned_sandbox().on("diff --no-color", output="fatal: bad object", exit_code=128)
    with pytest.raises(WorkspaceError, match="bad object"):
        await _fake_ws(sb).diff()


# ---------------------------------------------------------------- run_tests


async def test_run_tests_reports_failure_tail():
    lines = [f"line {i}" for i in range(100)]
    output = "\n".join([*lines, "FAILED tests/test_x.py::test_y", "1 failed, 2 passed in 0.12s"])
    sb = _cloned_sandbox().on("pytest", output=output + "\n\nExit code: 1", exit_code=1)

    report = await _fake_ws(sb).run_tests()

    assert report.passed is False
    assert report.summary == "1 failed, 2 passed in 0.12s"
    tail = report.output_tail.splitlines()
    assert len(tail) == 80
    assert "1 failed, 2 passed in 0.12s" in tail
    assert "line 0" not in tail
    assert sb.executed[-1] == 'uv run --directory "/workspace/repo" pytest -q'


async def test_run_tests_passing_with_parsed_exit_code():
    sb = _cloned_sandbox(structured_exit=False).on("pytest", output="...\n3 passed in 0.50s")
    report = await _fake_ws(sb).run_tests()
    assert report.passed is True
    assert report.summary == "3 passed in 0.50s"


# ---------------------------------------------------------------- open_workspace


async def test_ensure_workspace_recreates_missing_sandbox():
    client = FakeSandboxClient([FakeSandboxInfo("shipwright-other")])
    ws = await open_workspace(
        settings_stub(), thread_id="t1", repo="acme/taskboard-api", client=client
    )
    assert client.created == [{"name": "shipwright-t1", "idle_ttl_seconds": 3600}]
    assert isinstance(ws.backend, LangSmithSandbox)
    assert ws.shell_repo_dir == REPO_DIR
    assert ws.repo == "acme/taskboard-api"


async def test_open_workspace_reuses_existing_sandbox():
    client = FakeSandboxClient([FakeSandboxInfo("shipwright-t1")])
    ws = await open_workspace(settings_stub(), thread_id="t1", repo="a/b", client=client)
    assert client.created == []
    assert ws.backend.id == "shipwright-t1"


async def test_open_workspace_replaces_failed_sandbox():
    client = FakeSandboxClient([FakeSandboxInfo("shipwright-t1", status="failed")])
    await open_workspace(settings_stub(), thread_id="t1", repo="a/b", client=client)
    assert client.deleted == ["shipwright-t1"]
    assert [c["name"] for c in client.created] == ["shipwright-t1"]


async def test_open_workspace_local_uses_clean_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GITHUB_PAT", "ghp-should-not-leak")
    ws = await open_workspace(
        settings_stub(shipwright_sandbox_provider="local"),
        thread_id="t1",
        repo="a/b",
        local_root=tmp_path,
    )
    assert isinstance(ws.backend, LocalShellBackend)
    assert ws.shell_repo_dir == str(tmp_path / "t1" / "workspace" / "repo")
    env = ws.backend._env
    assert set(env) <= {
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "TEMP",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GCM_INTERACTIVE",
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
    }
    values = " ".join(env.values())
    assert "sk-should-not-leak" not in values
    assert "ghp-should-not-leak" not in values
    fake_home = str(tmp_path / "t1" / ".home")
    assert env["HOME"] == env["USERPROFILE"] == fake_home
    assert env["HOME"] != str(Path.home())
    assert env["GIT_CONFIG_GLOBAL"] == str(Path(fake_home) / ".gitconfig")
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"


async def test_open_workspace_rejects_unsafe_thread_id(tmp_path):
    with pytest.raises(ValueError):
        await open_workspace(
            settings_stub(shipwright_sandbox_provider="local"),
            thread_id="../evil",
            repo="a/b",
            local_root=tmp_path,
        )


# ---------------------------------------------------------------- diff tool


async def test_diff_tool_reports_no_changes_and_truncates():
    sb = _cloned_sandbox()
    tool = make_diff_tool(_fake_ws(sb))
    assert tool.name == "show_workspace_diff"
    assert await tool.ainvoke({}) == "No changes."

    sb.handlers.insert(
        0,
        (
            "diff --no-color",
            {"output": "", "exit_code": 0, "redirect": b"x" * 25_000, "effect": None},
        ),
    )
    out = await tool.ainvoke({})
    assert out.startswith("x" * 20_000)
    assert out.endswith("[diff truncated]")
    assert len(out) < 20_100


# ---------------------------------------------------------------- real git (LocalShellBackend)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
async def real_ws(tmp_path):
    origin = tmp_path / "origin"
    (origin / "app").mkdir(parents=True)
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "core.autocrlf", "false")
    (origin / ".gitignore").write_text(".pytest_cache/\n__pycache__/\n.venv/\n", newline="\n")
    (origin / "app" / "a.py").write_text("print('a')\n", newline="\n")
    (origin / "app" / "c.py").write_text("print('c')\n", newline="\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "init")

    parent = tmp_path / "parent"  # the workspace lives inside an unrelated git repo
    parent.mkdir()
    _git(parent, "init", "-q")
    root = parent / "ws"
    root.mkdir()
    backend = LocalShellBackend(root_dir=root, env=clean_shell_env(root))
    ws = Workspace(
        backend,
        "acme/taskboard-api",
        shell_repo_dir=str(root / "workspace" / "repo"),
        posix_shell=os.name != "nt",
        mkdir_parents=False,
        clone_url=origin.as_uri(),
        skills_source=tmp_path / "no-skills",
    )
    assert await ws.ensure_cloned() is True
    return ws, root / "workspace" / "repo"


async def test_real_git_clone_records_base_sha_outside_repo(real_ws):
    ws, repo = real_ws
    assert await ws.base_sha() == _git(repo, "rev-parse", "HEAD").strip()
    assert (repo.parent / ".shipwright" / "base_sha").is_file()
    assert await ws.ensure_cloned() is False
    assert await ws.diff() == ""
    assert await ws.changed_files() == []


async def test_real_git_changed_files_added_modified_deleted(real_ws):
    ws, repo = real_ws
    (repo / "app" / "a.py").write_text("print('a2')\n", newline="\n")
    (repo / "app" / "c.py").unlink()
    (repo / "app" / "b.py").write_text("print('b')\n", newline="\n")
    expected = [
        FileChange(path="app/a.py", content="print('a2')\n"),
        FileChange(path="app/b.py", content="print('b')\n"),
        FileChange(path="app/c.py", content=None),
    ]
    assert await ws.changed_files() == expected  # before add -N (untracked)
    diff = await ws.diff()
    assert "+print('b')" in diff and "-print('c')" in diff and "+print('a2')" in diff
    assert await ws.changed_files() == expected  # after add -N (intent-to-add)


async def test_changed_files_never_includes_ignored_or_cache_paths(real_ws):
    ws, repo = real_ws
    (repo / ".pytest_cache" / "v").mkdir(parents=True)
    (repo / ".pytest_cache" / "v" / "x").write_text("cache", newline="\n")
    (repo / "app" / "__pycache__").mkdir()
    (repo / "app" / "__pycache__" / "x.pyc").write_bytes(b"\x00\x01")
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / ".venv" / "lib" / "x.py").write_text("x", newline="\n")
    (repo / "app" / "new dir").mkdir()
    (repo / "app" / "new dir" / "b.py").write_text("b\n", newline="\n")

    files = await ws.changed_files()
    assert files == [FileChange(path="app/new dir/b.py", content="b\n")]
    diff = await ws.diff()
    assert "__pycache__" not in diff and ".venv" not in diff and ".pytest_cache" not in diff


async def test_diff_survives_developer_commit(real_ws):
    ws, repo = real_ws
    (repo / "app" / "a.py").write_text("print('a')\nprint('more')\n", newline="\n")
    _git(repo, "commit", "-q", "-am", "developer commit")

    rebuilt = Workspace(
        ws.backend,
        ws.repo,
        shell_repo_dir=ws.shell_repo_dir,
        posix_shell=ws.posix_shell,
        mkdir_parents=False,
        clone_url="file:///never-used",
        skills_source=ws.skills_source,
    )
    assert await rebuilt.ensure_cloned() is False
    diff = await rebuilt.diff()
    assert "+print('more')" in diff
    assert await rebuilt.changed_files() == [
        FileChange(path="app/a.py", content="print('a')\nprint('more')\n")
    ]


async def test_changed_files_consistent_with_diff_after_untracking(real_ws):
    ws, repo = real_ws
    _git(repo, "rm", "-q", "--cached", "app/a.py")  # file kept on disk, only untracked
    assert await ws.changed_files() == []
    assert await ws.diff() == ""


@pytest.mark.parametrize("repo", ["evil repo", "a/b/c", 'a/b"', "../..", "a", "a/b;rm"])
async def test_invalid_repo_is_rejected(repo):
    with pytest.raises(WorkspaceError, match="Invalid repo"):
        Workspace(FakeSandbox(), repo)
    client = FakeSandboxClient()
    with pytest.raises(WorkspaceError, match="Invalid repo"):
        await open_workspace(settings_stub(), thread_id="t1", repo=repo, client=client)
    assert client.created == []


async def test_changed_files_rejects_non_utf8_paths():
    sb = _cloned_sandbox().on("--name-status", redirect=b"M\x00app/\xff.py\x00")
    with pytest.raises(WorkspaceError, match="not valid UTF-8"):
        await _fake_ws(sb).changed_files()


async def test_local_git_sees_no_user_global_or_system_config(real_ws):
    ws, repo = real_ws
    out = await ws._exec("git config --global --list")  # empty GIT_CONFIG_GLOBAL file
    assert out.strip() in ("", "<no output>")
    origins = await ws._git("config --show-origin --list")
    assert str(Path.home() / ".gitconfig") not in origins
    assert "credential.helper" not in origins
    assert sorted(p.name for p in (repo.parent / ".shipwright").iterdir()) == ["base_sha"]
