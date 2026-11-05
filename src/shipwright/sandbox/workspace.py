"""Workspace: the target repo cloned in a sandbox, the diff the human reviews, the files to push.

`files_matching_approval` is the publish guard: same diff hash as approved, and every file's
blob sha equal to the approved patch's `index` line (see `git_diff`).
"""

import hashlib
import re
from pathlib import Path

from deepagents.backends.protocol import SandboxBackendProtocol

from shipwright.integrations.github.gateway import FileChange
from shipwright.sandbox import git_diff
from shipwright.sandbox.shell import (  # noqa: F401  (re-exported for callers)
    REPO_DIR,
    STATE_DIR,
    SandboxShell,
    WorkspaceError,
    parse_exit_code,
    q,
)

SKILLS_DIR = "/skills"
BASE_SHA_FILE = f"{STATE_DIR}/base_sha"
INFO_ATTRIBUTES = f"{REPO_DIR}/.git/info/attributes"
BOT_NAME = "Shipwright Bot"
BOT_EMAIL = "shipwright-bot@users.noreply.github.com"
CLONE_TIMEOUT = 600

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# `--text`: a `-diff` / `binary` attribute can never hide a text change behind "Binary files".
_DIFF_FLAGS = "--no-color --no-ext-diff --no-textconv --no-renames --binary --full-index --text"
_RAW_FLAGS = "--raw -z --no-renames --full-index --no-color"


def validate_repo(repo: str) -> None:
    if not _REPO_RE.match(repo) or any(part in (".", "..") for part in repo.split("/")):
        raise WorkspaceError(f"Invalid repo (expected 'owner/name'): {repo!r}")


