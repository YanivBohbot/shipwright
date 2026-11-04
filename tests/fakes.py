"""Reusable test doubles (no network)."""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from shipwright.config import Settings
from shipwright.integrations.github import FileChange
from shipwright.tickets import Ticket, TicketNotFound

_FAKE_VALUES = {
    "openai_api_key": "sk-fake-openai",
    "model_light": "openai:gpt-5.5-mini",
    "langsmith_api_key": "ls-fake",
    "mongodb_uri": "mongodb://fake:27017",
    "pinecone_api_key": "pc-fake",
    "llama_cloud_api_key": "llx-fake",
    "tavily_api_key": "tvly-fake",
    "github_pat": "ghp_fake",
    "shipwright_repo": "acme/taskboard-api",
    "jira_url": "https://acme.atlassian.net",
    "jira_username": "bot@acme.com",
    "jira_api_token": "jira-fake",
}


def settings_stub(**overrides: Any) -> Settings:
    """Build a Settings object with fake values, ignoring any .env or environment."""
    return Settings(_env_file=None, **{**_FAKE_VALUES, **overrides})


class FakeVectorStore(VectorStore):
    """In-memory VectorStore recording add_texts calls; search is a substring/overlap match."""

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.texts: list[str] = []
        self.metadatas: list[dict] = []

    @classmethod
    def with_docs(cls, docs: Iterable[tuple[str, dict]]) -> "FakeVectorStore":
        store = cls()
        docs = list(docs)
        store.add_texts(
            [t for t, _ in docs],
            metadatas=[m for _, m in docs],
            ids=[f"d{i}" for i in range(len(docs))],
        )
        return store

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: list[dict] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        texts = list(texts)
        metadatas = metadatas or [{} for _ in texts]
        ids = ids or [f"auto{i}" for i in range(len(self.ids), len(self.ids) + len(texts))]
        self.texts.extend(texts)
        self.metadatas.extend(metadatas)
        self.ids.extend(ids)
        return ids

    async def aadd_texts(self, texts, metadatas=None, *, ids=None, **kwargs) -> list[str]:
        return self.add_texts(texts, metadatas, ids=ids, **kwargs)

    def similarity_search(self, query: str, k: int = 4, **kwargs: Any) -> list[Document]:
        words = set(query.lower().split())
        scored = sorted(
            zip(self.texts, self.metadatas, strict=True),
            key=lambda tm: -len(words & set(tm[0].lower().split())),
        )
        return [Document(page_content=t, metadata=m) for t, m in scored[:k]]

    async def asimilarity_search(self, query: str, k: int = 4, **kwargs: Any) -> list[Document]:
        return self.similarity_search(query, k=k, **kwargs)

    @classmethod
    def from_texts(cls, texts, embedding: Embeddings, metadatas=None, **kwargs):  # type: ignore[override]
        store = cls()
        store.add_texts(texts, metadatas)
        return store


class FakeTool:
    """Minimal async stand-in for a LangChain BaseTool: records ainvoke args, returns or raises.

    `result` may be a value, or a callable taking the args dict and returning the value.
    """

    def __init__(
        self, name: str = "fake_tool", result: Any = None, raises: Exception | None = None
    ):
        self.name = name
        self.result = result
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        self.calls.append(input)
        if self.raises is not None:
            raise self.raises
        return self.result(input) if callable(self.result) else self.result


class FakeJiraGateway:
    """In-memory JiraGateway recording writes."""

    def __init__(self, tickets: dict[str, Ticket] | None = None) -> None:
        self.tickets = dict(tickets or {})
        self.comments: list[tuple[str, str]] = []
        self.transitions: list[tuple[str, str]] = []
        self.subtasks: list[tuple[str, str, str]] = []
        self.issues: list[tuple[str, str, str, str]] = []

    async def get_issue(self, key: str) -> Ticket:
        if key not in self.tickets:
            raise TicketNotFound(key)
        return self.tickets[key]

    async def add_comment(self, key: str, body: str) -> str:
        self.comments.append((key, body))
        return str(len(self.comments))

    async def transition(self, key: str, to_status: str) -> None:
        self.transitions.append((key, to_status))

    async def create_subtask(self, parent_key: str, summary: str, description: str) -> str:
        self.subtasks.append((parent_key, summary, description))
        return f"{parent_key.rsplit('-', 1)[0]}-{100 + len(self.subtasks)}"

    async def create_issue(
        self, project: str, summary: str, description: str, issue_type: str
    ) -> str:
        self.issues.append((project, summary, description, issue_type))
        return f"{project}-{200 + len(self.issues)}"


FAKE_PR_URL = "https://github.com/fake/repo/pull/1"


