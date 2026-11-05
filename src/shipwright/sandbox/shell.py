"""Portable command plumbing over a sandbox backend (`sh` in LangSmith, `cmd.exe` locally).

Virtual paths (`REPO_DIR`, `STATE_DIR`) are what file up/downloads see; shell paths
(`shell_repo_dir`) are what `execute` sees (host paths for the local provider). Commands use
double quotes only, no `cd`, `git -C` everywhere, and output that matters is redirected to a
file in `STATE_DIR` (outside the repo) and read back as exact bytes, so stderr or truncation
can never leak into a diff.
"""

import contextlib
import ntpath
import posixpath
import re
import uuid

from deepagents.backends.protocol import SandboxBackendProtocol

REPO_DIR = "/workspace/repo"
STATE_DIR = "/workspace/.shipwright"  # = {REPO_DIR}/../.shipwright, outside the repo

_EXIT_RE = re.compile(r"\[Command [^\]]*exit code (-?\d+)\]|^Exit code: (-?\d+)$", re.MULTILINE)


class WorkspaceError(RuntimeError):
    """A workspace shell or file operation failed, or the change set is not publishable."""


def parse_exit_code(output: str) -> int:
    """Exit code from the last `[Command ... exit code N]` marker; -1 (failure) if none."""
    matches = list(_EXIT_RE.finditer(output))
    return int(matches[-1].group(1) or matches[-1].group(2)) if matches else -1


def q(arg: str) -> str:
    """Double-quote an argument; valid in both `sh` and `cmd.exe` for our controlled values."""
    if any(c in arg for c in '"\n\r%$`'):
        raise WorkspaceError(f"Unsafe shell argument: {arg!r}")
    return f'"{arg}"'


class SandboxShell:
    """Runs commands and moves files in a backend whose repo lives at `shell_repo_dir`."""

    def __init__(
        self, backend: SandboxBackendProtocol, shell_repo_dir: str, posix_shell: bool
    ) -> None:
        self.backend = backend
        self.shell_repo_dir = shell_repo_dir
        self.mkdir_parents = shell_repo_dir == REPO_DIR  # only when shell paths == virtual paths
        self._path = posixpath if posix_shell else ntpath
        self.shell_state_dir = self._path.join(self._path.dirname(shell_repo_dir), ".shipwright")

    async def _exec(self, command: str, *, timeout: int | None = None) -> str:
        kwargs = {} if timeout is None else {"timeout": timeout}
        resp = await self.backend.aexecute(command, **kwargs)
        code = resp.exit_code if resp.exit_code is not None else parse_exit_code(resp.output)
        if code != 0:
            tail = "\n".join(resp.output.splitlines()[-20:])
            raise WorkspaceError(f"Command failed (exit {code}): {command}\n{tail}")
        return resp.output

    async def _git(self, args: str, *, timeout: int | None = None) -> str:
        return await self._exec(f"git -C {q(self.shell_repo_dir)} {args}", timeout=timeout)

    async def _capture_git(self, args: str, name: str) -> bytes:
        """Run git with stdout redirected to a unique file in `STATE_DIR`; return exact bytes."""
        unique = f"{name}-{uuid.uuid4().hex}"
        try:
            await self._git(f"{args} > {q(self._path.join(self.shell_state_dir, unique))}")
            content = await self._download(f"{STATE_DIR}/{unique}")
        finally:
            with contextlib.suppress(Exception):  # best effort: a leftover file is harmless
                await self.backend.adelete(f"{STATE_DIR}/{unique}")
        if content is None:
            raise WorkspaceError(f"git output file {unique} missing after: git {args}")
        return content

    async def _download_many(self, paths: list[str]) -> list[bytes | None]:
        responses = await self.backend.adownload_files(paths)
        return [r.content if r.error is None else None for r in responses]

    async def _download(self, path: str) -> bytes | None:
        return (await self._download_many([path]))[0]

    async def _upload(self, files: list[tuple[str, bytes]]) -> None:
        if not files:
            return
        if self.mkdir_parents:
            await self._exec("mkdir -p " + " ".join(q(posixpath.dirname(p)) for p, _ in files))
        responses = await self.backend.aupload_files(files)
        if failed := [f"{r.path}: {r.error}" for r in responses if r.error]:
            raise WorkspaceError("Upload failed: " + "; ".join(failed))
