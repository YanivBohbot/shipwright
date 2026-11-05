"""`web_search`: Tavily when `TAVILY_API_KEY` is set, otherwise a stub that says it is disabled.

Deep Agents concept: a prebuilt LangChain **tool** (`TavilySearch`) given to one subagent. The
stub keeps the tool name stable, so the researcher's prompt is the same in both cases.
"""

from langchain_core.tools import BaseTool, StructuredTool

from shipwright.core.config import Settings

DISABLED = "Web search is disabled (no TAVILY_API_KEY). Rely on internal knowledge only."


def _disabled_web_search(query: str) -> str:
    """Search the web for external/public library knowledge (disabled in this setup).

    Args:
        query: What to search for.
    """
    return DISABLED


def build_web_tool(settings: Settings) -> BaseTool:
    if settings.tavily_api_key is None:
        return StructuredTool.from_function(func=_disabled_web_search, name="web_search")
    from langchain_tavily import TavilySearch

    key = settings.tavily_api_key.get_secret_value()
    return TavilySearch(name="web_search", max_results=5, tavily_api_key=key)
