"""Reusable test doubles (no network)."""

from collections.abc import Iterable
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from shipwright.config import Settings

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
