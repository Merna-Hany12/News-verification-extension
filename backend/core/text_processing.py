import re
from typing import Optional
from urllib.parse import urlparse
from backend.core.config import TRUSTED

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


# ─── TRUSTED-SOURCE MATCHING ─────────────────────────────────────────────────
# Precompile one word-boundary pattern per TRUSTED entry, once, at import time.
# Word-boundary matching prevents false positives like "time" matching inside
# "The Economic Times" or "ap" matching inside "apparel" — plain substring
# matching (`t in sid`) was doing exactly that.
_TRUSTED_PATTERNS: list[tuple[str, re.Pattern]] = [
    (t, re.compile(rf"\b{re.escape(t)}\b", re.UNICODE))
    for t in TRUSTED
]


def _is_trusted(source_id: str, source_name: str) -> bool:
    sid  = source_id.lower()
    snam = source_name.lower()
    return any(p.search(sid) or p.search(snam) for _, p in _TRUSTED_PATTERNS)


def _trusted_label(source_id: str, source_name: str) -> Optional[str]:
    """
    Same check as _is_trusted, but returns *which* TRUSTED entry matched
    (or None). Useful for logging/debugging false positives/negatives
    instead of just a bool.
    """
    sid  = source_id.lower()
    snam = source_name.lower()
    for term, pattern in _TRUSTED_PATTERNS:
        if pattern.search(sid) or pattern.search(snam):
            return term
    return None


def _html_decode(s: str) -> str:
    return (
        s.replace("&amp;", "&").replace("&lt;", "<")
         .replace("&gt;", ">").replace("&quot;", '"')
         .replace("&#39;", "'").replace("&nbsp;", " ").strip()
    )


def _xml_tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([\s\S]*?)</{tag}>", block, re.IGNORECASE)
    return m.group(1) if m else ""


def _strip_cdata(s: str) -> str:
    return re.sub(r"^<!\[CDATA\[", "", s).replace("]]>", "").strip()

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

        # `link` is the actual per-article URL (via Google News redirect).
        # `src_url` from <source url="..."> is just the publisher's
        # homepage — never use it as the clickable link, only for
        # deriving the source_id/host below.
        real_link = _html_decode(_strip_cdata(link))

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