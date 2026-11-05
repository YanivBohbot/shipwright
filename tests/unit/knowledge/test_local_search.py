from pathlib import Path

from shipwright.knowledge.ingest import KnowledgeChunk
from shipwright.knowledge.search import KnowledgeSearcher, LocalKeywordSearcher, build_searcher
from tests.fakes import make_settings

KNOWLEDGE = Path(__file__).resolve().parents[3] / "knowledge"


async def test_local_search_ranks_by_keyword_overlap(tmp_path):
    (tmp_path / "adr").mkdir()
    (tmp_path / "adr" / "ADR-9.md").write_text(
        "# ADR-9\n## Errors\nErrors use problem+json.\n## Logging\nLog in JSON lines.\n",
        encoding="utf-8",
    )
    (tmp_path / "other.md").write_text("# Other\nNothing relevant here.\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("errors errors errors", encoding="utf-8")
    searcher = LocalKeywordSearcher(tmp_path)
    [top, *_] = await searcher.search("How are errors formatted?", k=3)
    assert top == KnowledgeChunk(
        text="Errors use problem+json.",
        source="adr/ADR-9.md",
        doc_id="ADR-9",
        section="ADR-9 > Errors",
    )
    assert await searcher.search("the and of", k=3) == []
    assert len(await searcher.search("json", k=1)) == 1


async def test_local_search_finds_the_real_specs():
    searcher = LocalKeywordSearcher(KNOWLEDGE)
    hits = await searcher.search("EXPORT-CSV-v2 header row", k=3)
    assert hits and hits[0].doc_id == "EXPORT-CSV-v2"
    hits = await searcher.search("rate limiting 429 Retry-After", k=3)
    assert hits[0].doc_id == "ADR-002-rate-limiting"


def test_build_searcher_falls_back_to_local_without_pinecone(tmp_path, monkeypatch):
    s = build_searcher(make_settings(knowledge_dir=tmp_path))
    assert isinstance(s, LocalKeywordSearcher) and s.root == tmp_path

    import shipwright.knowledge.ingest as ingest

    monkeypatch.setattr(ingest, "build_vector_store", lambda settings: "store")
    s = build_searcher(make_settings(pinecone_api_key="pc-fake"))
    assert isinstance(s, KnowledgeSearcher)
