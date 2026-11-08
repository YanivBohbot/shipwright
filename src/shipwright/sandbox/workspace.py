"""The thread's workspace: a sandbox backend holding a clone of the target repository.

Deep Agents concept: a **sandbox backend** gives the agent the file tools plus `execute`
(shell). One per thread, reused while it lives, so a paused run finds its files again.
`SANDBOX_PROVIDER` picks it:
- `local` (default): `LocalShellBackend` in `tmp/sandboxes/<thread_id>/` on this machine.
  NOT isolated: commands run on your computer (cmd.exe on Windows), with a minimal env.
- `langsmith`: an isolated LangSmith sandbox `shipwright-<thread_id>` (the feature must be
  enabled on the LangSmith organization).

The file tools see the repo at `REPO_DIR` in both cases; shell commands use
`shell_repo_dir(backend)` (the host path for the local shell). The clone has no push
credentials: publishing goes through the GitHub MCP tools only.
"""

import asyncio
import os
import re
from pathlib import Path

from deepagents.backends import LangSmithSandbox, LocalShellBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from shipwright.core.config import PROJECT_ROOT, Settings

REPO_DIR = "/workspace/repo"
LOCAL_ROOT = PROJECT_ROOT / "tmp" / "sandboxes"
CLONE_TIMEOUT = 600
# Only what git and uv need: no API key or token of this process reaches the agent's shell.
LOCAL_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
)
# Compare the working tree with the cloned `main`, not HEAD: a stray local `git commit` by the
# developer must not hide its changes from the review and the push.
BASE = "origin/main"
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


class WorkspaceError(RuntimeError):
    """A command in the sandbox failed."""


async def open_sandbox(settings: Settings, thread_id: str) -> SandboxBackendProtocol:
    if not _THREAD_ID_RE.match(thread_id):
        raise ValueError(f"Unsafe thread id for a sandbox name: {thread_id!r}")
    if settings.sandbox_provider == "local":
        root = LOCAL_ROOT / thread_id
        root.mkdir(parents=True, exist_ok=True)
        env = {k: os.environ[k] for k in LOCAL_ENV_KEYS if k in os.environ}
        env["GIT_TERMINAL_PROMPT"] = "0"
        return LocalShellBackend(root_dir=root, virtual_mode=True, env=env, timeout=CLONE_TIMEOUT)
    return await _langsmith_sandbox(settings, f"shipwright-{thread_id}")


async def _langsmith_sandbox(settings: Settings, name: str) -> LangSmithSandbox:
    """Reuse the sandbox called `name` unless it failed; otherwise create it."""
    from langsmith.sandbox import SandboxClient

    if settings.langsmith_api_key is None:  # Settings already refuses this; for the type checker
        raise ValueError("SANDBOX_PROVIDER=langsmith requires LANGSMITH_API_KEY")
    client = SandboxClient(api_key=settings.langsmith_api_key.get_secret_value())
    existing = [s for s in await asyncio.to_thread(client.list_sandboxes) if s.name == name]
    sandbox = next((s for s in existing if getattr(s, "status", "ready") != "failed"), None)
    if sandbox is None:
        if existing:
            await asyncio.to_thread(client.delete_sandbox, name)
        sandbox = await asyncio.to_thread(
            client.create_sandbox, name=name, idle_ttl_seconds=settings.sandbox_idle_ttl_seconds
        )
    return LangSmithSandbox(sandbox=sandbox)


def shell_repo_dir(backend: SandboxBackendProtocol) -> str:
    """The repo path for `execute`: the host path for the local shell, else `REPO_DIR`."""
    if isinstance(backend, LocalShellBackend):
        return str(Path(backend.cwd) / "workspace" / "repo")
    return REPO_DIR


async def run(backend: SandboxBackendProtocol, command: str, timeout: int | None = None) -> str:
    """Run a command; raise WorkspaceError on a non-zero exit. `[stderr]` lines are dropped."""
    resp = await backend.aexecute(command, **({"timeout": timeout} if timeout else {}))
    if resp.exit_code not in (0, None):
        tail = "\n".join(resp.output.splitlines()[-20:])
        raise WorkspaceError(f"Command failed (exit {resp.exit_code}): {command}\n{tail}")
    lines = [line for line in resp.output.splitlines() if not line.startswith("[stderr]")]
    return "" if resp.output.strip() == "<no output>" else "\n".join(lines)


def git(backend: SandboxBackendProtocol, args: str) -> str:
    return f'git -C "{shell_repo_dir(backend)}" {args}'


async def clone_repo(backend: SandboxBackendProtocol, repo: str) -> None:
    """Clone `https://github.com/<repo>` into the workspace once (no-op if already cloned)."""
    (head,) = await backend.adownload_files([f"{REPO_DIR}/.git/HEAD"])
    if head.error is None:
        return
    await run(
        backend,
        "git clone --depth 50 --config core.autocrlf=false "
        f'"https://github.com/{repo}.git" "{shell_repo_dir(backend)}"',
        timeout=CLONE_TIMEOUT,
    )
    await run(backend, git(backend, 'remote set-url --push origin "no-push://use-github-mcp"'))


async def workspace_diff(backend: SandboxBackendProtocol) -> str:
    """Unified diff of the working tree against `origin/main`, new files included."""
    await run(backend, git(backend, "add -N ."))  # make new files visible to `git diff`
    return await run(backend, git(backend, f"--no-pager diff --no-color {BASE}"))


async def changed_files(backend: SandboxBackendProtocol) -> dict[str, list]:
    """`{"files": [{"path", "content"}], "deleted": [...]}`: what `push_files` must receive."""
    await run(backend, git(backend, "add -N ."))
    status = await run(backend, git(backend, f"--no-pager diff --name-status --no-renames {BASE}"))
    entries = [line.split("\t", 1) for line in status.splitlines() if "\t" in line]
    deleted = [path for code, path in entries if code.startswith("D")]
    present = [path for code, path in entries if not code.startswith("D")]
    downloads = await backend.adownload_files([f"{REPO_DIR}/{p}" for p in present])
    files = []
    for path, resp in zip(present, downloads, strict=True):
        if resp.error or resp.content is None:
            raise WorkspaceError(f"Cannot read changed file {path}: {resp.error}")
        files.append({"path": path, "content": resp.content.decode("utf-8")})
    return {"files": files, "deleted": deleted}
