import asyncio
import httpx
from backend.search.fetchers import (
    _fetch_duckduckgo,
    _fetch_google_rss,
    _fetch_pubmed,
    _fetch_newsdata,
    _fetch_currents,
    _fetch_gnews,
    fetch_article_body,
)

def _format_results(articles: list[dict]) -> str:
    if not articles:
        return "No results found."
    lines = []
    seen_links = set()
    unique_articles = []
    for a in articles:
        link = a.get('link', '')
        if link and link not in seen_links:
            seen_links.add(link)
            unique_articles.append(a)
    
    for a in unique_articles[:15]:
        title = a.get('title', '')
        desc = a.get('description', '')
        link = a.get('link', '')
        src = a.get('source_name', '') or a.get('source_id', '')
        lines.append(f"Source: {src}\nTitle: {title}\nDescription: {desc}\nURL: {link}\n---")
    return "\n".join(lines)

async def search_duckduckgo_tool(query: str, lang: str = "ar") -> str:
    async with httpx.AsyncClient() as client:
        try:
            results = await asyncio.wait_for(_fetch_duckduckgo(client, query, lang), timeout=10.0)
            return _format_results(results)
        except Exception as e:
            return f"DuckDuckGo search failed: {e}"

async def search_google_rss_tool(query: str, lang: str = "ar") -> str:
    async with httpx.AsyncClient() as client:
        try:
            results = await _fetch_google_rss(client, query, lang)
            return _format_results(results)
        except Exception as e:
            return f"Google RSS search failed: {e}"

async def search_pubmed_tool(query: str, lang: str = "ar") -> str:
    async with httpx.AsyncClient() as client:
        try:
            results = await _fetch_pubmed(client, query, lang)
            return _format_results(results)
        except Exception as e:
            return f"PubMed search failed: {e}"

async def search_news_apis_tool(query: str, lang: str = "ar") -> str:
    async with httpx.AsyncClient() as client:
        try:
            results = await asyncio.gather(
                _fetch_newsdata(client, query, lang),
                _fetch_currents(client, query, lang),
                _fetch_gnews(client, query, lang),
                return_exceptions=True
            )
        except Exception as e:
            return f"News APIs search failed: {e}"
            
    articles = []
    for bucket in results:
        if isinstance(bucket, list):
            articles.extend(bucket)
    return _format_results(articles)

async def fetch_article_body_tool(url: str) -> str:
    async with httpx.AsyncClient() as client:
        try:
            body = await fetch_article_body(client, url)
            if not body:
                return "Could not extract body from this URL (might be paywalled or unsupported)."
            return body
        except Exception as e:
            return f"Failed to fetch article body: {e}"

async def execute_tool(name: str, arguments: dict) -> str:
    if name == "search_duckduckgo_tool":
        return await search_duckduckgo_tool(**arguments)
    elif name == "search_google_rss_tool":
        return await search_google_rss_tool(**arguments)
    elif name == "search_pubmed_tool":
        return await search_pubmed_tool(**arguments)
    elif name == "search_news_apis_tool":
        return await search_news_apis_tool(**arguments)
    elif name == "fetch_article_body_tool":
        return await fetch_article_body_tool(**arguments)
    else:
        return f"Error: Tool {name} not found."

GROQ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_duckduckgo_tool",
            "description": (
                "Search DuckDuckGo for encyclopedic, historical, and scientific facts. "
                "Best for verifying historical events, scientific claims, and general knowledge. "
                "NOT suitable for breaking news — use search_google_rss_tool or search_news_apis_tool instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query in the claim's language."},
                    "lang": {"type": "string", "description": "Language code, default 'ar'"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_google_rss_tool",
            "description": (
                "Search Google News for recent news articles and press coverage. "
                "Best FIRST choice for verifying current news claims, political events, and recent incidents. "
                "Returns headlines and snippets from major news outlets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query in the claim's language."},
                    "lang": {"type": "string", "description": "Language code, default 'ar'"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_pubmed_tool",
            "description": (
                "Search PubMed for peer-reviewed medical and biomedical research papers. "
                "MUST be used as the FIRST tool for any health or medical claim. "
                "Returns academic papers and clinical studies. "
                "Do NOT use for non-medical claims such as politics, sports, or history."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Medical/scientific search query, preferably in English."},
                    "lang": {"type": "string", "description": "Language code, default 'ar'"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_news_apis_tool",
            "description": (
                "Search multiple news databases (NewsData, Currents, GNews) for breaking news and recent reporting. "
                "Use as a SECOND source after search_google_rss_tool for news claims, "
                "or when Google RSS returns insufficient results. "
                "Do NOT use for historical or scientific claims."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "News search query in the claim's language."},
                    "lang": {"type": "string", "description": "Language code, default 'ar'"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_article_body_tool",
            "description": (
                "Fetch the full article text from a URL found in search results. "
                "Use ONLY when you need to read the full content of a promising article "
                "whose snippet is insufficient to verify the claim. "
                "Maximum 2 uses per verification session — choose URLs wisely."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The full URL of the article to fetch."}
                },
                "required": ["url"]
            }
        }
    }
]
