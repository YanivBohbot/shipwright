"""Workspace sandbox: clone the target repo, compute the reviewed diff, ship skills.

Two path spaces coexist and must never be mixed:

- **Virtual paths** (`REPO_DIR`, `SKILLS_DIR`, `STATE_DIR`) are what the filesystem tools
  and `upload_files` / `download_files` see.
- **Shell paths** (`Workspace.shell_repo_dir`) are what `execute` sees. In a LangSmith
  sandbox they equal the virtual paths; with the local provider (`LocalShellBackend`) they are
  real host paths, and on Windows the shell is `cmd.exe`.

Every shell command is therefore written to run unchanged under both `sh` and `cmd.exe`:
double quotes only, no `cd`, no `test`, no `2>/dev/null`; `git -C "<dir>"` everywhere. Command
stdout that matters (diff, file lists) is redirected to a file in `STATE_DIR` (outside the
repo) and read back as bytes, so stderr warnings, output truncation or `<no output>`
placeholders can never leak into the diff the human approves.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import ntpath
import os
import posixpath
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents.backends import LangSmithSandbox, LocalShellBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from shipwright.config import Settings
from shipwright.integrations.github import FileChange

REPO_DIR = "/workspace/repo"
SKILLS_DIR = "/skills"
STATE_DIR = "/workspace/.shipwright"  # = {REPO_DIR}/../.shipwright, outside the repo
BASE_SHA_FILE = f"{STATE_DIR}/base_sha"

DEFAULT_SKILLS_SOURCE = Path(__file__).parent / "skills"
BOT_NAME = "Shipwright Bot"
BOT_EMAIL = "shipwright-bot@users.noreply.github.com"
CLONE_TIMEOUT = 600
CLEAN_ENV_KEYS = ("PATH", "SYSTEMROOT", "TEMP")  # HOME/USERPROFILE are redirected
SANDBOX_HOME = ".home"  # per-thread fake home inside the local workspace root

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_EXIT_RE = re.compile(r"\[Command [^\]]*exit code (-?\d+)\]|^Exit code: (-?\d+)$", re.MULTILINE)
# `--text`: a `-diff` / `binary` attribute (in-tree `.gitattributes`) can never hide a text
# change behind "Binary files differ" / a `GIT binary patch` the human cannot read.
_DIFF_FLAGS = "--no-color --no-ext-diff --no-textconv --no-renames --binary --full-index --text"
_RAW_FLAGS = "--raw -z --no-renames --full-index --no-color"
REGULAR_MODE = "100644"
ABSENT_MODE = "000000"
SYMLINK_MODE = "120000"
GITLINK_MODE = "160000"
INFO_ATTRIBUTES = f"{REPO_DIR}/.git/info/attributes"


class WorkspaceError(RuntimeError):
    """A workspace shell or file operation failed."""


@dataclass(frozen=True)
class RawEntry:
    """One `git diff --raw` entry vs the base commit."""

    old_mode: str
    new_mode: str
    status: str  # A / M / D / T ...
    path: str


def unpublishable_reason(entry: RawEntry) -> str | None:
    """Why `entry` cannot be published as a plain file write/delete, or None if it can.

    `publish_pull_request` pushes regular-file contents (mode 100644) and deletions only, so
    symlinks, gitlinks (embedded repos / submodules), executable new files and ANY mode change
    would make the PR differ from the diff the human approved: they are refused (fail closed).
    """
    modes = (entry.old_mode, entry.new_mode)
    if SYMLINK_MODE in modes:
        return "symlink"
    if GITLINK_MODE in modes:
        return "gitlink (embedded repository or submodule)"
    if ABSENT_MODE not in modes and entry.old_mode != entry.new_mode:
        return f"mode change {entry.old_mode} -> {entry.new_mode}"
    if entry.old_mode == ABSENT_MODE and entry.new_mode != REGULAR_MODE:
        return f"new file with mode {entry.new_mode}"
    return None


def parse_raw_diff(raw: bytes) -> list[RawEntry]:
    """Parse `git diff --raw -z --no-renames` output (`:old new sha sha STATUS\\0path\\0`)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise WorkspaceError("A changed file path is not valid UTF-8") from e
    fields = text.split("\0")
    entries: list[RawEntry] = []
    for meta, path in zip(fields[0::2], fields[1::2], strict=False):
        if not meta:
            continue
        parts = meta.lstrip(":").split()
        if len(parts) != 5 or not path:
            raise WorkspaceError(f"Unexpected git diff --raw entry: {meta!r} {path!r}")
        entries.append(RawEntry(parts[0], parts[1], parts[4][:1], path))
    return entries


