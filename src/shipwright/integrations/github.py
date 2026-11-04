"""GitHub gateway: a Protocol plus an implementation over the official GitHub MCP server."""

import re
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.tools import BaseTool

from shipwright.integrations.mcp_client import normalize_tool_result

# Tool names of github/github-mcp-server (remote, https://api.githubcopilot.com/mcp/).
TOOL_CREATE_BRANCH = "create_branch"
TOOL_PUSH_FILES = "push_files"
TOOL_DELETE_FILE = "delete_file"
TOOL_CREATE_PULL_REQUEST = "create_pull_request"
TOOL_LIST_BRANCHES = "list_branches"
TOOL_LIST_PULL_REQUESTS = "list_pull_requests"
TOOL_UPDATE_PULL_REQUEST = "update_pull_request"

_PAGE_SIZE = 100
_MAX_PAGES = 10


@dataclass(frozen=True)
class FileChange:
    path: str
    content: str | None  # None = delete


class GitHubGateway(Protocol):
    async def create_branch(self, repo: str, branch: str, from_branch: str = "main") -> None: ...
    async def push_files(
        self, repo: str, branch: str, files: list[FileChange], message: str
    ) -> str: ...
    async def create_pull_request(
        self, repo: str, head: str, base: str, title: str, body: str, draft: bool = False
    ) -> str: ...
    async def branch_exists(self, repo: str, branch: str) -> bool: ...
    async def find_open_pull_request(self, repo: str, head: str) -> str | None: ...
    async def update_pull_request(self, repo: str, pr_url: str, title: str, body: str) -> None: ...


def _split_repo(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        raise ValueError(f"repo must have the format 'owner/name', got {repo!r}")
    return owner, name


def _pr_number(pr_url: str) -> int:
    m = re.search(r"/pull/(\d+)", pr_url)
    if not m:
        raise ValueError(f"Not a pull request URL: {pr_url!r}")
    return int(m.group(1))


def _find_key(data: Any, key: str) -> Any:
    """Depth-first search for `key` in nested dicts/lists (MCP result shapes vary by version)."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        children: Any = data.values()
    elif isinstance(data, list):
        children = data
    else:
        return None
    for child in children:
        found = _find_key(child, key)
        if found is not None:
            return found
    return None


def _as_list(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


class McpGitHubGateway:
    def __init__(self, tools: dict[str, BaseTool]) -> None:
        self._tools = tools

    async def _call(self, tool: str, **args: Any) -> Any:
        if tool not in self._tools:
            raise KeyError(f"MCP tool '{tool}' is not available on the GitHub server")
        return normalize_tool_result(await self._tools[tool].ainvoke(args))

    async def create_branch(self, repo: str, branch: str, from_branch: str = "main") -> None:
        owner, name = _split_repo(repo)
        await self._call(
            TOOL_CREATE_BRANCH, owner=owner, repo=name, branch=branch, from_branch=from_branch
        )

    async def push_files(
        self, repo: str, branch: str, files: list[FileChange], message: str
    ) -> str:
        """Push writes in one commit via push_files, then delete files one by one (delete_file)."""
        owner, name = _split_repo(repo)
        sha = ""
        writes = [{"path": f.path, "content": f.content} for f in files if f.content is not None]
        if writes:
            data = await self._call(
                TOOL_PUSH_FILES,
                owner=owner,
                repo=name,
                branch=branch,
                files=writes,
                message=message,
            )
            sha = str(_find_key(data, "sha") or "")
        for f in files:
            if f.content is None:
                data = await self._call(
                    TOOL_DELETE_FILE,
                    owner=owner,
                    repo=name,
                    branch=branch,
                    path=f.path,
                    message=message,
                )
                sha = str(_find_key(data, "sha") or sha)
        return sha

    async def create_pull_request(
        self, repo: str, head: str, base: str, title: str, body: str, draft: bool = False
    ) -> str:
        owner, name = _split_repo(repo)
        data = await self._call(
            TOOL_CREATE_PULL_REQUEST,
            owner=owner,
            repo=name,
            head=head,
            base=base,
            title=title,
            body=body,
            draft=draft,
        )
        url = _find_key(data, "html_url") or _find_key(data, "url")
        if not url:
            raise RuntimeError(f"GitHub did not return a pull request URL: {data!r}")
        return str(url)

    async def branch_exists(self, repo: str, branch: str) -> bool:
        owner, name = _split_repo(repo)
        for page in range(1, _MAX_PAGES + 1):
            data = await self._call(
                TOOL_LIST_BRANCHES, owner=owner, repo=name, page=page, perPage=_PAGE_SIZE
            )
            items = _as_list(data)
            if any(isinstance(b, dict) and b.get("name") == branch for b in items):
                return True
            if len(items) < _PAGE_SIZE:
                return False
        raise RuntimeError(
            f"branch_exists gave up after {_MAX_PAGES * _PAGE_SIZE} branches in {repo}; "
            "cannot determine whether the branch exists"
        )

    async def find_open_pull_request(self, repo: str, head: str) -> str | None:
        owner, name = _split_repo(repo)
        data = await self._call(
            TOOL_LIST_PULL_REQUESTS,
            owner=owner,
            repo=name,
            head=head if ":" in head else f"{owner}:{head}",
            state="open",
        )
        for pr in _as_list(data):
            if isinstance(pr, dict):
                url = pr.get("html_url") or pr.get("url")
                if url:
                    return str(url)
        return None

    async def update_pull_request(self, repo: str, pr_url: str, title: str, body: str) -> None:
        owner, name = _split_repo(repo)
        await self._call(
            TOOL_UPDATE_PULL_REQUEST,
            owner=owner,
            repo=name,
            pullNumber=_pr_number(pr_url),
            title=title,
            body=body,
        )
