"""Research tools for the researcher subagent: internal knowledge search and web search."""

from langchain_core.tools import BaseTool, StructuredTool
from langchain_tavily import TavilySearch

from shipwright.config import Settings
from shipwright.knowledge.search import KnowledgeSearcher

NO_RESULTS = "No internal documents matched."


def make_knowledge_search_tool(searcher: KnowledgeSearcher) -> BaseTool:
    async def knowledge_search(query: str, k: int = 5) -> str:
        """Search the target repo's internal specs, ADRs and architecture docs.

        Use this FIRST to find product specs, acceptance criteria, architecture decisions
        and conventions relevant to a ticket. Returns text blocks, each prefixed with a
        citation of the form `[doc_id § section] (source)`. Use web_search instead only
        for external/public library knowledge.

        Args:
            query: Natural-language description of what to look for.
            k: Maximum number of chunks to return.
        """
        chunks = await searcher.search(query, k=k)
        if not chunks:
            return NO_RESULTS
        return "\n\n".join(f"[{c.doc_id} § {c.section}] ({c.source})\n{c.text}" for c in chunks)

    return StructuredTool.from_function(coroutine=knowledge_search, name="knowledge_search")


def make_web_search_tool(settings: Settings) -> BaseTool:
    """Tavily web search for external/public library knowledge (no network on construction)."""
    return TavilySearch(
        name="web_search",
        max_results=5,
        tavily_api_key=settings.tavily_api_key.get_secret_value(),
    )