def parse_exit_code(output: str) -> int:
    """Exit code from the final `[Command ... exit code N]` (or `Exit code: N`) marker.

    Fallback for backends whose `ExecuteResponse.exit_code` is `None`. Returns -1 when no
    marker is found, so an unknown outcome is always treated as a failure.
    """
    matches = list(_EXIT_RE.finditer(output))
    if not matches:
        return -1
    last = matches[-1]
    return int(last.group(1) or last.group(2))


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
    """Minimal environment for local shell commands.

    No Settings secret is visible, and the user's git/ssh credentials are not reachable by
    default: HOME/USERPROFILE point to a per-thread dir under `workspace_root`, the global git
    config is an empty file there, the system config (credential helpers) is disabled and git
    never prompts. Not a sandbox boundary: `LocalShellBackend` is dev/tests/evals only.
    """
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


def _validate_repo(repo: str) -> None:
    if not _REPO_RE.match(repo) or any(part in (".", "..") for part in repo.split("/")):
        raise WorkspaceError(f"Invalid repo (expected 'owner/name'): {repo!r}")


def _q(arg: str) -> str:
    """Double-quote an argument; valid in both `sh` and `cmd.exe` for our controlled values."""
    if any(c in arg for c in '"\n\r%$`'):
        raise WorkspaceError(f"Unsafe shell argument: {arg!r}")
    return f'"{arg}"'


def _tail(text: str, n: int = 20) -> str:
    return "\n".join(text.splitlines()[-n:])


