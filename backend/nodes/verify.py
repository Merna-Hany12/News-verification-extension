from groq import AsyncGroq

from backend.core.config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    BODY_CHARS_PER_ARTICLE,
)
from backend.core.state import HAQQState, _make_result
from backend.core.text_processing import _normalise, _is_trusted


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