class Workspace(SandboxShell):
    """A cloned repo inside a sandbox backend (LangSmith sandbox or local shell)."""

    def __init__(
        self,
        backend: SandboxBackendProtocol,
        repo: str,
        clone_url: str,
        *,
        shell_repo_dir: str = REPO_DIR,
        posix_shell: bool = True,
        skills_source: Path | None = None,
    ) -> None:
        validate_repo(repo)
        super().__init__(backend, shell_repo_dir, posix_shell)
        self.repo, self.clone_url, self.skills_source = repo, clone_url, skills_source

    async def ensure_cloned(self) -> bool:
        """Clone once (recording the base sha) and upload the skills. True if cloned now."""
        cloned = False
        if await self._download(f"{REPO_DIR}/.git/HEAD") is None:
            await self._exec(
                "git clone --depth 50 --config core.autocrlf=false "
                f"{q(self.clone_url)} {q(self.shell_repo_dir)}",
                timeout=CLONE_TIMEOUT,
            )
            await self._git(f"config user.name {q(BOT_NAME)}")
            await self._git(f"config user.email {q(BOT_EMAIL)}")
            sha = (await self._git("rev-parse HEAD")).strip().splitlines()[0].strip()
            if not _SHA_RE.match(sha):
                raise WorkspaceError(f"Unexpected HEAD sha after clone: {sha!r}")
            await self._upload([(BASE_SHA_FILE, sha.encode())])
            cloned = True
        elif await self._download(BASE_SHA_FILE) is None:
            raise WorkspaceError(
                f"Workspace corrupt: repo exists but no {BASE_SHA_FILE}; "
                "delete the workspace/sandbox and retry."
            )
        if (src := self.skills_source) is not None and src.is_dir():
            files = [p for p in sorted(src.rglob("*")) if p.is_file()]
            await self._upload(
                [(f"{SKILLS_DIR}/{p.relative_to(src).as_posix()}", p.read_bytes()) for p in files]
            )
        return cloned

    async def base_sha(self) -> str:
        sha = ((await self._download(BASE_SHA_FILE)) or b"").decode("ascii", "replace").strip()
        if not _SHA_RE.match(sha):
            raise WorkspaceError(f"No valid base sha recorded at {BASE_SHA_FILE}")
        return sha

    async def _publishable_entries(self, sha: str) -> list[git_diff.RawEntry]:
        """Changed entries vs `sha` (after `add -N .`); refuse unsafe modes and info/attributes."""
        await self._git("add -N .")
        raw = await self._capture_git(f"diff {_RAW_FLAGS} {sha}", "raw")
        try:
            entries = git_diff.parse_raw_diff(raw)
        except git_diff.GitDiffError as e:
            raise WorkspaceError(str(e)) from e
        if bad := git_diff.unsafe_entries(entries):
            raise WorkspaceError(
                "Unpublishable changes (only regular-file edits, additions and deletions can be "
                "published; revert these): " + ", ".join(bad)
            )
        (resp,) = await self.backend.adownload_files([INFO_ATTRIBUTES])
        if resp.error != "file_not_found" and (resp.error or (resp.content or b"").strip()):
            raise WorkspaceError(
                "Unpublishable workspace: .git/info/attributes exists "
                "(it would change the reviewed diff); delete it"
            )
        return entries

    async def patch_and_hash(self) -> tuple[bytes, str]:
        """Exact `--full-index` patch bytes vs the base sha and their sha256 (one git read)."""
        sha = await self.base_sha()
        await self._publishable_entries(sha)
        patch = await self._capture_git(f"diff {_DIFF_FLAGS} {sha}", "diff.patch")
        return patch, hashlib.sha256(patch).hexdigest()

    async def diff_and_hash(self) -> tuple[str, str]:
        """`(diff text for the human, sha256 of the exact bytes)`; refuses unpublishable sets."""
        patch, digest = await self.patch_and_hash()
        return patch.decode("utf-8", errors="replace"), digest

    async def changed_files(self) -> list[FileChange]:
        """Changed files vs the base sha (deletions have `content=None`), sorted by path."""
        entries = await self._publishable_entries(await self.base_sha())
        status = {e.path: e.status for e in entries}
        present = [p for p in sorted(status) if status[p] != "D"]
        downloaded = await self._download_many([f"{REPO_DIR}/{p}" for p in present])
        contents = dict(zip(present, downloaded, strict=True))
        changes: list[FileChange] = []
        for path in sorted(status):
            if status[path] == "D":
                changes.append(FileChange(path, None))
                continue
            if (data := contents[path]) is None:
                raise WorkspaceError(f"Changed file could not be read: {path}")
            try:
                changes.append(FileChange(path, data.decode("utf-8")))
            except UnicodeDecodeError as e:
                raise WorkspaceError(f"Changed file is not UTF-8 text: {path}") from e
        return changes

    async def files_matching_approval(self, approved_hash: str) -> list[FileChange]:
        """The files to publish, guaranteed to be exactly the diff the human approved.

        Refuses if the diff hash changed since approval, or if any file's blob sha differs from
        the `index` line of the approved patch (clean filters, assume-unchanged...).
        """
        patch, digest = await self.patch_and_hash()
        if digest != approved_hash:
            raise WorkspaceError("The workspace changed since the human approved the diff")
        files = await self.changed_files()
        if problems := git_diff.verify_files_match_diff(patch, files):
            raise WorkspaceError("Files differ from the approved diff: " + "; ".join(problems))
        return files

    async def read_repo_file(self, rel_path: str) -> str | None:
        """UTF-8 content of a repo file (relative, `/`-separated), or None if it does not exist."""
        parts = rel_path.replace("\\", "/").split("/")
        bad = any(p in ("", ".", "..") for p in parts) or ":" in parts[0]
        if not rel_path or rel_path.startswith(("/", "\\")) or bad:
            raise ValueError(f"Invalid repo-relative path: {rel_path!r}")
        (resp,) = await self.backend.adownload_files([f"{REPO_DIR}/{'/'.join(parts)}"])
        if resp.error == "file_not_found":
            return None
        if resp.error is not None or resp.content is None:  # never "absent" on other errors
            raise WorkspaceError(f"Repo file could not be read: {rel_path} ({resp.error})")
        try:
            return resp.content.decode("utf-8")
        except UnicodeDecodeError as e:
            raise WorkspaceError(f"Repo file is not UTF-8 text: {rel_path}") from e
