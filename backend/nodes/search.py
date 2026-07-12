import asyncio
import httpx

from backend.core.config import (
    API_QUERY_LIMIT,
    SEARCH_QUERY_LIMIT,
    BODY_FETCH_TOP_N,
)
from backend.core.state import HAQQState
from backend.core.text_processing import (
    _extract_keywords,
    _fit,
    _normalise,
)
from backend.search.fetchers import (
    _fetch_duckduckgo,
    _fetch_google_rss,
    _fetch_newsdata,
    _fetch_currents,
    _fetch_gnews,
    fetch_article_body,
)

DDG_TIMEOUT = 4.0  # seconds — DDG tends to be the slowest of the search backends.
                   # On the news path DDG is now a last-resort fallback (only called
                   # when paid APIs + RSS both come back empty), so this budget mostly
                   # protects the historical/scientific path, where DDG still runs in
                   # parallel with RSS on every request.


def extract_keywords_node(state: HAQQState) -> HAQQState:
    keywords = _extract_keywords(state["text"])
    if not keywords:
        return {
            **state,
            "keywords":    None,
            "verdict":     "unverified",
            "confidence":  0.2,
            "explanation": "لا توجد كلمات مفتاحية كافية",
            "sources":     [],
        }

    kw_count = len(keywords.split())
    if kw_count < 2:
        return {
            **state,
            "keywords":    keywords,
            "verdict":     "unverified",
            "confidence":  0.2,
            "explanation": "الكلمات المفتاحية غير كافية للبحث",
            "sources":     [],
        }

    api_query    = _fit(keywords, API_QUERY_LIMIT)
    search_query = _fit(keywords, SEARCH_QUERY_LIMIT)
    print(f"[HAQQ graph] keywords ({kw_count}) → '{api_query[:60]}...'")
    return {**state, "keywords": keywords, "api_query": api_query, "search_query": search_query}


async def _fetch_duckduckgo_bounded(client: httpx.AsyncClient, query: str, lang: str) -> list[dict]:
    """
    Wraps _fetch_duckduckgo with a timeout so a slow DDG response can't drag
    down the whole search_node call. On timeout or any fetch error, returns
    an empty list rather than raising — DDG is a bonus source, not a required one.
    """
    try:
        return await asyncio.wait_for(_fetch_duckduckgo(client, query, lang), timeout=DDG_TIMEOUT)
    except asyncio.TimeoutError:
        print(f"[HAQQ graph] DDG timed out (>{DDG_TIMEOUT}s) — skipping for this request")
        return []
    except Exception as exc:
        print(f"[HAQQ graph] DDG fetch failed: {exc}")
        return []


