"""The thread's workspace: a LangSmith sandbox holding a clone of the target repository.

Deep Agents concept: a **sandbox backend**. `LangSmithSandbox` gives the agent the file tools
plus `execute` (shell) inside an isolated machine. One sandbox per thread
(`shipwright-<thread_id>`), reused while it lives, so a paused run finds its files again.

The clone has no push credentials: publishing goes through the GitHub MCP tools only.
"""

import asyncio
import re

from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import SandboxBackendProtocol

from shipwright.core.config import Settings

REPO_DIR = "/workspace/repo"
CLONE_TIMEOUT = 600
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


class WorkspaceError(RuntimeError):
    """A command in the sandbox failed."""


async def open_sandbox(settings: Settings, thread_id: str) -> LangSmithSandbox:
    """Reuse the thread's sandbox unless it failed; otherwise create it."""
    from langsmith.sandbox import SandboxClient

    if not _THREAD_ID_RE.match(thread_id):
        raise ValueError(f"Unsafe thread id for a sandbox name: {thread_id!r}")
    client = SandboxClient(api_key=settings.langsmith_api_key.get_secret_value())
    name = f"shipwright-{thread_id}"
    existing = [s for s in await asyncio.to_thread(client.list_sandboxes) if s.name == name]
    sandbox = next((s for s in existing if getattr(s, "status", "ready") != "failed"), None)
    if sandbox is None:
        if existing:
            await asyncio.to_thread(client.delete_sandbox, name)
        sandbox = await asyncio.to_thread(
            client.create_sandbox, name=name, idle_ttl_seconds=settings.sandbox_idle_ttl_seconds
        )
    return LangSmithSandbox(sandbox=sandbox)


async def run(backend: SandboxBackendProtocol, command: str, timeout: int | None = None) -> str:
    """Run a shell command in the sandbox; raise WorkspaceError on a non-zero exit code."""
    resp = await backend.aexecute(command, **({"timeout": timeout} if timeout else {}))
    if resp.exit_code not in (0, None):
        tail = "\n".join(resp.output.splitlines()[-20:])
        raise WorkspaceError(f"Command failed (exit {resp.exit_code}): {command}\n{tail}")
    return resp.output


def git(args: str) -> str:
    return f'git -C "{REPO_DIR}" {args}'


async def clone_repo(backend: SandboxBackendProtocol, repo: str) -> None:
    """Clone `https://github.com/<repo>` into REPO_DIR once (no-op if already cloned)."""
    if (await backend.aexecute(f'test -d "{REPO_DIR}/.git"')).exit_code == 0:
        return
    await run(
        backend,
        f'git clone --depth 50 "https://github.com/{repo}.git" "{REPO_DIR}"',
        timeout=CLONE_TIMEOUT,
    )
    await run(backend, git('remote set-url --push origin "no-push://use-the-github-mcp-tools"'))


async def workspace_diff(backend: SandboxBackendProtocol) -> str:
    """Unified diff of the working tree against HEAD, new files included."""
    await run(backend, git("add -N ."))  # make new files visible to `git diff`
    return await run(backend, git("--no-pager diff --no-color HEAD"))


async def changed_files(backend: SandboxBackendProtocol) -> dict[str, list]:
    """`{"files": [{"path", "content"}], "deleted": [...]}`: what `push_files` must receive."""
    await run(backend, git("add -N ."))
    status = await run(backend, git("--no-pager diff --name-status --no-renames HEAD"))
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
