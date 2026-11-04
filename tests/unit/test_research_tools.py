from shipwright.knowledge.ingest import KnowledgeChunk
from shipwright.tools.research import make_knowledge_search_tool, make_web_search_tool
from tests.fakes import settings_stub


class FakeSearcher:
    def __init__(self, chunks: list[KnowledgeChunk]):
        self._chunks = chunks
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, k: int = 5) -> list[KnowledgeChunk]:
        self.calls.append((query, k))
        return self._chunks


async def test_knowledge_search_formats_citations():
    chunk = KnowledgeChunk(
        text="Export must stream rows.",
        source="specs/export-csv.md",
        doc_id="EXPORT-CSV-v2",
        section="Acceptance criteria",
    )
    searcher = FakeSearcher([chunk])
    tool = make_knowledge_search_tool(searcher)  # type: ignore[arg-type]
    out = await tool.ainvoke({"query": "csv export", "k": 3})
    assert "[EXPORT-CSV-v2 § Acceptance criteria] (specs/export-csv.md)" in out
    assert "Export must stream rows." in out
    assert searcher.calls == [("csv export", 3)]


async def test_knowledge_search_empty():
    tool = make_knowledge_search_tool(FakeSearcher([]))  # type: ignore[arg-type]
    assert await tool.ainvoke({"query": "nothing"}) == "No internal documents matched."


def test_web_search_tool_name():
    tool = make_web_search_tool(settings_stub())
    assert tool.name == "web_search"
