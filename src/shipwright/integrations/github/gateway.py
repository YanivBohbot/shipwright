"""What the agent needs from GitHub to publish a PR: the `GitHubGateway` protocol + FileChange.

Implemented by `McpGitHub` (live, GitHub MCP server) and `LocalGitHub` (local bare repo).
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FileChange:
    path: str
    content: str | None  # None = delete


class GitHubGateway(Protocol):
    async def list_branch_names(self, repo: str) -> set[str]: ...
    async def get_branch_sha(self, repo: str, branch: str) -> str: ...
    async def create_branch(self, repo: str, branch: str, from_branch: str = "main") -> None: ...
    async def push_files(
        self, repo: str, branch: str, files: list[FileChange], message: str
    ) -> str: ...
    async def create_pull_request(
        self, repo: str, head: str, base: str, title: str, body: str, draft: bool = False
    ) -> str: ...
    async def find_open_pull_request(self, repo: str, head: str) -> str | None: ...
