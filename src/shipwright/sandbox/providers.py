"""Where a Workspace runs: `LocalShellBackend` under `tmp/` (local mode) or a LangSmith sandbox.

The local shell gets a scrubbed environment (no Settings secret, fake HOME, empty global git
config, no system config, no prompts). It is not a security boundary: dev/demo/tests only.
"""

import asyncio
import functools
import os
import re
from pathlib import Path
from typing import Any

from deepagents.backends import LangSmithSandbox, LocalShellBackend

from shipwright.core.config import Settings
from shipwright.sandbox.workspace import CLONE_TIMEOUT, Workspace, validate_repo

CLEAN_ENV_KEYS = ("PATH", "SYSTEMROOT", "TEMP")  # HOME/USERPROFILE are redirected
SANDBOX_HOME = ".home"  # per-thread fake home inside the local workspace root
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


def _uv_env() -> dict[str, str]:
    """Real uv cache / managed-Python dirs (no credentials) so `uv run` works with a fake HOME."""
    env = os.environ
    if os.name == "nt":
        local = Path(env.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        roaming = Path(env.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        cache, python = local / "uv" / "cache", roaming / "uv" / "python"
    else:
        cache = Path(env.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "uv"
        python = Path(env.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "uv" / "python"
    return {
        "UV_CACHE_DIR": env.get("UV_CACHE_DIR", str(cache)),
        "UV_PYTHON_INSTALL_DIR": env.get("UV_PYTHON_INSTALL_DIR", str(python)),
    }


def clean_shell_env(workspace_root: Path) -> dict[str, str]:
    """Minimal environment for local shell commands (no secrets, no user git credentials)."""
    home = (Path(workspace_root) / SANDBOX_HOME).resolve()
    home.mkdir(parents=True, exist_ok=True)
    gitconfig = home / ".gitconfig"
    if not gitconfig.exists():
        gitconfig.write_bytes(b"")
    return {
        **{k: os.environ[k] for k in CLEAN_ENV_KEYS if k in os.environ},
        "HOME": str(home),
        "USERPROFILE": str(home),
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        **_uv_env(),
    }


@functools.cache
def shared_sandbox_client(api_key: str) -> Any:
    """One LangSmith `SandboxClient` per process and API key (reused across runs/threads)."""
    from langsmith.sandbox import SandboxClient

    return SandboxClient(api_key=api_key)


async def _langsmith_sandbox(settings: Settings, name: str, client: Any) -> Any:
    """Reuse the sandbox called `name` unless it failed; otherwise (re)create it."""
    existing = [s for s in await asyncio.to_thread(client.list_sandboxes) if s.name == name]
    sandbox = next((s for s in existing if getattr(s, "status", "ready") != "failed"), None)
    if sandbox is None:
        if existing:
            await asyncio.to_thread(client.delete_sandbox, name)
        sandbox = await asyncio.to_thread(
            client.create_sandbox, name=name, idle_ttl_seconds=settings.sandbox_idle_ttl_seconds
        )
    return sandbox


async def open_workspace(
    settings: Settings,
    *,
    thread_id: str,
    repo: str | None = None,
    clone_url: str | None = None,
    skills_source: Path | None = None,
    local_root: Path | None = None,
    client: Any = None,
) -> Workspace:
    """Build the Workspace of a thread (cloned lazily by `ensure_cloned`).

    Local mode: `LocalShellBackend` rooted at `{local_root or tmp_dir/shipwright}/{thread_id}`,
    cloning `clone_url` (default: the local bare remote). Live mode: the LangSmith sandbox
    `shipwright-{thread_id}`, cloning `https://github.com/{repo}.git`.
    """
    if not _THREAD_ID_RE.match(thread_id):
        raise ValueError(f"Unsafe thread_id for a workspace name: {thread_id!r}")
    repo = repo or settings.repo
    validate_repo(repo)  # before any sandbox is created

    if settings.mode == "local":
        root = (Path(local_root or settings.tmp_dir / "shipwright") / thread_id).resolve()
        root.mkdir(parents=True, exist_ok=True)
        backend = LocalShellBackend(
            root_dir=root, virtual_mode=True, env=clean_shell_env(root), timeout=CLONE_TIMEOUT
        )
        return Workspace(
            backend,
            repo,
            clone_url or settings.local_remote.resolve().as_uri(),
            shell_repo_dir=str(root / "workspace" / "repo"),
            posix_shell=os.name != "nt",
            skills_source=skills_source,
        )

    if client is None:
        if settings.langsmith_api_key is None:
            raise ValueError("LANGSMITH_API_KEY is required for the LangSmith sandbox")
        client = shared_sandbox_client(settings.langsmith_api_key.get_secret_value())
    sandbox = await _langsmith_sandbox(settings, f"shipwright-{thread_id}", client)
    return Workspace(
        LangSmithSandbox(sandbox=sandbox),
        repo,
        clone_url or f"https://github.com/{repo}.git",
        skills_source=skills_source,
    )
