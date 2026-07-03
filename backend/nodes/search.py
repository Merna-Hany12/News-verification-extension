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


async def search_node(state: HAQQState) -> HAQQState:
    """
    Routes to different search backends depending on content_type:

    news               → paid APIs (newsdata / currents / gnews) in parallel
                         Google RSS fallback if all return empty
    historical_sci     → DuckDuckGo HTML + Google RSS in parallel (no API keys needed,
                         and DDG/RSS both rank encyclopedic sources higher than paid news APIs)

    Efficiency notes:
    • For news we don't call DDG — it's slower and less current than the paid APIs.
    • For historical/sci we skip the paid news APIs — they rarely index Wikipedia,
      Britannica, or PubMed articles, so they'd just waste quota.
    • Both paths run their fetchers concurrently (asyncio.gather).
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
            # Three paid APIs in parallel; Google RSS only if they all fail.
            print("[HAQQ graph] search path → news (paid APIs)")
            if lang == "ar":
                results = await asyncio.gather(
                    _fetch_newsdata(client, api_q, "ar"),
                    _fetch_currents(client, api_q, "ar"),
                    _fetch_gnews(client, api_q, "ar"),
                )
            else:
                results = await asyncio.gather(
                    _fetch_newsdata(client, search_q, "en"),
                    _fetch_currents(client, search_q, "en"),
                    _fetch_gnews(client, search_q, "en"),
                )

            articles = [a for bucket in results for a in bucket]

            if not articles:
                print("[HAQQ graph] paid APIs empty → Google RSS fallback")
                articles = await _fetch_google_rss(client, search_q, lang)
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