class Workspace:
    """A cloned repo inside a sandbox backend (LangSmith sandbox or local shell)."""

    def __init__(
        self,
        backend: SandboxBackendProtocol,
        repo: str,
        *,
        shell_repo_dir: str = REPO_DIR,
        posix_shell: bool = True,
        mkdir_parents: bool = True,
        clone_url: str | None = None,
        skills_source: Path = DEFAULT_SKILLS_SOURCE,
    ) -> None:
        """
        Args:
            backend: Sandbox backend with `aexecute` / `aupload_files` / `adownload_files`.
            repo: `owner/name` of the public GitHub repo.
            shell_repo_dir: Repo path as seen by `execute` (host path for the local provider).
            posix_shell: Whether `execute` runs a POSIX shell (False: Windows `cmd.exe`).
            mkdir_parents: Create parent dirs with `mkdir -p` before uploads (only valid when
                shell paths equal virtual paths, i.e. the LangSmith sandbox).
            clone_url: Override of `https://github.com/{repo}.git` (tests).
            skills_source: Local directory whose files are uploaded under `SKILLS_DIR`.
        """
        _validate_repo(repo)
        self.backend = backend
        self.repo = repo
        self.shell_repo_dir = shell_repo_dir
        self.posix_shell = posix_shell
        self.mkdir_parents = mkdir_parents
        self.clone_url = clone_url or f"https://github.com/{repo}.git"
        self.skills_source = Path(skills_source)
        pathmod = posixpath if posix_shell else ntpath
        self._shell_join = pathmod.join
        self.shell_state_dir = pathmod.join(pathmod.dirname(shell_repo_dir), ".shipwright")

    # ------------------------------------------------------------------ primitives

    async def _exec(self, command: str, *, timeout: int | None = None) -> str:
        if timeout is None:
            resp = await self.backend.aexecute(command)
        else:
            resp = await self.backend.aexecute(command, timeout=timeout)
        code = resp.exit_code if resp.exit_code is not None else parse_exit_code(resp.output)
        if code != 0:
            raise WorkspaceError(f"Command failed (exit {code}): {command}\n{_tail(resp.output)}")
        return resp.output

    async def _git(self, args: str, *, timeout: int | None = None) -> str:
        return await self._exec(f"git -C {_q(self.shell_repo_dir)} {args}", timeout=timeout)

    async def _capture_git(self, args: str, name: str) -> bytes:
        """Run git with stdout redirected to a unique file in `STATE_DIR`; return exact bytes.

        The file name is unique per call (concurrent calls never share it) and the file is
        removed after reading.
        """
        unique = f"{name}-{uuid.uuid4().hex}"
        target = self._shell_join(self.shell_state_dir, unique)
        virtual = f"{STATE_DIR}/{unique}"
        try:
            await self._git(f"{args} > {_q(target)}")
            content = await self._download(virtual)
        finally:
            with contextlib.suppress(Exception):  # best effort: a leftover file is harmless
                await self.backend.adelete(virtual)
        if content is None:
            raise WorkspaceError(f"git output file {unique} missing after: git {args}")
        return content

    async def _download_many(self, paths: Sequence[str]) -> list[Any]:
        responses = await self.backend.adownload_files(list(paths))
        return [r.content if r.error is None else None for r in responses]

    async def _download(self, path: str) -> bytes | None:
        return (await self._download_many([path]))[0]

    async def _upload(self, files: list[tuple[str, bytes]]) -> None:
        if not files:
            return
        if self.mkdir_parents:
            parents = sorted({posixpath.dirname(p) for p, _ in files})
            await self._exec("mkdir -p " + " ".join(_q(p) for p in parents))
        responses = await self.backend.aupload_files(files)
        failed = [f"{r.path}: {r.error}" for r in responses if r.error is not None]
        if failed:
            raise WorkspaceError("Upload failed: " + "; ".join(failed))

    # ------------------------------------------------------------------ public API

    async def ensure_cloned(self) -> bool:
        """Clone the repo (once) and upload the skills. Returns True if cloned now.

        The base SHA is recorded only right after a fresh clone, so a rebuilt Workspace never
        moves the diff base past commits made by the developer.
        """
        cloned = False
        if await self._download(f"{REPO_DIR}/.git/HEAD") is not None:
            if await self._download(BASE_SHA_FILE) is None:
                raise WorkspaceError(
                    "Workspace corrupt: repo exists but no base sha was recorded "
                    f"({BASE_SHA_FILE}); delete the workspace/sandbox and retry."
                )
        else:
            await self._exec(
                "git clone --depth 50 --config core.autocrlf=false "
                f"{_q(self.clone_url)} {_q(self.shell_repo_dir)}",
                timeout=CLONE_TIMEOUT,
            )
            await self._git(f"config user.name {_q(BOT_NAME)}")
            await self._git(f"config user.email {_q(BOT_EMAIL)}")
            sha = (await self._git("rev-parse HEAD")).strip().splitlines()[0].strip()
            if not _SHA_RE.match(sha):
                raise WorkspaceError(f"Unexpected HEAD sha after clone: {sha!r}")
            await self._upload([(BASE_SHA_FILE, sha.encode())])
            cloned = True
        await self._upload(self._skill_files())
        return cloned

    def _skill_files(self) -> list[tuple[str, bytes]]:
        if not self.skills_source.is_dir():
            return []
        return [
            (f"{SKILLS_DIR}/{p.relative_to(self.skills_source).as_posix()}", p.read_bytes())
            for p in sorted(self.skills_source.rglob("*"))
            if p.is_file()
        ]

    async def base_sha(self) -> str:
        raw = await self._download(BASE_SHA_FILE)
        sha = (raw or b"").decode("ascii", errors="replace").strip()
        if not _SHA_RE.match(sha):
            raise WorkspaceError(f"No valid base sha recorded at {BASE_SHA_FILE}")
        return sha

    async def _raw_entries(self, sha: str) -> list[RawEntry]:
        """Changed entries (with modes) vs `sha`; call after `add -N .` (untracked included)."""
        return parse_raw_diff(await self._capture_git(f"diff {_RAW_FLAGS} {sha}", "raw"))

    async def _check_publishable(self, entries: list[RawEntry]) -> None:
        """Raise WorkspaceError unless the change set can be published exactly as diffed.

        Refuses symlinks, gitlinks, mode changes and executable new files (see
        `unpublishable_reason`), and a repo-local `.git/info/attributes` file: it is invisible
        in the diff yet could change how git renders it (eol/text/diff attributes).
        """
        bad = [f"{e.path} ({reason})" for e in entries if (reason := unpublishable_reason(e))]
        if bad:
            raise WorkspaceError(
                "Unpublishable changes (only regular-file edits, additions and deletions can be "
                "published; revert these): " + ", ".join(bad)
            )
        (resp,) = await self.backend.adownload_files([INFO_ATTRIBUTES])
        if resp.error == "file_not_found":
            return
        if resp.error is not None or (resp.content or b"").strip():
            raise WorkspaceError(
                "Unpublishable workspace: .git/info/attributes exists (repo-local git attributes "
                "would change the reviewed diff); delete it"
            )

    async def _diff_bytes(self, *, publishable: bool) -> bytes:
        sha = await self.base_sha()
        await self._git("add -N .")
        if publishable:
            await self._check_publishable(await self._raw_entries(sha))
        return await self._capture_git(f"diff {_DIFF_FLAGS} {sha}", "diff.patch")

    async def diff(self) -> str:
        """Full diff vs the base SHA: committed + uncommitted + new (untracked) files.

        For display only (never refuses); the review gate and publication use
        `diff_and_hash` / `diff_hash`, which refuse unpublishable change sets.
        """
        return (await self._diff_bytes(publishable=False)).decode("utf-8", errors="replace")

    async def diff_hash(self) -> str:
        """sha256 hex of the exact diff bytes git produced.

        Raises WorkspaceError if the change set is not publishable (`_check_publishable`).
        """
        return hashlib.sha256(await self._diff_bytes(publishable=True)).hexdigest()

    async def diff_and_hash(self) -> tuple[str, str]:
        """`(diff(), diff_hash())` computed from ONE git read, so the hash matches the text.

        Raises WorkspaceError if the change set is not publishable (`_check_publishable`).
        """
        raw = await self._diff_bytes(publishable=True)
        return raw.decode("utf-8", errors="replace"), hashlib.sha256(raw).hexdigest()

    async def read_repo_file(self, rel_path: str) -> str | None:
        """UTF-8 content of a file of the repo working tree, or None if it does not exist.

        `rel_path` is relative to the repo root (`/`-separated); absolute paths and `..` are
        rejected with ValueError.
        """
        parts = rel_path.replace("\\", "/").split("/")
        if (
            not rel_path
            or rel_path.startswith(("/", "\\"))
            or ":" in parts[0]
            or any(p in ("", ".", "..") for p in parts)
        ):
            raise ValueError(f"Invalid repo-relative path: {rel_path!r}")
        (resp,) = await self.backend.adownload_files([f"{REPO_DIR}/{'/'.join(parts)}"])
        if resp.error == "file_not_found":
            return None
        if resp.error is not None or resp.content is None:
            # never report "absent" on other errors: the caller would overwrite the file
            raise WorkspaceError(f"Repo file could not be read: {rel_path} ({resp.error})")
        data = resp.content
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise WorkspaceError(f"Repo file is not UTF-8 text: {rel_path}") from e

    async def changed_files(self) -> list[FileChange]:
        """Individual changed files vs the base SHA (ignored files excluded), sorted by path.

        Renames appear as delete + add. Deleted files have `content=None`. Raises
        WorkspaceError (naming the paths) if any entry is not a plain regular-file change
        (symlink, gitlink, mode change...), so publication fails closed.
        """
        sha = await self.base_sha()
        await self._git("add -N .")  # same index state as diff(): untracked files included
        entries = await self._raw_entries(sha)
        await self._check_publishable(entries)
        status = {e.path: e.status for e in entries}

        paths = sorted(status)
        present = [p for p in paths if status[p] != "D"]
        contents = dict(
            zip(
                present,
                await self._download_many([f"{REPO_DIR}/{p}" for p in present]),
                strict=True,
            )
        )
        changes: list[FileChange] = []
        for path in paths:
            if status[path] == "D":
                changes.append(FileChange(path=path, content=None))
                continue
            data = contents[path]
            if data is None:
                raise WorkspaceError(f"Changed file could not be read: {path}")
            try:
                changes.append(FileChange(path=path, content=data.decode("utf-8")))
            except UnicodeDecodeError as e:
                raise WorkspaceError(f"Changed file is not UTF-8 text: {path}") from e
        return changes


