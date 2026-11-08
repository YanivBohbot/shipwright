"""Test-only doubles (no network): settings, sandbox, MCP tools, chat model, vector store."""

from collections.abc import Iterable
from typing import Any

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.vectorstores import VectorStore

from shipwright.core.config import Settings

REQUIRED_FAKES: dict[str, Any] = {
    "openai_api_key": "sk-fake-openai",
    "langsmith_api_key": "ls-fake",
    "github_pat": "ghp_fake",
    "shipwright_repo": "acme/taskboard-api",
    "jira_url": "https://acme.atlassian.net",
    "jira_username": "bot@acme.com",
    "jira_api_token": "jira-fake",
    "mongodb_uri": "mongodb://fake:27017",
    "pinecone_api_key": None,
    "llama_cloud_api_key": None,
    "tavily_api_key": None,
}


def make_settings(**overrides: Any) -> Settings:
    """Settings with fake values, ignoring `.env` and the environment."""
    return Settings(_env_file=None, **{**REQUIRED_FAKES, **overrides})


class MemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def record(self, event: Any) -> None:
        self.events.append(event)


class FakeSandbox:
    """In-memory sandbox: a dict of files plus `command substring -> (output, exit code)`."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = dict(files or {})
        self.handlers: list[tuple[str, str, int]] = []
        self.executed: list[str] = []

    @property
    def id(self) -> str:
        return "fake-sandbox"

    def on(self, pattern: str, output: str = "", exit_code: int = 0) -> "FakeSandbox":
        self.handlers.append((pattern, output, exit_code))
        return self

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.executed.append(command)
        output, code = next(((o, c) for p, o, c in self.handlers if p in command), ("", 0))
        return ExecuteResponse(output=output, exit_code=code)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError("tests use the async API")

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(path=p, content=self.files[p], error=None)
            if p in self.files
            else FileDownloadResponse(path=p, content=None, error="file_not_found")
            for p in paths
        ]


# The servers' real `readOnlyHint` annotations (checked against both live servers).
READ_ONLY_MCP_TOOLS = frozenset(
    {
        "list_branches",
        "list_commits",
        "list_pull_requests",
        "search_code",
        "jira_get_issue",
        "jira_search",
        "jira_get_transitions",
    }
)


def fake_mcp_tool(name: str, answer: str = "ok", calls: list | None = None) -> BaseTool:
    """A tool with an MCP tool's name and annotation, recording its arguments in `calls`."""
    recorded = calls if calls is not None else []

    async def run(**kwargs: Any) -> str:
        recorded.append((name, kwargs))
        return answer

    tool = StructuredTool.from_function(
        coroutine=run,
        name=name,
        description=f"Fake MCP tool {name}.",
        args_schema={"type": "object", "properties": {}, "additionalProperties": True},
    )
    tool.metadata = {"readOnlyHint": True if name in READ_ONLY_MCP_TOOLS else None}
    return tool


def fake_mcp(calls: list | None = None) -> dict[str, BaseTool]:
    """Every allowlisted MCP tool, faked: what `load_mcp_tools` returns."""
    from shipwright.integrations.mcp import ALLOWED_TOOLS

    return {n: fake_mcp_tool(n, answer=f"{n} done", calls=calls) for n in ALLOWED_TOOLS}


class ScriptedChatModel(GenericFakeChatModel):
    """GenericFakeChatModel that accepts `bind_tools` (returns itself)."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":  # type: ignore[override]
        return self


class FakeVectorStore(VectorStore):
    """In-memory VectorStore recording add_texts calls; search is a word-overlap match."""

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

    def add_texts(self, texts, metadatas=None, *, ids=None, **kwargs) -> list[str]:  # type: ignore[override]
        texts = list(texts)
        metadatas = metadatas or [{} for _ in texts]
        ids = ids or [f"auto{i}" for i in range(len(self.ids), len(self.ids) + len(texts))]
        self.texts.extend(texts)
        self.metadatas.extend(metadatas)
        self.ids.extend(ids)
        return ids

    async def aadd_texts(self, texts, metadatas=None, *, ids=None, **kwargs) -> list[str]:  # type: ignore[override]
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
