from pathlib import Path

from shipwright.knowledge.ingest import chunk_markdown, ingest_directory, parse_document
from tests.fakes import FakeVectorStore, settings_stub


def test_chunk_markdown_keeps_section_path():
    md = "# Export CSV\n## Acceptance criteria\nAC-1 header row present\n"
    chunks = chunk_markdown(md, source="knowledge/specs/EXPORT-CSV-v2.md", doc_id="EXPORT-CSV-v2")
    assert chunks[-1].section == "Export CSV > Acceptance criteria"
    assert "AC-1" in chunks[-1].text
    assert chunks[-1].doc_id == "EXPORT-CSV-v2"


def test_chunk_markdown_splits_long_sections():
    md = "# Doc\n" + ("word " * 1000)
    chunks = chunk_markdown(md, source="s.md", doc_id="D")
    assert len(chunks) > 1
    assert all(len(c.text) <= 1200 for c in chunks)


async def test_ingest_directory_uses_deterministic_ids(tmp_path):
    (tmp_path / "ADR-001-error-format.md").write_text("# ADR-001\nUse problem+json\n")
    store = FakeVectorStore()
    n = await ingest_directory(tmp_path, settings_stub(), vector_store=store)
    assert n == 1 and store.ids == ["ADR-001-error-format:0"]
    assert store.metadatas[0]["doc_id"] == "ADR-001-error-format"
    assert store.metadatas[0]["section"] == "ADR-001"


async def test_ingest_directory_recurses_and_ignores_other_files(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "A.md").write_text("# A\nbody\n")
    (tmp_path / "notes.txt").write_text("ignored")
    store = FakeVectorStore()
    assert await ingest_directory(tmp_path, settings_stub(), vector_store=store) == 1


async def test_parse_document_reads_markdown_verbatim(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("# T\nhello\n", encoding="utf-8")
    assert await parse_document(p, settings_stub()) == "# T\nhello\n"


async def test_ingest_prefers_pdf_over_md_with_same_stem(tmp_path, monkeypatch):
    (tmp_path / "S.md").write_text("# S\nfrom md\n")
    (tmp_path / "S.pdf").write_bytes(b"%PDF-fake")

    async def fake_parse(path, settings):
        return "# S\nfrom pdf\n" if path.suffix == ".pdf" else "# S\nfrom md\n"

    monkeypatch.setattr("shipwright.knowledge.ingest.parse_document", fake_parse)
    store = FakeVectorStore()
    assert await ingest_directory(tmp_path, settings_stub(), vector_store=store) == 1
    assert store.texts == ["from pdf"]
