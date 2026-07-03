import asyncio
import re
from urllib.parse import urlparse
import httpx

from backend.core.config import (
    NEWSDATA_KEY,
    CURRENTS_KEY,
    GNEWS_KEY,
    BODY_CHARS_PER_ARTICLE,
)
from backend.core.text_processing import (
    _html_decode,
    _parse_rss,
    _normalise,
)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HAQQBot/2.0)"}


async def _get_json(client: httpx.AsyncClient, url: str, **params) -> dict:
    try:
        r = await client.get(url, params=params, timeout=8.0)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"[HAQQ] HTTP error {url}: {exc}")
        return {}


async def _fetch_newsdata(client: httpx.AsyncClient, query: str, lang: str) -> list[dict]:
    data = await _get_json(
        client, "https://newsdata.io/api/1/news",
        apikey=NEWSDATA_KEY, q=query, language=lang, size=10,
    )
    if data.get("status") == "error":
        print("[HAQQ] NewsData error:", data.get("message", ""))
        return []
    articles = data.get("results") or []
    for a in articles:
        a.setdefault("source_name", a.get("source_id", ""))
        a.setdefault("link", a.get("link", "#"))
        a["_api"] = "newsdata"
    return articles


async def _fetch_currents(client: httpx.AsyncClient, query: str, lang: str) -> list[dict]:
    data = await _get_json(
        client, "https://api.currentsapi.services/v1/search",
        apiKey=CURRENTS_KEY, keywords=query, language=lang, limit=10,
    )
    return [
        {
            "title":       a.get("title", ""),
            "description": a.get("description", ""),
            "link":        a.get("url", "#"),
            "source_id":   a.get("author", ""),
            "source_name": a.get("author", ""),
            "_api":        "currents",
        }
        for a in (data.get("news") or [])
    ]


async def _fetch_gnews(client: httpx.AsyncClient, query: str, lang: str) -> list[dict]:
    data = await _get_json(
        client, "https://gnews.io/api/v4/search",
        token=GNEWS_KEY, q=query, lang=lang, max=10,
    )
    return [
        {
            "title":       a.get("title", ""),
            "description": a.get("description", ""),
            "link":        a.get("url", "#"),
            "source_id":   (a.get("source") or {}).get("name", "").lower(),
            "source_name": (a.get("source") or {}).get("name", ""),
            "_api":        "gnews",
        }
        for a in (data.get("articles") or [])
    ]


async def _fetch_google_rss(client: httpx.AsyncClient, query: str, lang: str) -> list[dict]:
    """Google News RSS — no key required."""
    params: dict[str, str] = {"q": query[:200]}
    if lang == "ar":
        params.update(hl="ar", gl="EG", ceid="EG:ar")
    else:
        params.update(hl="en-US", gl="US", ceid="US:en")

    try:
        r = await client.get(
            "https://news.google.com/rss/search",
            params=params, timeout=8.0, headers=_HEADERS,
        )
        r.raise_for_status()
        return _parse_rss(r.text)
    except Exception as exc:
        print(f"[HAQQ] Google RSS error: {exc}")
        return []


async def _fetch_duckduckgo(client: httpx.AsyncClient, query: str, lang: str) -> list[dict]:
    """
    DuckDuckGo HTML search — no API key, no rate limit (for moderate use).
    Parses the result links from the HTML response.

    Best for: encyclopedic, historical, and scientific queries because
    DDG's ranking heavily favours Wikipedia, Britannica, NIH, NASA, etc.

    NOTE: DDG blocks obvious bot UA strings. Use a realistic browser UA.
    If DDG starts returning CAPTCHAs, add a small asyncio.sleep(1) before
    the call or rotate the User-Agent.
    """
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ar,en;q=0.9" if lang == "ar" else "en-US,en;q=0.9",
    }
    try:
        r = await client.post(
            url,
            data={"q": query[:200], "kl": "ar-ar" if lang == "ar" else "us-en"},
            headers=headers,
            timeout=10.0,
        )
        r.raise_for_status()
        return _parse_ddg_html(r.text)
    except Exception as exc:
        print(f"[HAQQ] DuckDuckGo error: {exc}")
        return []


def _parse_ddg_html(html: str) -> list[dict]:
    """
    Extract result titles, URLs, and snippets from DDG HTML results page.
    DDG HTML structure (stable since 2020):
      <div class="result">
        <a class="result__a" href="...">Title</a>
        <a class="result__snippet">Snippet text</a>
      </div>
    """
    results: list[dict] = []

    # Extract each result block
    for block in re.finditer(
        r'<div[^>]+class="[^"]*result[^"]*"[^>]*>([\s\S]*?)</div>\s*</div>',
        html,
    ):
        text = block.group(1)

        # Title + URL
        link_m = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', text)
        if not link_m:
            continue
        raw_url = link_m.group(1)
        title   = re.sub(r"<[^>]+>", "", link_m.group(2)).strip()

        # DDG wraps URLs in a redirect — extract the actual URL
        uddg_m = re.search(r"uddg=([^&]+)", raw_url)
        if uddg_m:
            from urllib.parse import unquote
            real_url = unquote(uddg_m.group(1))
        else:
            real_url = raw_url

        # Snippet
        snip_m   = re.search(r'class="result__snippet"[^>]*>([\s\S]*?)</a>', text)
        snippet  = re.sub(r"<[^>]+>", "", snip_m.group(1)).strip() if snip_m else ""

        # Source host
        try:
            host = urlparse(real_url).netloc.lstrip("www.")
        except Exception:
            host = ""

        results.append({
            "title":       _html_decode(title),
            "description": _html_decode(snippet),
            "link":        real_url,
            "source_id":   host,
            "source_name": host,
            "_api":        "duckduckgo",
        })
        if len(results) >= 10:
            break

    print(f"[HAQQ] DuckDuckGo → {len(results)} results")
    return results


async def fetch_article_body(client: httpx.AsyncClient, url: str) -> str:
    """
    Fetch the real article page and extract clean body text (up to BODY_CHARS_PER_ARTICLE chars).

    Strategy:
      1. Download raw HTML (first 30 KB — enough for lede + first few paragraphs)
      2. Remove script/style/nav/header/footer/aside blocks entirely
      3. Strip remaining tags
      4. Skip short lines (boilerplate) and grab the first meaningful paragraphs

    Why 30 KB? Full pages are 200-500 KB but the article body always leads.
    Fetching more wastes bandwidth and adds latency.

    Returns "" on any error so callers can fall back to the snippet.
    """
    if not url or url == "#" or "google.com" in url:
        return ""

    try:
        r = await client.get(
            url,
            timeout=6.0,
            follow_redirects=True,
            headers=_HEADERS,
        )
        html = r.text[:30_000]   # ~30 KB is plenty for the lede

        # Remove entire noisy blocks first
        for tag in ("script", "style", "nav", "header", "footer", "aside", "iframe"):
            html = re.sub(rf"<{tag}[\s>][\s\S]*?</{tag}>", " ", html, flags=re.IGNORECASE)

        # Strip remaining tags
        text = re.sub(r"<[^>]+>", " ", html)
        text = _html_decode(text)
        text = re.sub(r"\s+", " ", text).strip()

        # Keep only lines / sentences that look like real content (>50 chars)
        sentences = [s.strip() for s in re.split(r"[.!?\n]", text) if len(s.strip()) > 50]
        body = ". ".join(sentences[:10])[:BODY_CHARS_PER_ARTICLE]
        return body

    except Exception as exc:
        print(f"[HAQQ] body fetch failed {url[:60]}: {exc}")
        return ""