@functools.cache
def shared_sandbox_client(api_key: str) -> Any:
    """One LangSmith `SandboxClient` per process and API key (reused across runs/threads)."""
    from langsmith.sandbox import SandboxClient

    return SandboxClient(api_key=api_key)


async def open_workspace(
    settings: Settings,
    *,
    thread_id: str,
    repo: str,
    client: Any = None,
    skills_source: Path = DEFAULT_SKILLS_SOURCE,
    local_root: Path | str | None = None,
) -> Workspace:
    """Build the Workspace for a thread (the repo is cloned lazily by `ensure_cloned`).

    Provider `langsmith`: reuse the sandbox named `shipwright-{thread_id}` or create it.
    Provider `local`: `LocalShellBackend` rooted at `{local_root}/{thread_id}` (default
    `tmp/shipwright`) with a cleaned environment; dev/tests/evals only.
    """
    if not _THREAD_ID_RE.match(thread_id):
        raise ValueError(f"Unsafe thread_id for a workspace name: {thread_id!r}")
    _validate_repo(repo)  # before any sandbox is created

    if settings.shipwright_sandbox_provider == "local":
        root = (Path(local_root or "tmp/shipwright") / thread_id).resolve()
        root.mkdir(parents=True, exist_ok=True)
        backend = LocalShellBackend(
            root_dir=root, virtual_mode=True, env=clean_shell_env(root), timeout=CLONE_TIMEOUT
        )
        return Workspace(
            backend,
            repo,
            shell_repo_dir=str(root / "workspace" / "repo"),
            posix_shell=os.name != "nt",
            mkdir_parents=False,
            skills_source=skills_source,
        )

    if client is None:
        client = shared_sandbox_client(settings.langsmith_api_key.get_secret_value())
    name = f"shipwright-{thread_id}"
    existing = [s for s in await asyncio.to_thread(client.list_sandboxes) if s.name == name]
    sandbox = next((s for s in existing if getattr(s, "status", "ready") != "failed"), None)
    if sandbox is None:
        if existing:
            await asyncio.to_thread(client.delete_sandbox, name)
        sandbox = await asyncio.to_thread(
            client.create_sandbox, name=name, idle_ttl_seconds=settings.sandbox_idle_ttl_seconds
        )
    return Workspace(LangSmithSandbox(sandbox=sandbox), repo, skills_source=skills_source)
