"""`GitHubGateway` for local mode: a bare git repo is the "remote", a PR is a Markdown file.

Branches and commits are real (git plumbing on the bare repo, no worktree), so the sandbox can
clone the remote and `git log shipwright/ship-1` shows what was published.
"""

import asyncio
import os
import re
import subprocess
import tempfile
from pathlib import Path

from shipwright.core.config import LOCAL_REPO
from shipwright.integrations.github.gateway import FileChange

BOT_NAME = "Shipwright Bot"
BOT_EMAIL = "shipwright-bot@users.noreply.github.com"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _check_path(path: str) -> None:
    parts = path.split("/")
    if not path or "\\" in path or ":" in path or any(p in ("", ".", "..", ".git") for p in parts):
        raise ValueError(f"Invalid repo file path: {path!r}")


def ensure_bare_remote(source: Path, remote: Path) -> Path:
    """Create `remote` with `git clone --bare <source>` unless it already exists."""
    if not (remote / "HEAD").is_file():
        remote.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--bare", "-q", str(Path(source).resolve()), str(remote)],
            check=True,
            capture_output=True,
            env=_env(remote),
        )
    return remote


def _env(remote: Path, **extra: str) -> dict[str, str]:
    """Host env with the user's global/system git config hidden (autocrlf, hooks, signing...)."""
    empty = remote.parent / f"{remote.name}.gitconfig"
    if not empty.exists():
        empty.parent.mkdir(parents=True, exist_ok=True)
        empty.write_bytes(b"")
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": BOT_NAME,
        "GIT_AUTHOR_EMAIL": BOT_EMAIL,
        "GIT_COMMITTER_NAME": BOT_NAME,
        "GIT_COMMITTER_EMAIL": BOT_EMAIL,
        **extra,
    }


class LocalGitHub:
    def __init__(self, remote: Path, prs_dir: Path, repo: str = LOCAL_REPO) -> None:
        self.remote = Path(remote)
        self.prs_dir = Path(prs_dir)
        self.repo = repo

    def _git(self, *args: str, input: bytes | None = None, **env: str) -> str:
        done = subprocess.run(
            ["git", "--git-dir", str(self.remote), *args],
            input=input,
            capture_output=True,
            env=_env(self.remote, **env),
        )
        if done.returncode != 0:
            err = done.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"git {args[0]} failed: {err}")
        return done.stdout.decode().strip()

    async def _run(self, *args: str, **kwargs) -> str:
        return await asyncio.to_thread(self._git, *args, **kwargs)

    def _check(self, repo: str, *branches: str) -> None:
        if repo != self.repo:
            raise ValueError(f"LocalGitHub serves {self.repo!r}, not {repo!r}")
        for b in branches:
            self._git("check-ref-format", f"refs/heads/{b}")

    def _pr_file(self, head: str) -> Path:
        return self.prs_dir / f"{head}.md"  # branch "shipwright/ship-1" -> shipwright/ship-1.md

    async def list_branch_names(self, repo: str) -> set[str]:
        self._check(repo)
        out = await self._run("for-each-ref", "--format=%(refname:short)", "refs/heads/")
        return set(out.split())

    async def get_branch_sha(self, repo: str, branch: str) -> str:
        self._check(repo, branch)
        try:
            sha = await self._run("rev-parse", "--verify", "-q", f"refs/heads/{branch}^{{commit}}")
        except RuntimeError as e:
            raise RuntimeError(f"Branch {branch!r} not found in {repo}") from e
        if not _SHA_RE.match(sha):
            raise RuntimeError(f"Unexpected sha for branch {branch!r}: {sha!r}")
        return sha

    async def create_branch(self, repo: str, branch: str, from_branch: str = "main") -> None:
        sha = await self.get_branch_sha(repo, from_branch)
        self._check(repo, branch)
        try:  # empty old value: fails if the branch already exists
            await self._run("update-ref", f"refs/heads/{branch}", sha, "")
        except RuntimeError as e:
            raise RuntimeError(f"Reference already exists: {branch}") from e

    async def push_files(
        self, repo: str, branch: str, files: list[FileChange], message: str
    ) -> str:
        """One commit on `branch` with exactly `files` written (mode 100644) or deleted."""
        parent = await self.get_branch_sha(repo, branch)
        for f in files:
            _check_path(f.path)
        with tempfile.TemporaryDirectory(prefix="shipwright-index-") as tmp:
            # A throwaway index and an empty work tree: the remote itself is never touched.
            (Path(tmp) / "wt").mkdir()
            idx = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), "GIT_WORK_TREE": f"{tmp}/wt"}
            await self._run("read-tree", parent, **idx)
            for f in files:
                if f.content is None:
                    await self._run("update-index", "--force-remove", "--", f.path, **idx)
                    continue
                data = f.content.encode("utf-8")
                blob = await self._run("hash-object", "-w", "--no-filters", "--stdin", input=data)
                info = f"100644,{blob},{f.path}"
                await self._run("update-index", "--add", "--cacheinfo", info, **idx)
            tree = await self._run("write-tree", **idx)
        commit = await self._run("commit-tree", tree, "-p", parent, "-m", message)
        await self._run("update-ref", f"refs/heads/{branch}", commit, parent)
        return commit

    async def create_pull_request(
        self, repo: str, head: str, base: str, title: str, body: str, draft: bool = False
    ) -> str:
        head_sha = await self.get_branch_sha(repo, head)
        base_sha = await self.get_branch_sha(repo, base)
        path = self._pr_file(head)
        if path.exists():
            raise RuntimeError(f"A pull request already exists for {head}")
        changes = await self._run("diff", "--name-status", "--no-renames", base_sha, head_sha)
        files = "\n".join(f"- `{c}`" for c in changes.splitlines()) or "- (no changes)"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# {title}\n\n- base: `{base}` ({base_sha})\n- head: `{head}` ({head_sha})\n"
            f"- draft: {str(draft).lower()}\n\n## Files\n{files}\n\n## Description\n{body}\n",
            encoding="utf-8",
        )
        return path.resolve().as_uri()

    async def find_open_pull_request(self, repo: str, head: str) -> str | None:
        self._check(repo, head)
        path = self._pr_file(head)
        return path.resolve().as_uri() if path.exists() else None