class FakeGitHubGateway:
    """In-memory GitHubGateway recording writes. `open_prs` maps head branch -> PR URL."""

    def __init__(
        self, existing_branches: Iterable[str] = (), open_prs: dict[str, str] | None = None
    ) -> None:
        self.existing_branches = set(existing_branches)
        self.open_prs = dict(open_prs or {})
        self.branches: list[tuple[str, str, str]] = []
        self.pushes: list[tuple[str, str, list[FileChange], str]] = []
        self.prs: list[dict[str, Any]] = []
        self.pr_updates: list[tuple[str, str, str, str]] = []

    async def create_branch(self, repo: str, branch: str, from_branch: str = "main") -> None:
        self.branches.append((repo, branch, from_branch))
        self.existing_branches.add(branch)

    async def push_files(
        self, repo: str, branch: str, files: list[FileChange], message: str
    ) -> str:
        self.pushes.append((repo, branch, list(files), message))
        return f"sha{len(self.pushes)}"

    async def create_pull_request(
        self, repo: str, head: str, base: str, title: str, body: str, draft: bool = False
    ) -> str:
        self.prs.append(
            {"repo": repo, "head": head, "base": base, "title": title, "body": body, "draft": draft}
        )
        self.open_prs[head] = FAKE_PR_URL
        return FAKE_PR_URL

    async def branch_exists(self, repo: str, branch: str) -> bool:
        return branch in self.existing_branches

    async def find_open_pull_request(self, repo: str, head: str) -> str | None:
        return self.open_prs.get(head)

    async def update_pull_request(self, repo: str, pr_url: str, title: str, body: str) -> None:
        self.pr_updates.append((repo, pr_url, title, body))


_REDIRECT_RE = re.compile(r'>\s*"([^"]+)"\s*$')


class FakeSandbox:
    """In-memory sandbox backend: a dict of files plus a table `command pattern -> response`.

    Mimics a LangSmith sandbox (POSIX paths, shell paths == virtual paths). Each handler is
    matched by substring against the executed command (first match wins). When a matched
    command ends with a `> "<path>"` redirect, `redirect` bytes are written to that path.
    `effect(sandbox)` runs side effects (e.g. a clone creating `.git/HEAD`). Unmatched commands
    succeed with empty output. With `structured_exit=False` the exit code is only reported in
    the output as `[Command ... exit code N]` (exercises the parse fallback).
    """

    def __init__(
        self, files: dict[str, bytes] | None = None, *, structured_exit: bool = True
    ) -> None:
        self.files: dict[str, bytes] = dict(files or {})
        self.structured_exit = structured_exit
        self.handlers: list[tuple[str, dict[str, Any]]] = []
        self.executed: list[str] = []

    @property
    def id(self) -> str:
        return "fake-sandbox"

    def on(
        self,
        pattern: str,
        *,
        output: str = "",
        exit_code: int = 0,
        redirect: bytes = b"",
        effect: Callable[["FakeSandbox"], None] | None = None,
    ) -> "FakeSandbox":
        self.handlers.append(
            (
                pattern,
                {"output": output, "exit_code": exit_code, "redirect": redirect, "effect": effect},
            )
        )
        return self

    def count(self, pattern: str) -> int:
        return sum(pattern in c for c in self.executed)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.executed.append(command)
        spec = next((h for p, h in self.handlers if p in command), None) or {
            "output": "",
            "exit_code": 0,
            "redirect": b"",
            "effect": None,
        }
        if spec["effect"] is not None:
            spec["effect"](self)
        if (m := _REDIRECT_RE.search(command)) is not None and spec["exit_code"] == 0:
            self.files[m.group(1)] = spec["redirect"]
        output, code = spec["output"], spec["exit_code"]
        if self.structured_exit:
            return ExecuteResponse(output=output, exit_code=code)
        verdict = "succeeded" if code == 0 else "failed"
        return ExecuteResponse(output=f"{output}\n[Command {verdict} with exit code {code}]")

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self.execute(command, timeout=timeout)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        for path, content in files:
            self.files[path] = content
        return [FileUploadResponse(path=p, error=None) for p, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(path=p, content=self.files[p], error=None)
            if p in self.files
            else FileDownloadResponse(path=p, content=None, error="file_not_found")
            for p in paths
        ]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self.download_files(paths)


@dataclass
class FakeSandboxInfo:
    name: str
    status: str = "ready"


class FakeSandboxClient:
    """Stand-in for `langsmith.sandbox.SandboxClient` (sync API), recording calls."""

    def __init__(self, sandboxes: Iterable[FakeSandboxInfo] = ()) -> None:
        self.sandboxes = list(sandboxes)
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def list_sandboxes(self) -> list[FakeSandboxInfo]:
        return list(self.sandboxes)

    def create_sandbox(self, *, name: str, idle_ttl_seconds: int | None = None, **kwargs: Any):
        self.created.append({"name": name, "idle_ttl_seconds": idle_ttl_seconds, **kwargs})
        sandbox = FakeSandboxInfo(name=name)
        self.sandboxes.append(sandbox)
        return sandbox

    def delete_sandbox(self, name: str) -> None:
        self.deleted.append(name)
        self.sandboxes = [s for s in self.sandboxes if s.name != name]
