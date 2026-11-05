"""Knowledge search with one API, `search(query, k)`, over two stores.

`KnowledgeSearcher` uses a vector store (Pinecone); `LocalKeywordSearcher` scores the Markdown
files of `knowledge/` by keyword overlap, so the demo works without Pinecone.
"""

import re
from pathlib import Path

from langchain_core.vectorstores import VectorStore

from shipwright.core.config import Settings
from shipwright.knowledge.ingest import KnowledgeChunk, chunk_markdown

_WORD_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_STOPWORDS = frozenset(
    "a an and are as at be by do for from how i in is it of on or the this to what when with"
    " should does".split()
)


class KnowledgeSearcher:
    """Semantic search over a LangChain vector store."""

    def __init__(self, vector_store: VectorStore):
        self._store = vector_store

    async def search(self, query: str, k: int = 5) -> list[KnowledgeChunk]:
        docs = await self._store.asimilarity_search(query, k=k)
        return [
            KnowledgeChunk(
                text=d.page_content,
                source=d.metadata.get("source", ""),
                doc_id=d.metadata.get("doc_id", ""),
                section=d.metadata.get("section", ""),
            )
            for d in docs
        ]


def _words(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]


class LocalKeywordSearcher:
    """Keyword search over the `.md` files under `root` (chunked like the ingestion)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._chunks: list[KnowledgeChunk] | None = None

    def _load(self) -> list[KnowledgeChunk]:
        if self._chunks is None:
            self._chunks = [
                chunk
                for path in sorted(self.root.rglob("*.md"))
                for chunk in chunk_markdown(
                    path.read_text(encoding="utf-8"),
                    source=path.relative_to(self.root).as_posix(),
                    doc_id=path.stem,
                )
            ]
        return self._chunks

    async def search(self, query: str, k: int = 5) -> list[KnowledgeChunk]:
        """Chunks ranked by how often the query's words occur (doc id and section count too)."""
        wanted = set(_words(query))
        scored = []
        for i, c in enumerate(self._load()):
            words = _words(f"{c.doc_id} {c.section} {c.text}")
            score = sum(words.count(w) for w in wanted)
            if score:
                scored.append((-score, i, c))
        return [c for _, _, c in sorted(scored)[:k]]


def build_searcher(settings: Settings) -> KnowledgeSearcher | LocalKeywordSearcher:
    """Pinecone when `PINECONE_API_KEY` is set, else local keyword search over `knowledge/`."""
    if settings.pinecone_api_key is not None:
        from shipwright.knowledge.ingest import build_vector_store

        return KnowledgeSearcher(build_vector_store(settings))
    return LocalKeywordSearcher(settings.knowledge_dir)
