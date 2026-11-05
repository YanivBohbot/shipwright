"""knowledge_search formatting and web_search (Tavily or disabled)."""

from shipwright.agent.tools.knowledge_tools import (
    build_knowledge_tool as make_knowledge_search_tool,
)
from shipwright.agent.tools.web_tools import DISABLED, build_web_tool
from shipwright.knowledge.ingest import KnowledgeChunk
from tests.fakes import make_settings


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


async def test_web_search_disabled_without_tavily_key():
    tool = build_web_tool(make_settings())
    assert tool.name == "web_search"
    assert await tool.ainvoke({"query": "fastapi"}) == DISABLED


def test_web_search_uses_tavily_with_a_key():
    tool = build_web_tool(make_settings(tavily_api_key="tvly-fake"))
    assert tool.name == "web_search" and type(tool).__name__ == "TavilySearch"
