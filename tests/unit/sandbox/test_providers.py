import pytest
from deepagents.backends import LangSmithSandbox, LocalShellBackend

from shipwright.sandbox import providers
from shipwright.sandbox.providers import clean_shell_env, open_workspace
from shipwright.sandbox.workspace import WorkspaceError
from tests.fakes import FakeSandboxClient, FakeSandboxInfo, make_settings


async def test_local_mode_uses_local_shell_and_the_local_remote(tmp_path):
    settings = make_settings(demo_dir=tmp_path / "demo", tmp_dir=tmp_path / "tmp")
    ws = await open_workspace(settings, thread_id="t-1")
    assert isinstance(ws.backend, LocalShellBackend)
    assert ws.repo == "local/taskboard-api"
    assert ws.clone_url == (tmp_path / "demo" / ".local_remote.git").resolve().as_uri()
    assert ws.shell_repo_dir == str(
        (tmp_path / "tmp" / "shipwright" / "t-1").resolve() / "workspace" / "repo"
    )


def test_clean_shell_env_hides_secrets_and_user_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GITHUB_PAT", "ghp_should_not_leak")
    env = clean_shell_env(tmp_path)
    assert "sk-should-not-leak" not in env.values() and "ghp_should_not_leak" not in env.values()
    home = (tmp_path / ".home").resolve()
    assert env["HOME"] == env["USERPROFILE"] == str(home)
    assert env["GIT_CONFIG_GLOBAL"] == str(home / ".gitconfig")
    assert (home / ".gitconfig").read_bytes() == b""
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0"


async def test_live_mode_creates_then_reuses_the_thread_sandbox():
    client = FakeSandboxClient()
    settings = make_settings(mode="live", sandbox_idle_ttl_seconds=60)
    ws = await open_workspace(settings, thread_id="t1", client=client)
    assert isinstance(ws.backend, LangSmithSandbox)
    assert ws.clone_url == "https://github.com/acme/taskboard-api.git"
    assert client.created == [{"name": "shipwright-t1", "idle_ttl_seconds": 60}]
    await open_workspace(settings, thread_id="t1", client=client)
    assert len(client.created) == 1


async def test_live_mode_replaces_a_failed_sandbox():
    client = FakeSandboxClient([FakeSandboxInfo("shipwright-t1", status="failed")])
    await open_workspace(make_settings(mode="live"), thread_id="t1", client=client)
    assert client.deleted == ["shipwright-t1"] and len(client.created) == 1


@pytest.mark.parametrize("thread_id", ["../x", "a b", "", "x" * 70, "-x"])
async def test_unsafe_thread_id_is_rejected(thread_id):
    with pytest.raises(ValueError, match="Unsafe thread_id"):
        await open_workspace(
            make_settings(mode="live"), thread_id=thread_id, client=FakeSandboxClient()
        )


@pytest.mark.parametrize("repo", ["evil repo", "a/b/c", 'a/b"', "../..", "a", "a/b;rm"])
async def test_invalid_repo_is_rejected_before_any_sandbox(repo):
    client = FakeSandboxClient()
    with pytest.raises(WorkspaceError, match="Invalid repo"):
        await open_workspace(make_settings(mode="live"), thread_id="t1", repo=repo, client=client)
    assert client.created == []


def test_one_sandbox_client_per_process_and_key(monkeypatch):
    import langsmith.sandbox

    made: list[str] = []

    class Client:
        def __init__(self, api_key: str) -> None:
            made.append(api_key)

    monkeypatch.setattr(langsmith.sandbox, "SandboxClient", Client)
    providers.shared_sandbox_client.cache_clear()
    a = providers.shared_sandbox_client("k1")
    assert providers.shared_sandbox_client("k1") is a and made == ["k1"]
    providers.shared_sandbox_client.cache_clear()


async def test_local_mode_end_to_end_publish_matches_the_approved_patch(tmp_path):
    """Bare remote -> sandbox clone -> approval guard -> LocalGitHub commit, all real git."""
    import subprocess

    from shipwright.integrations.github.local_github import LocalGitHub, ensure_bare_remote
    from shipwright.sandbox.git_diff import parse_blob_ids

    def git(cwd, *args):
        return subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@x", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    settings = make_settings(demo_dir=tmp_path / "demo", tmp_dir=tmp_path / "tmp")
    source = settings.local_source_repo
    (source / "app").mkdir(parents=True)
    git(source, "init", "-q", "-b", "main")
    (source / "app" / "a.py").write_bytes(b"a = 1\n")
    (source / "app" / "gone.py").write_bytes(b"x\n")
    git(source, "add", ".")
    git(source, "commit", "-q", "-m", "init")
    ensure_bare_remote(source, settings.local_remote)
    gh = LocalGitHub(settings.local_remote, settings.local_prs_dir, repo=settings.repo)

    ws = await open_workspace(settings, thread_id="e2e")
    await ws.ensure_cloned()
    assert await ws.base_sha() == await gh.get_branch_sha(settings.repo, "main")
    repo = tmp_path / "tmp" / "shipwright" / "e2e" / "workspace" / "repo"
    (repo / "app" / "a.py").write_bytes(b"a = 2\n")
    (repo / "app" / "new.py").write_bytes(b"n = 1\n")
    (repo / "app" / "gone.py").unlink()

    patch, approved = await ws.patch_and_hash()
    files = await ws.files_matching_approval(approved)
    await gh.create_branch(settings.repo, "shipwright/ship-1")
    sha = await gh.push_files(settings.repo, "shipwright/ship-1", files, "fix")
    pushed = {
        line.split("\t")[1]: line.split()[2]
        for line in git(settings.local_remote, "ls-tree", "-r", sha).splitlines()
    }
    expected = {p: new for p, (_, new) in parse_blob_ids(patch).items() if new != "0" * 40}
    assert pushed == expected
