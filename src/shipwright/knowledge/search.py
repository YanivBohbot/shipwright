"""Semantic search over the knowledge base."""

from langchain_core.vectorstores import VectorStore

from shipwright.knowledge.ingest import KnowledgeChunk


class KnowledgeSearcher:
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