async def search_node(state: HAQQState) -> HAQQState:
    """
    Routes to different search backends depending on content_type:

    news               → paid APIs (newsdata / currents / gnews) + Google RSS
                         in parallel; DuckDuckGo only as a last-resort fallback
                         if that combined batch returns nothing
    historical_sci     → DuckDuckGo HTML + Google RSS in parallel (no API keys needed,
                         and DDG/RSS both rank encyclopedic sources higher than paid news APIs)

    Efficiency notes:
    • RSS is free and fast enough to always include on the news path, so it
      runs alongside the paid APIs rather than waiting for them to fail first.
    • DDG is the slowest of the four backends, so on the news path it's held
      back as a fallback — only called (and only then time-boxed via
      DDG_TIMEOUT) if paid APIs + RSS together come back empty.
    • For historical/sci we skip the paid news APIs — they rarely index Wikipedia,
      Britannica, or PubMed articles, so they'd just waste quota.
    • All fetchers within a batch run concurrently (asyncio.gather).
    """
    api_q        = state["api_query"]
    search_q     = state["search_query"]
    lang         = state.get("lang", "en")
    content_type = state.get("content_type", "news")

    async with httpx.AsyncClient() as client:

        if content_type == "historical_scientific":
            # ── Historical / scientific path ──────────────────────────────────
            # DuckDuckGo is excellent for encyclopedic content; Google RSS adds
            # journalistic coverage of the same topic.
            print("[HAQQ graph] search path → historical_scientific (DDG + Google RSS)")
            results = await asyncio.gather(
                _fetch_duckduckgo(client, search_q, lang),
                _fetch_google_rss(client, search_q, lang),
            )

        else:
            # ── News path ─────────────────────────────────────────────────────
            # Three paid APIs + Google RSS, all in parallel — RSS is free and
            # fast enough to always include rather than saving it for a fallback.
            # DuckDuckGo is only called afterward, and only if that combined
            # batch comes back completely empty (see DDG_TIMEOUT above).
            print("[HAQQ graph] search path → news (paid APIs + RSS)")
            if lang == "ar":
                results = await asyncio.gather(
                    _fetch_newsdata(client, api_q, "ar"),
                    _fetch_currents(client, api_q, "ar"),
                    _fetch_gnews(client, api_q, "ar"),
                    _fetch_google_rss(client, search_q, "ar"),
                )
            else:
                results = await asyncio.gather(
                    _fetch_newsdata(client, search_q, "en"),
                    _fetch_currents(client, search_q, "en"),
                    _fetch_gnews(client, search_q, "en"),
                    _fetch_google_rss(client, search_q, "en"),
                )

            articles = [a for bucket in results for a in bucket]

            if not articles:
                print("[HAQQ graph] paid APIs + RSS empty → DuckDuckGo fallback")
                articles = await _fetch_duckduckgo_bounded(client, search_q, lang)
                results  = [articles]   # wrap so the dedup below still works

    articles = [a for bucket in results for a in bucket]
    print(f"[HAQQ graph] search total → {len(articles)} articles")

    if not articles:
        return {
            **state,
            "articles":     [],
            "bodies_fetched": False,
            "verdict":      "unverified",
            "confidence":   0.2,
            "explanation":  "لم يُعثر على مصادر مرتبطة بهذا المحتوى",
            "sources":      [],
        }

    return {**state, "articles": articles, "bodies_fetched": False}


async def fetch_bodies_node(state: HAQQState) -> HAQQState:
    """
    NEW NODE — fetch actual article body text for the top BODY_FETCH_TOP_N articles.

    Why this matters
    ────────────────
    The RSS / API snippets that feed into llm_verify_node are often only 1-2
    sentences (or empty). The LLM was forced to judge relevance from the title
    alone, which caused:
      • False CONFIRMEDs — title matches but article is about something else
      • False UNCONFIRMEDs — real confirmation buried in the body

    By fetching ~800 chars of real article text we give the LLM enough signal
    to make an accurate judgment without sending entire pages (token cost).

    Ranking before fetching
    ───────────────────────
    We rank articles by keyword overlap first so we spend the fetch budget on
    the most relevant articles, not just the first 3 returned by the API.

    Failure handling
    ─────────────────
    If a fetch fails (paywall, timeout, 403) we keep the original snippet.
    The node never fails the whole pipeline.
    """
    articles = state["articles"]
    keywords = (state.get("keywords") or "").split()

    def _overlap(a: dict) -> int:
        blob = _normalise(f"{a.get('title','')} {a.get('description','')}")
        return sum(1 for k in keywords if k in blob)

    # Sort by overlap descending; stable sort preserves API order for ties
    ranked = sorted(enumerate(articles), key=lambda x: _overlap(x[1]), reverse=True)

    async with httpx.AsyncClient() as client:
        fetch_tasks = []
        fetch_indices = []

        for original_idx, article in ranked[:BODY_FETCH_TOP_N]:
            url = article.get("link", "")
            if url and url != "#":
                fetch_tasks.append(fetch_article_body(client, url))
                fetch_indices.append(original_idx)

        if fetch_tasks:
            bodies = await asyncio.gather(*fetch_tasks)
            for idx, body in zip(fetch_indices, bodies):
                if body:
                    articles[idx]["body"] = body
                    print(
                        f"[HAQQ graph] body fetched for article[{idx}] "
                        f"({len(body)} chars): {articles[idx].get('link','')[:60]}"
                    )

    return {**state, "articles": articles, "bodies_fetched": True}