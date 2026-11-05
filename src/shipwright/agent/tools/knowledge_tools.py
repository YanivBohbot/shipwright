"""`knowledge_search`: the researcher's tool over internal specs and ADRs.

Deep Agents concept: a **custom tool** given to one subagent only. The searcher is Pinecone
(semantic) or the local keyword fallback; both expose `search(query, k)` (`knowledge/search.py`).
"""

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

NO_RESULTS = "No internal documents matched."


def build_knowledge_tool(searcher: Any) -> BaseTool:
    async def knowledge_search(query: str, k: int = 5) -> str:
        """Search the target repo's internal specs, ADRs and architecture docs.

        Use this FIRST for product specs, acceptance criteria, architecture decisions and
        conventions. Each result starts with a citation `[doc_id § section] (source)`. Use
        web_search only for external/public library knowledge.

        Args:
            query: Natural-language description of what to look for (or a spec id).
            k: Maximum number of chunks to return.
        """
        chunks = await searcher.search(query, k=k)
        if not chunks:
            return NO_RESULTS
        return "\n\n".join(f"[{c.doc_id} § {c.section}] ({c.source})\n{c.text}" for c in chunks)

    return StructuredTool.from_function(coroutine=knowledge_search, name="knowledge_search")
