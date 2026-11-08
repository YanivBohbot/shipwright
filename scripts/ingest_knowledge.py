"""Create the Pinecone index if needed and ingest the knowledge/ directory (live)."""

import asyncio
import time
from pathlib import Path

from pinecone import Pinecone, ServerlessSpec

from shipwright.core.config import Settings, get_settings
from shipwright.knowledge.ingest import (
    EMBEDDING_DIMENSION,
    build_vector_store,
    ingest_directory,
)
from shipwright.knowledge.search import KnowledgeSearcher

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"


def ensure_index(settings: Settings) -> None:
    if settings.pinecone_api_key is None:
        raise SystemExit("PINECONE_API_KEY is required to create and fill the index.")
    pc = Pinecone(api_key=settings.pinecone_api_key.get_secret_value())
    if pc.has_index(settings.pinecone_index):
        return
    pc.create_index(
        name=settings.pinecone_index,
        dimension=EMBEDDING_DIMENSION,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    while not pc.describe_index(settings.pinecone_index).status["ready"]:
        time.sleep(1)


async def main() -> None:
    settings = get_settings()
    ensure_index(settings)
    store = build_vector_store(settings)
    count = await ingest_directory(KNOWLEDGE_DIR, settings, vector_store=store)
    print(f"Ingested {count} chunks into {settings.pinecone_index}/{settings.pinecone_namespace}")
    for chunk in await KnowledgeSearcher(store).search("CSV export columns", k=3):
        print(f"- {chunk.doc_id} [{chunk.section}]")


if __name__ == "__main__":
    asyncio.run(main())
