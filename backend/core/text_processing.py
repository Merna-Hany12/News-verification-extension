import math
import re
from typing import Optional
from urllib.parse import urlparse

import yake  # pip install yake --break-system-packages   (add "yake" to requirements.txt)

from backend.core.config import TRUSTED

# ─── STOP WORDS ──────────────────────────────────────────────────────────────
# These are still needed as a *second pass* on top of YAKE: YAKE's built-in
# stopword lists are generic and don't know domain-specific junk like Arabic
# reporting verbs ("قال", "أضاف") or wire-service boilerplate ("عاجل").
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


# ─── KEYWORD EXTRACTION ───────────────────────────────────────────────────────
# Two implementations live side by side on purpose: _extract_keywords_yake is
# the current production default, _extract_keywords_heuristic is the earlier
# hand-rolled scoring approach (no external dependency). Keeping both lets the
# benchmark A/B test them against each other instead of just trusting that
# "the fancier one must be better." `_extract_keywords` is an alias pointing
# at whichever is the current default — nothing else in the codebase needs to
# know or care which implementation is active.

LEAD_CHARS = 700   # inverted-pyramid convention: the core who/what/where lives
                   # in the first ~700 chars of a news article — truncating here
                   # keeps latency flat regardless of how long the article is, for
                   # BOTH extractors below

# --- YAKE extractor (production default) -------------------------------------
YAKE_TOP = 30        # cap on returned keywords: short queries stay fast + on-topic
YAKE_NGRAM = 3     # allow 2-word phrases, e.g. "Saudi Arabia" not "saudi" + "arabia"
YAKE_DEDUP_LIM =  0.5

_yake_extractors: dict[str, "yake.KeywordExtractor"] = {}

def _get_yake_extractor(lang: str, text_len: int = 300) -> "yake.KeywordExtractor":
    ngram = 3 if text_len > 200 else 2
    cache_key = f"{lang}_{ngram}"
    if cache_key not in _yake_extractors:
        _yake_extractors[cache_key] = yake.KeywordExtractor(
            lan=lang,
            n=ngram,
            dedupLim=YAKE_DEDUP_LIM,
            top=YAKE_TOP,
            windowsSize=3,
        )
    return _yake_extractors[cache_key]

def _extract_keywords_yake(text: str) -> Optional[str]:
    if not text or not text.strip():
        return None

    lang = "ar" if _is_arabic(text) else "en"
    extractor = _get_yake_extractor(lang)

    try:
        # YAKE returns [(phrase, score), ...] already sorted best-first
        # (lower score = more relevant — this is YAKE's convention, not a bug)
        raw = extractor.extract_keywords(text[:LEAD_CHARS])
    except Exception as exc:
        print(f"[text_processing] YAKE extraction failed: {exc}")
        return None

    stops = STOPS_AR if lang == "ar" else STOPS_EN
    seen_words: set[str] = set()
    output: list[str] = []
    for phrase, _score in raw:
        norm = _normalise(phrase)
        tokens = norm.split()
        if not tokens or any(t in stops for t in tokens):
            continue
        # keep only the words from this phrase not already used by a
        # higher-ranked phrase — this is what prevents a word like "national"
        # from showing up twice just because it appeared in two YAKE phrases
        new_tokens = [t for t in tokens if t not in seen_words]
        if not new_tokens:
            continue
        seen_words.update(new_tokens)
        output.append(" ".join(new_tokens))

    return " ".join(output) or None


# --- Hand-rolled heuristic extractor (no external dependency) ----------------
HEURISTIC_MAX_KEYWORDS = 8

_TOKEN_RE = re.compile(r"[A-Za-z\u0600-\u06FF0-9]+")
_SENT_END_RE = re.compile(r"[.!?؟]")


def _tokenize_with_entity_flags(text: str) -> list[tuple[str, bool]]:
    """
    Single-pass tokenizer over the lead portion of the text (see LEAD_CHARS).
    Yields (normalized_word, is_entity_signal) in reading order.

    is_entity_signal: capitalized Latin word that is NOT the first word of its
    sentence — mid-sentence capitalization is a strong proper-noun signal;
    sentence-initial capitalization is just grammar / headline styling.
    """
    text = text[:LEAD_CHARS]
    tokens: list[tuple[str, bool]] = []
    new_sentence = True
    prev_end = 0
    for m in _TOKEN_RE.finditer(text):
        raw = m.group()
        if _SENT_END_RE.search(text[prev_end:m.start()]):
            new_sentence = True
        is_entity = (not new_sentence) and raw.isascii() and raw.isalpha() and raw[0].isupper()
        norm = _normalise(raw)
        if norm:
            tokens.append((norm, is_entity))
        new_sentence = False
        prev_end = m.end()
    return tokens


def _extract_keywords_heuristic(text: str, max_keywords: int = HEURISTIC_MAX_KEYWORDS) -> Optional[str]:
    tokens = _tokenize_with_entity_flags(text)

    freq: dict[str, int] = {}
    is_entity: dict[str, bool] = {}
    first_pos: dict[str, int] = {}

    for pos, (word, entity_flag) in enumerate(tokens):
        min_len = 2 if _is_arabic(word) else 4
        if len(word) < min_len or word in STOPS_AR or word in STOPS_EN:
            continue
        freq[word] = freq.get(word, 0) + 1
        is_entity[word] = is_entity.get(word, False) or entity_flag
        first_pos.setdefault(word, pos)

    if not freq:
        return None

    total = len(tokens) or 1

    def score(word: str) -> float:
        s = math.log1p(freq[word]) * 2.0              # diminishing returns per repeat
        if is_entity[word]:
            s += 3.0                                    # proper nouns = precise anchors
        s += max(0.0, 1.0 - (first_pos[word] / total))  # earlier = closer to the lead
        return s

    ranked = sorted(freq, key=lambda w: (-score(w), first_pos[w]))
    top = ranked[:max_keywords]
    return " ".join(sorted(top, key=lambda w: first_pos[w])) or None


# Which extractor _extract_keywords() dispatches to at call time. Mutable at
# runtime — the benchmark script flips this between "yake" and "heuristic" to
# A/B them against the same dataset in one process, without a restart.
KEYWORD_EXTRACTOR_METHOD = "yake"  # "yake" | "heuristic"


def _extract_keywords(text: str) -> Optional[str]:
    if not text or not text.strip():
        return None
    if KEYWORD_EXTRACTOR_METHOD == "heuristic":
        return _extract_keywords_heuristic(text)
    return _extract_keywords_yake(text)


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