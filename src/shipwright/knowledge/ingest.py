"""Knowledge ingestion: parse docs (Markdown, PDF via LlamaParse), chunk by heading, index.

Used by `scripts/ingest_knowledge.py` to fill Pinecone; `chunk_markdown` is also used by the
local keyword search.
"""

from dataclasses import dataclass
from pathlib import Path

from langchain_core.vectorstores import VectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from shipwright.core.config import Settings

SUPPORTED_SUFFIXES = {".md", ".pdf"}
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536

_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]


@dataclass(frozen=True)
class KnowledgeChunk:
    text: str
    source: str
    doc_id: str
    section: str


def chunk_markdown(markdown: str, *, source: str, doc_id: str) -> list[KnowledgeChunk]:
    """Split by h1-h3 headings, then by size. `section` is the heading path joined by ' > '."""
    header_splitter = MarkdownHeaderTextSplitter(_HEADERS, strip_headers=True)
    size_splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=150)
    chunks: list[KnowledgeChunk] = []
    for section_doc in header_splitter.split_text(markdown):
        section = " > ".join(
            section_doc.metadata[key] for _, key in _HEADERS if key in section_doc.metadata
        )
        for text in size_splitter.split_text(section_doc.page_content):
            if text.strip():
                chunks.append(
                    KnowledgeChunk(text=text, source=source, doc_id=doc_id, section=section)
                )
    return chunks


async def parse_document(path: Path, settings: Settings) -> str:
    """Return Markdown for a file: `.md` is read as-is, `.pdf` is parsed with LlamaParse."""
    suffix = path.suffix.lower()
    if suffix == ".md":
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        from llama_cloud_services import LlamaParse

        if settings.llama_cloud_api_key is None:
            raise ValueError(f"LLAMA_CLOUD_API_KEY is required to parse {path}")
        parser = LlamaParse(
            api_key=settings.llama_cloud_api_key.get_secret_value(),
            result_type="markdown",
        )
        documents = await parser.aload_data(path)
        return "\n\n".join(doc.text for doc in documents)
    raise ValueError(f"Unsupported knowledge file type: {path}")


async def ingest_directory(root: Path, settings: Settings, *, vector_store: VectorStore) -> int:
    """Ingest every .md/.pdf under `root`. Returns the number of chunks indexed.

    Ids are `{doc_id}:{i}` so re-ingesting a document overwrites its chunks (idempotent).
    When `X.md` and `X.pdf` coexist, the PDF wins if LLAMA_CLOUD_API_KEY is set, else the Markdown.
    """
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        has_pdf = path.with_suffix(".pdf").exists()
        if path.suffix.lower() == ".md" and has_pdf and settings.llama_cloud_api_key is not None:
            continue  # the PDF rendering of the same doc wins (same doc_id would collide)
        if path.suffix.lower() == ".pdf" and settings.llama_cloud_api_key is None:
            continue  # no LlamaParse key: the Markdown twin (if any) is ingested instead
        doc_id = path.stem
        markdown = await parse_document(path, settings)
        chunks = chunk_markdown(markdown, source=path.as_posix(), doc_id=doc_id)
        if not chunks:
            continue
        await vector_store.aadd_texts(
            [c.text for c in chunks],
            metadatas=[
                {"source": c.source, "doc_id": c.doc_id, "section": c.section} for c in chunks
            ],
            ids=[f"{doc_id}:{i}" for i in range(len(chunks))],
        )
        total += len(chunks)
    return total


def build_vector_store(settings: Settings) -> PineconeVectorStore:
    if settings.pinecone_api_key is None:
        raise ValueError("PINECONE_API_KEY is required for the Pinecone vector store")
    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=settings.openai_api_key)
    return PineconeVectorStore(
        index_name=settings.pinecone_index,
        embedding=embeddings,
        namespace=settings.pinecone_namespace,
        pinecone_api_key=settings.pinecone_api_key.get_secret_value(),
    )
