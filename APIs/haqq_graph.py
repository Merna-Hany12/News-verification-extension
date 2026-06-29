"""
HAQQ — LangGraph Verification Pipeline  (v2)
=============================================

Changes from v1
---------------
1. THREE-CLASS CLASSIFIER
   classify_node now returns one of:
     • "news"                 → existing path (NewsData / Currents / GNews + RSS fallback)
     • "historical_scientific" → DuckDuckGo HTML + Google RSS (no API keys needed)
     • "non_news"             → exit immediately

2. EFFICIENT SEARCH ROUTING
   Each content type hits only the sources that make sense for it:
     news               → paid APIs first, Google RSS only as fallback
     historical/science → DuckDuckGo (best at encyclopedic content) + Google RSS in parallel

3. LLM READS ACTUAL URL BODIES (top 3 sources)
   Before calling Groq, fetch_article_body() scrapes the real article page
   and passes up to 800 chars of clean body text per article.
   This replaces the 200-char RSS snippet that was causing CONFIRMED hallucinations.

Graph flow
----------
classify_node
    │ non_news          → non_news_exit → END
    │ news              → extract_keywords_node
    │ historical_sci    → extract_keywords_node   (same kw extraction, different search)
    ▼
extract_keywords_node
    │ too few keywords  → END (unverified)
    ▼
search_node            (branches internally on content_type)
    │ news              → newsdata / currents / gnews  [+ google rss fallback]
    │ historical_sci    → duckduckgo + google rss in parallel
    │ no articles       → END (unverified)
    ▼
fetch_bodies_node      (NEW) fetches real HTML body for top-3 articles
    ▼
llm_verify_node        (Groq / Llama — now sees full body text, not just snippets)
    ▼
score_node             (trusted-source count + LLM verdict → final verdict)
    ▼
END
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

import httpx
from groq import AsyncGroq
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

# ─── CONFIG ──────────────────────────────────────────────────────────────────
NEWSDATA_KEY = os.environ.get("NEWSDATA_API_KEY", "")
CURRENTS_KEY = os.environ.get("CURRENTS_API_KEY", "")
GNEWS_KEY    = os.environ.get("GNEWS_API_KEY",    "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY",     "")
GROQ_MODEL   = os.environ.get("GROQ_MODEL",       "llama-3.3-70b-versatile")

# How many chars of article body to pass to the LLM per article
BODY_CHARS_PER_ARTICLE = 800
# How many articles to actually fetch the body for (costs time)
BODY_FETCH_TOP_N = 3

# ─── TRUSTED SOURCES ─────────────────────────────────────────────────────────
TRUSTED: set[str] = {
    # International
    "bbc", "reuters", "ap", "apnews", "associated press",
    "aljazeera", "al jazeera", "cnn", "nytimes", "theguardian",
    "france24", "dw", "euronews", "skynews", "sky news", "afp",
    "washingtonpost", "wsj", "bloomberg", "time", "newsweek",
    "nbcnews", "cbsnews", "abcnews", "usatoday", "latimes",
    "independent", "telegraph", "ft", "middleeasteye",
    # Scientific / encyclopedic (relevant for historical_sci class)
    "wikipedia", "britannica", "nature", "sciencedirect", "pubmed",
    "ncbi", "nih", "who", "nasa", "arxiv", "scholar",
    # Arabic
    "الجزيرة", "رويترز", "العربية", "alarabiya", "france24arabic",
    "aawsat", "asharqalawsat", "alhurra",
    # Egyptian
    "ahram", "alahram", "youm7", "masrawy", "elwatannews",
    "almasryalyoum", "shorouk", "elshorouk", "vetogate",
    "filbalad", "mobtada", "dotmsr", "elbashayer", "cairo24",
}

# ─── STOP WORDS ──────────────────────────────────────────────────────────────
STOPS_AR: set[str] = {
    "في","من","على","إلى","عن","مع","هذا","هذه","ذلك","تلك",
    "التي","الذي","وهو","وهي","كان","كانت","أن","إن","لكن",
    "كما","حيث","بعد","قبل","عند","حتى","هل","لا","نعم","كل",
    "بين","غير","عبر","خلال","حول","ضد","أو","ثم","لم","لن",
    "قد","فقد","وقد","منذ","إذا","إذ","بما","مما","فمن","وفي",
    "وعلى","ومع","وإن","أما","بل","فإن","ولا","وهذا","وهذه",
    "هناك","هنا","أيضا","ايضا","لذلك","لذا","عندما","كذلك",
    "سوف","لقد","إلا","سوى","معه","معها","منه","منها",
    "إليه","إليها","عليه","عليها","فيه","فيها","لهذا","لهذه",
    "قال","قالت","وقال","وقالت","أضاف","أضافت","وأضاف","وأضافت",
    "أعلن","أعلنت","وأعلن","وأعلنت","ذكر","ذكرت","وذكر","وذكرت",
    "أكد","أكدت","وأكد","وأكدت","أشار","أشارت","وأشار","وأشارت",
    "بحسب","وفقا","وفقاً","حسب","تابع","تابعت","أوضح","أوضحت",
    "عاجل","خبر عاجل","عاجل الآن",
}

STOPS_EN: set[str] = {
    "this","that","these","those","with","from","have","has","had",
    "been","were","also","into","over","under","more","most",
    "some","such","than","then","when","where","which","what","while",
    "during","after","before","about","because","through","among",
    "between","against","without","within","upon","said","says",
    "according","reported","reportedly","officials","statement",
    "including","their","there","here","will","would","could","should",
    "they","them","your","just","like","make","made","being","still",
    "only","very","much","many","each","both","other","another",
    "first","last","year","years","time","news",
    "breaking","breakingnews",
}

API_QUERY_LIMIT    = 95
SEARCH_QUERY_LIMIT = 200

# Labels for the three-class zero-shot classifier
# Keep these descriptive enough that mDeBERTa can separate them well.
CLASSIFY_LABELS = [
    # class 0 — news
    "breaking news report journalism media coverage current event announcement politics",
    # class 1 — historical / scientific
    "historical fact scientific discovery research study academic ancient history science",
    # class 2 — non-news
    "personal opinion joke meme social media post casual conversation gossip advertisement",
]

# Map label index → internal content_type string
LABEL_TO_TYPE = {
    0: "news",
    1: "historical_scientific",
    2: "non_news",
}

# ─── GRAPH STATE ─────────────────────────────────────────────────────────────
class HAQQState(TypedDict):
    # inputs
    text:           str
    lang:           str

    # set by classify_node
    content_type:   Optional[str]   # "news" | "historical_scientific" | "non_news"
    is_news:        Optional[bool]  # kept for backward compat with /classify endpoint
    news_score:     float
    non_news_score: float

    # set by extract_keywords_node
    keywords:       Optional[str]
    api_query:      Optional[str]
    search_query:   Optional[str]

    # set by search_node
    articles:       list[dict[str, Any]]

    # set by fetch_bodies_node
    # articles are mutated in-place: article["body"] is added
    bodies_fetched: bool

    # set by llm_verify_node
    llm_verdict:    Optional[str]   # CONFIRMED | UNCONFIRMED | CONTRADICTED
    llm_reasoning:  Optional[str]

    # set by score_node / early exits
    verdict:        Optional[str]   # fact | unverified | fake | non_news
    confidence:     float
    explanation:    str
    sources:        list[dict[str, str]]


# ─── TEXT HELPERS ─────────────────────────────────────────────────────────────
def _is_arabic(w: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", w))


def _normalise(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[\u064B-\u065F\u0670\u0671\u0640]", "", text)
    text = re.sub(r"[آأإٱ]", "ا", text)
    text = text.replace("ة", "ه").replace("ى", "ي")
    text = re.sub(r"[^\u0600-\u06FFa-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_keywords(text: str) -> Optional[str]:
    tokens = _normalise(text).split()
    seen: set[str] = set()
    words: list[str] = []
    for w in tokens:
        min_len = 2 if _is_arabic(w) else 4
        if len(w) >= min_len and w not in STOPS_AR and w not in STOPS_EN and w not in seen:
            seen.add(w)
            words.append(w)
    return " ".join(words) or None


def _fit(keywords: str, limit: int) -> str:
    if len(keywords) <= limit:
        return keywords
    truncated = keywords[:limit]
    return re.sub(r"\s\S*$", "", truncated).strip()


def _is_trusted(source_id: str, source_name: str) -> bool:
    sid  = source_id.lower()
    snam = source_name.lower()
    return any(t in sid or t in snam for t in TRUSTED)


def _make_result(verdict: str, confidence: float, explanation: str, sources: list[dict]) -> dict:
    return {"verdict": verdict, "confidence": confidence, "explanation": explanation, "sources": sources}


# ─── HTTP HELPERS ─────────────────────────────────────────────────────────────
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HAQQBot/2.0)"}


async def _get_json(client: httpx.AsyncClient, url: str, **params) -> dict:
    try:
        r = await client.get(url, params=params, timeout=8.0)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"[HAQQ] HTTP error {url}: {exc}")
        return {}


# ─── SOURCE FETCHERS ─────────────────────────────────────────────────────────

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


def _html_decode(s: str) -> str:
    return (
        s.replace("&amp;", "&").replace("&lt;", "<")
         .replace("&gt;", ">").replace("&quot;", '"')
         .replace("&#39;", "'").replace("&nbsp;", " ").strip()
    )


# ─── ARTICLE BODY FETCHER ─────────────────────────────────────────────────────

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


# ─── RSS HELPERS ─────────────────────────────────────────────────────────────

def _parse_rss(xml: str) -> list[dict]:
    results: list[dict] = []
    for m in re.finditer(r"<item>([\s\S]*?)</item>", xml):
        block    = m.group(1)
        title    = _xml_tag(block, "title")
        link     = _xml_tag(block, "link")
        desc     = _xml_tag(block, "description")
        if not title or not link:
            continue

        src_m    = re.search(r'<source[^>]*url="([^"]*)"[^>]*>([\s\S]*?)</source>', block)
        src_name = _html_decode(_strip_cdata(src_m.group(2))).strip() if src_m else ""
        src_url  = src_m.group(1) if src_m else ""
        src_host = ""
        if src_url:
            try:
                src_host = urlparse(src_url).netloc.lstrip("www.")
            except Exception:
                pass

        raw_link  = _html_decode(_strip_cdata(link))
        real_link = src_url if src_url else raw_link

        results.append({
            "title":       _html_decode(_strip_cdata(title)),
            "description": _html_decode(re.sub(r"<[^>]+>", "", _strip_cdata(desc))),
            "link":        real_link,
            "source_id":   src_host or src_name.lower(),
            "source_name": src_name,
            "_api":        "googlenews_rss",
        })
        if len(results) >= 10:
            break
    return results


def _xml_tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([\s\S]*?)</{tag}>", block, re.IGNORECASE)
    return m.group(1) if m else ""


def _strip_cdata(s: str) -> str:
    return re.sub(r"^<!\[CDATA\[", "", s).replace("]]>", "").strip()


# ─── NODES ────────────────────────────────────────────────────────────────────

def classify_node(state: HAQQState) -> HAQQState:
    """
    Three-class zero-shot classification:
      • news
      • historical_scientific
      • non_news

    The key change from v1: we now pick the BEST label by score and map it
    to content_type. The routing logic in _after_classify sends each type
    down a different search path.
    """
    text = (state.get("text") or "").strip()
    if len(text) < 20:
        return {
            **state,
            "content_type":  "non_news",
            "is_news":       False,
            "verdict":       "unverified",
            "confidence":    0.0,
            "explanation":   "النص قصير جداً للتحقق.",
            "sources":       [],
        }

    try:
        from news_claim import classifier  # type: ignore[import]

        out     = classifier(text[:500], CLASSIFY_LABELS)
        best_label_text = out["labels"][0]   # highest-scored label string
        best_score      = float(out["scores"][0])

        # Map the label string back to an index
        label_index = CLASSIFY_LABELS.index(best_label_text)
        content_type = LABEL_TO_TYPE[label_index]

        # Backward-compat: is_news stays True for both news and historical_sci
        is_news = content_type in ("news", "historical_scientific")

        # Collect all scores for logging
        scores = dict(zip(out["labels"], out["scores"]))
        news_score     = float(scores.get(CLASSIFY_LABELS[0], 0.0))
        non_news_score = float(scores.get(CLASSIFY_LABELS[2], 0.0))

        # Ambiguity guard: if top score < 0.45, treat as news to avoid false negatives
        if best_score < 0.45:
            content_type = "news"
            is_news      = True

        print(
            f"[HAQQ graph] classify → {content_type} "
            f"(score={best_score:.3f}) "
            f"news={news_score:.3f} non_news={non_news_score:.3f}"
        )

        return {
            **state,
            "content_type":  content_type,
            "is_news":       is_news,
            "news_score":    news_score,
            "non_news_score": non_news_score,
        }

    except Exception as exc:
        print(f"[HAQQ graph] classify error — failing open: {exc}")
        return {
            **state,
            "content_type":  "news",
            "is_news":       True,
            "news_score":    0.5,
            "non_news_score": 0.5,
        }


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


async def llm_verify_node(state: HAQQState) -> HAQQState:
    """
    Sends claim + enriched article content to Groq/Llama.

    Content priority per article (best available wins):
      1. article["body"]        ← real fetched body text (fetch_bodies_node)
      2. article["description"] ← API/RSS snippet
      3. article["title"]       ← last resort

    The system prompt is adjusted for content_type:
      • news              → focus on event confirmation, recency
      • historical_sci    → focus on factual accuracy, scientific consensus
    """
    claim        = state["text"]
    articles     = state["articles"]
    lang         = state.get("lang", "ar")
    content_type = state.get("content_type", "news")
    keywords     = (state.get("keywords") or "").split()

    def _overlap(a: dict) -> int:
        blob = _normalise(f"{a.get('title','')} {a.get('description','')} {a.get('body','')}")
        return sum(1 for k in keywords if k in blob)

    ranked = sorted(articles, key=_overlap, reverse=True)

    # ── Relevance gate ────────────────────────────────────────────────────────
    if ranked and _overlap(ranked[0]) == 0:
        print("[HAQQ graph] LLM skipped — no article overlaps with keywords")
        return {
            **state,
            "llm_verdict":   "UNCONFIRMED",
            "llm_reasoning": "لا تتناول المصادر المتاحة موضوع الادعاء",
        }

    # ── Build snippets ────────────────────────────────────────────────────────
    snippets = []
    for i, a in enumerate(ranked[:6], 1):
        title   = (a.get("title") or "").strip()
        # Prefer real body, fall back to API snippet, then title
        content = (a.get("body") or a.get("description") or "")[:BODY_CHARS_PER_ARTICLE].strip()
        src     = a.get("source_name") or a.get("source_id") or "مصدر غير معروف"
        url     = a.get("link", "")
        has_body = bool(a.get("body"))

        snippet_lines = [f"[{i}] ({src}) {'[full body]' if has_body else '[snippet]'}"]
        if url:
            snippet_lines.append(f"URL: {url}")
        snippet_lines.append(f"العنوان: {title}")
        if content:
            snippet_lines.append(f"المحتوى: {content}")
        snippets.append("\n".join(snippet_lines))

    snippets_text = "\n\n".join(snippets)

    # ── System prompt — tuned per content type ────────────────────────────────
    if content_type == "historical_scientific":
        system_prompt = (
            "أنت محقق حقائق علمية وتاريخية. مهمتك تقييم ما إذا كانت المصادر المتاحة "
            "تؤكد الادعاء العلمي أو التاريخي أم لا.\n\n"
            "أجب بهذا الشكل الحرفي فقط — سطران:\n"
            "السطر الأول: كلمة واحدة: CONFIRMED أو UNCONFIRMED أو CONTRADICTED\n"
            "السطر الثاني: جملة عربية واحدة تصف ما تقوله المصادر عن هذا الموضوع.\n\n"
            "تعريفات:\n"
            "- CONFIRMED: مصدران أو أكثر موثوقان (ويكيبيديا، بريتانيكا، NIH، NASA، إلخ) "
            "يؤكدان المعلومة بتفاصيل متطابقة.\n"
            "- CONTRADICTED: مصدر موثوق ينفي المعلومة صراحةً أو يصححها.\n"
            "- UNCONFIRMED: أي حالة أخرى.\n\n"
            "مهم: السطر الثاني يصف ما تقوله المصادر فعلاً، ليس سبب حكمك."
        )
    else:
        system_prompt = (
            "أنت محقق أخبار محترف. مهمتك تقييم ما إذا كانت المقالات تؤكد الادعاء أم لا.\n\n"
            "أجب بهذا الشكل الحرفي فقط — سطران:\n"
            "السطر الأول: كلمة واحدة: CONFIRMED أو UNCONFIRMED أو CONTRADICTED\n"
            "السطر الثاني: جملة عربية واحدة تصف ما تقوله المصادر عن هذا الموضوع.\n\n"
            "تعريفات:\n"
            "- CONFIRMED: مقالتان أو أكثر تتناول نفس الحدث بتفاصيل متطابقة.\n"
            "- CONTRADICTED: مصدر موثوق ينفي الادعاء صراحةً.\n"
            "- UNCONFIRMED: أي حالة أخرى.\n\n"
            "مهم جداً: السطر الثاني يصف ما تقوله المصادر فعلاً، وليس سبب حكمك."
        )

    user_prompt = (
        f"الادعاء:\n{claim[:600]}\n\n"
        f"المصادر:\n{snippets_text}"
    )

    try:
        groq  = AsyncGroq(api_key=GROQ_API_KEY)
        resp  = await groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=200,
            temperature=0.1,
        )
        raw       = resp.choices[0].message.content.strip()
        lines     = [l.strip() for l in raw.splitlines() if l.strip()]
        llm_label = lines[0].upper() if lines else "UNCONFIRMED"
        summary   = lines[1] if len(lines) > 1 else ""

        for tag in ("CONFIRMED", "CONTRADICTED", "UNCONFIRMED"):
            if tag in llm_label:
                llm_label = tag
                break
        else:
            llm_label = "UNCONFIRMED"

        print(f"[HAQQ graph] LLM → {llm_label} | {summary[:80]}")
        return {**state, "llm_verdict": llm_label, "llm_reasoning": summary}

    except Exception as exc:
        print(f"[HAQQ graph] LLM error: {exc}")
        return {**state, "llm_verdict": "UNCONFIRMED", "llm_reasoning": ""}


def score_node(state: HAQQState) -> HAQQState:
    """
    Decision table (same logic as v1, extended for historical_sci).

    LLM=CONFIRMED  + ≥1 trusted → fact   (high confidence)
    LLM=CONFIRMED  + 0 trusted  → fact   (medium confidence)
    LLM=CONTRADICTED            → fake
    LLM=UNCONFIRMED + ≥2 trusted → fact  (trusted sources agree even if LLM unsure)
    LLM=UNCONFIRMED + ≥3 untrusted → unverified (widespread but unconfirmed)
    LLM=UNCONFIRMED otherwise   → unverified
    """
    articles    = state["articles"]
    keywords    = (state.get("keywords") or "").split()
    llm_verdict = state.get("llm_verdict", "UNCONFIRMED")
    reasoning   = state.get("llm_reasoning", "")

    kws = [_normalise(k) for k in keywords if len(k) > 2]

    trusted_matches   = 0
    untrusted_matches = 0
    total_overlap     = 0
    sources: list[dict] = []

    for a in articles:
        # Include body in overlap calc since fetch_bodies_node enriched it
        blob    = _normalise(
            f"{a.get('title','')} {a.get('description','')} {a.get('body','')}"
        )
        overlap = sum(1 for k in kws if k in blob)
        trusted = _is_trusted(a.get("source_id", ""), a.get("source_name", ""))
        total_overlap += overlap

        if overlap >= 1 and len(sources) < 5:
            url   = a.get("link", "#")
            title = a.get("title", "") or a.get("source_name", "") or url
            if url and url != "#":
                sources.append({"url": url, "title": title})

        if overlap >= 2:
            if trusted:
                trusted_matches += 1
            else:
                untrusted_matches += 1

    ratio   = min(total_overlap / (len(kws) * len(articles) + 0.001), 1.0)
    summary = reasoning

    if llm_verdict == "CONFIRMED":
        if trusted_matches >= 1:
            return {**state, **_make_result(
                "fact",
                min(0.80 + ratio * 0.17, 0.97),
                summary or f"تؤكده {trusted_matches} مصادر موثوقة",
                sources,
            )}
        return {**state, **_make_result(
            "fact",
            min(0.65 + ratio * 0.15, 0.85),
            summary or "تؤكده المصادر المتاحة",
            sources,
        )}

    if llm_verdict == "CONTRADICTED":
        return {**state, **_make_result(
            "fake",
            min(0.70 + ratio * 0.20, 0.92),
            summary or "❌ المصادر تناقض هذا الادعاء",
            sources,
        )}

    # UNCONFIRMED — only promote to fact if ≥2 trusted and LLM doesn't signal off-topic
    off_topic = ("لا تتناول", "لا تذكر", "لا تغطي", "غير ذات صلة", "لا علاقة")
    llm_off_topic = any(s in (summary or "") for s in off_topic)

    if not llm_off_topic and trusted_matches >= 2:
        return {**state, **_make_result(
            "fact",
            min(0.65 + ratio * 0.15, 0.82),
            summary or f"تؤكده {trusted_matches} مصادر موثوقة",
            sources,
        )}

    if untrusted_matches >= 3:
        return {**state, **_make_result(
            "unverified",
            min(0.40 + ratio * 0.12, 0.60),
            summary or "⚠️ الخبر منتشر لكن لم يُؤكَّد من مصادر موثوقة",
            sources,
        )}

    return {**state, **_make_result(
        "unverified",
        0.25,
        summary or "⚠️ لا يمكن التحقق — أدلة غير كافية",
        sources,
    )}


# ─── ROUTING ─────────────────────────────────────────────────────────────────

def _after_classify(state: HAQQState) -> str:
    if state.get("verdict"):
        return END
    content_type = state.get("content_type", "news")
    if content_type == "non_news":
        return "non_news_exit"
    # Both "news" and "historical_scientific" go to keyword extraction
    return "extract_keywords"


def _after_keywords(state: HAQQState) -> str:
    return END if state.get("verdict") else "search"


def _after_search(state: HAQQState) -> str:
    return END if state.get("verdict") else "fetch_bodies"


def _after_bodies(state: HAQQState) -> str:
    return "llm_verify"


# ─── GRAPH BUILDER ───────────────────────────────────────────────────────────

def build_graph() -> Any:
    g = StateGraph(HAQQState)

    def _non_news(state: HAQQState) -> HAQQState:
        return {
            **state,
            "verdict":     "non_news",
            "confidence":  state.get("non_news_score", 0.9),
            "explanation": "💬 هذا محتوى غير إخباري",
            "sources":     [],
        }

    g.add_node("classify",         classify_node)
    g.add_node("extract_keywords", extract_keywords_node)
    g.add_node("search",           search_node)
    g.add_node("fetch_bodies",     fetch_bodies_node)   # NEW
    g.add_node("llm_verify",       llm_verify_node)
    g.add_node("score",            score_node)
    g.add_node("non_news_exit",    _non_news)

    g.set_entry_point("classify")
    g.add_conditional_edges("classify", _after_classify, {
        "non_news_exit":   "non_news_exit",
        "extract_keywords": "extract_keywords",
        END:               END,
    })
    g.add_edge("non_news_exit", END)
    g.add_conditional_edges("extract_keywords", _after_keywords, {
        "search": "search",
        END:      END,
    })
    g.add_conditional_edges("search", _after_search, {
        "fetch_bodies": "fetch_bodies",
        END:            END,
    })
    g.add_conditional_edges("fetch_bodies", _after_bodies, {
        "llm_verify": "llm_verify",
    })
    g.add_edge("llm_verify", "score")
    g.add_edge("score",      END)

    compiled = g.compile()
    print("[HAQQ graph] Graph compiled ✅  (v2 — 3-class + DDG + body fetch)")
    return compiled


# ─── PUBLIC ENTRY POINT ──────────────────────────────────────────────────────

async def run_verify(graph, text: str, lang: str) -> dict:
    initial_state: HAQQState = {
        "text":           text,
        "lang":           lang,
        "content_type":   None,
        "is_news":        None,
        "news_score":     0.0,
        "non_news_score":  0.0,
        "keywords":       None,
        "api_query":      None,
        "search_query":   None,
        "articles":       [],
        "bodies_fetched": False,
        "llm_verdict":    None,
        "llm_reasoning":  None,
        "verdict":        None,
        "confidence":     0.0,
        "explanation":    "",
        "sources":        [],
    }
    final = await graph.ainvoke(initial_state)
    return {
        "verdict":     final.get("verdict",     "unverified"),
        "confidence":  final.get("confidence",  0.0),
        "explanation": final.get("explanation", ""),
        "sources":     final.get("sources",     []),
    }