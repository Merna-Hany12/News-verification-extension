from backend.core.config import (
    GROQ_MODEL,
    BODY_CHARS_PER_ARTICLE,
    MEDICAL_TRUSTED,
)
from backend.core.state import HAQQState, _make_result
from backend.core.text_processing import _normalise, _is_trusted, _trusted_label
import os
from backend.core.groq_key_rotator import GroqKeyRotator

_keys = [
    os.environ["GROQ_API_KEY_1"],
    os.environ["GROQ_API_KEY_2"],
    os.environ["GROQ_API_KEY_3"],
]
rotator = GroqKeyRotator(_keys)


def _rank_key(a: dict, kws: list[str]) -> tuple[bool, int]:
    """
    Shared ranking key: trusted sources first, then by keyword overlap.
    Used both to pick what the LLM sees and what ends up in `sources`.
    """
    blob    = _normalise(f"{a.get('title','')} {a.get('description','')} {a.get('body','')}")
    overlap = sum(1 for k in kws if k in blob)
    trusted = _is_trusted(a.get("source_id", ""), a.get("source_name", ""))
    return (trusted, overlap)


def _log_trusted(articles: list[dict], tag: str) -> None:
    trusted_found = []
    for a in articles:
        label = _trusted_label(a.get("source_id", ""), a.get("source_name", ""))
        if label:
            name = a.get("source_name") or a.get("source_id") or "unknown"
            trusted_found.append(f"{name} (matched: '{label}')")
    if trusted_found:
        print(f"[HAQQ graph] {tag} trusted sources ({len(trusted_found)}): {', '.join(trusted_found)}")
    else:
        print(f"[HAQQ graph] {tag} trusted sources: none")


# Personal-opinion detection ---------------------------------------------
# The upstream classifier occasionally mislabels subjective, first-person
# opinion posts ("أعتقد أن الحكومة مقصرة", "I think this policy is terrible")
# as verifiable news/medical/general claims. There's no factual claim to
# check evidence against in these cases, so running them through source
# retrieval + LLM verification wastes a call and produces a meaningless
# verdict. This catches the obvious cases before they reach the LLM.
_OPINION_MARKERS_AR = (
    "برأيي", "في رأيي", "من وجهة نظري", "أعتقد أن", "أظن أن", "أشعر أن",
    "في نظري", "حسب رأيي", "أنا أرى", "شخصياً أعتقد", "بصراحة أرى",
)
_OPINION_MARKERS_EN = (
    "i think", "i believe", "in my opinion", "imo", "personally i",
    "i feel like", "my take is", "i reckon", "to me,",
)


def _looks_like_personal_opinion(text: str) -> bool:
    blob = _normalise(text)
    # Check both marker sets regardless of detected `lang` — code-switching
    # and upstream language-detection misses both happen often enough that
    # it's cheap insurance to check both lists either way.
    return any(m in blob for m in _OPINION_MARKERS_AR) or any(m in blob for m in _OPINION_MARKERS_EN)


async def llm_verify_node(state: HAQQState) -> HAQQState:
    claim        = state["text"]
    articles     = state["articles"]
    lang         = state.get("lang", "ar")
    content_type = state.get("content_type", "news")
    keywords     = (state.get("keywords") or "").split()
    kws          = [_normalise(k) for k in keywords if len(k) > 2]

    _log_trusted(articles, "llm_verify_node —")

    if _looks_like_personal_opinion(claim):
        print("[HAQQ graph] Claim looks like personal opinion — routing to NON_NEWS, skipping LLM verification")
        return {
            **state,
            "llm_verdict":        "NON_NEWS",
            "llm_reasoning":      "هذا يبدو رأياً شخصياً وليس ادعاءً يمكن التحقق منه",
            "llm_topic_mismatch": False,
        }

    ranked = sorted(articles, key=lambda a: _rank_key(a, kws), reverse=True)

    top_overlap = _rank_key(ranked[0], kws)[1] if ranked else 0
    if ranked and top_overlap == 0:
        print("[HAQQ graph] LLM skipped — no article overlaps with keywords")
        return {
            **state,
            "llm_verdict":        "UNCONFIRMED",
            "llm_reasoning":      "لا تتناول المصادر المتاحة موضوع الادعاء",
            "llm_topic_mismatch": True,
        }

    snippets = []
    for i, a in enumerate(ranked[:6], 1):
        title    = (a.get("title") or "").strip()
        content  = (a.get("body") or a.get("description") or "")[:BODY_CHARS_PER_ARTICLE].strip()
        src      = a.get("source_name") or a.get("source_id") or "مصدر غير معروف"
        url      = a.get("link", "")
        has_body = bool(a.get("body"))
        trusted  = _is_trusted(a.get("source_id", ""), a.get("source_name", ""))

        tag_bits = ["[full body]" if has_body else "[snippet]"]
        if trusted:
            tag_bits.append("[trusted]")

        snippet_lines = [f"[{i}] ({src}) {' '.join(tag_bits)}"]
        if url:
            snippet_lines.append(f"URL: {url}")
        snippet_lines.append(f"العنوان: {title}")
        if content:
            snippet_lines.append(f"المحتوى: {content}")
        snippets.append("\n".join(snippet_lines))

    snippets_text = "\n\n".join(snippets)

    if content_type == "medical":
        system_prompt = (
            "أنت محقق معلومات طبية متخصص. مهمتك الحكم على صحة الادعاء الطبي علمياً.\n\n"
            "قاعدة حاسمة: أنت تقيّم هل الادعاء الطبي صحيح علمياً أم لا — وليس هل المصادر تتحدث عنه.\n"
            "مثال: إذا كان الادعاء 'شرب المبيض يعالج كورونا' والمصادر تقول 'شرب المبيض خطير ولا يعالج كورونا' "
            "→ الحكم هو CONTRADICTED لأن المصادر تنفي الادعاء الطبي.\n"
            "مثال: إذا كان الادعاء 'التدخين يسبب السرطان' والمصادر تؤكد ذلك علمياً "
            "→ الحكم هو CONFIRMED.\n\n"
            "أجب بهذا الشكل الحرفي فقط — ثلاثة أسطر، بدون أي نص إضافي:\n"
            "السطر الأول: كلمة واحدة: CONFIRMED أو UNCONFIRMED أو CONTRADICTED\n"
            "السطر الثاني: جملة عربية واحدة قصيرة تصف ما تقوله المصادر الطبية.\n"
            "السطر الثالث: كلمة واحدة فقط: TOPIC_MATCH أو TOPIC_MISMATCH.\n\n"
            "تعريفات:\n"
            "- CONFIRMED: الادعاء الطبي صحيح علمياً — مصادر طبية موثوقة تؤكد أن العلاج فعال أو أن السبب حقيقي.\n"
            "- CONTRADICTED: الادعاء الطبي خاطئ — المصادر تنفي فعالية العلاج، تصفه بأنه خطير، أو تكذّب المعلومة الطبية.\n"
            "- UNCONFIRMED: لا توجد أدلة كافية للتأكيد أو النفي، أو النتائج متضاربة.\n"
            "- TOPIC_MISMATCH: المصادر عن موضوع طبي مختلف عن الادعاء.\n\n"
            "تنبيه مهم:\n"
            "- إذا قالت المصادر أن علاجاً ما 'خطير' أو 'لا دليل على فعاليته' أو 'خرافة' → هذا CONTRADICTED\n"
            "- إذا كان الادعاء عن علاج مزيف (مثل شرب مبيض، زيوت تشفي السرطان) والمصادر تحذر منه → CONTRADICTED\n"
            "- لا تخلط بين 'المصادر تتحدث عن نفس الموضوع' و 'المصادر تؤكد صحة الادعاء'\n\n"
            "مستوى الدليل (من الأقوى للأضعف):\n"
            "- المراجعات المنهجية والتحليلات التلوية > التجارب العشوائية > الدراسات الرصدية > آراء الخبراء\n"
            "- التجارب الشخصية والإشاعات ليست دليلاً طبياً\n\n"
            "مهم: يجب أن تكون الاستجابة ثلاثة أسطر فقط دائماً."
        )
    elif content_type == "historical_scientific":
        system_prompt = (
            "أنت محقق حقائق علمية وتاريخية. مهمتك تقييم ما إذا كانت المصادر المتاحة "
            "تؤكد الادعاء العلمي أو التاريخي أم لا.\n\n"
            "أجب بهذا الشكل الحرفي فقط — ثلاثة أسطر، بدون أي نص إضافي:\n"
            "السطر الأول: كلمة واحدة: CONFIRMED أو UNCONFIRMED أو CONTRADICTED\n"
            "السطر الثاني: جملة عربية واحدة قصيرة تصف ما تقوله المصادر عن هذا الموضوع.\n"
            "السطر الثالث: كلمة واحدة فقط: TOPIC_MATCH أو TOPIC_MISMATCH.\n\n"
            "تعريفات:\n"
            "- CONFIRMED: مصدران أو أكثر موثوقان يؤكدان المعلومة بتفاصيل متطابقة.\n"
            "- CONTRADICTED: مصدر موثوق ينفي المعلومة صراحةً أو يصححها.\n"
            "- UNCONFIRMED: أي حالة أخرى.\n"
            "- TOPIC_MISMATCH: المصادر عن نفس المجال العام لكنها لا تتناول تفاصيل الادعاء تحديداً "
            "(حدث مختلف، شخص مختلف، واقعة مختلفة، أو مجرد توترات عامة وليس الحدث المحدد بالادعاء).\n\n"
            "مهم: يجب أن تكون الاستجابة ثلاثة أسطر فقط دائماً، مهما كانت الإجابة."
        )
    else:
        system_prompt = (
            "أنت محقق أخبار محترف. مهمتك تقييم ما إذا كانت المقالات تؤكد الادعاء أم لا.\n\n"
            "أجب بهذا الشكل الحرفي فقط — ثلاثة أسطر، بدون أي نص إضافي:\n"
            "السطر الأول: كلمة واحدة: CONFIRMED أو UNCONFIRMED أو CONTRADICTED\n"
            "السطر الثاني: جملة عربية واحدة قصيرة تصف ما تقوله المصادر عن هذا الموضوع.\n"
            "السطر الثالث: كلمة واحدة فقط: TOPIC_MATCH أو TOPIC_MISMATCH.\n\n"
            "تعريفات:\n"
            "- CONFIRMED: مقالتان أو أكثر تتناول نفس الحدث بتفاصيل متطابقة.\n"
            "- CONTRADICTED: مصدر موثوق ينفي الادعاء صراحةً.\n"
            "- UNCONFIRMED: أي حالة أخرى.\n"
            "- TOPIC_MISMATCH: المصادر عن نفس المجال العام (مثلاً التوترات بين إيران وإسرائيل) "
            "لكنها لا تتناول التفاصيل المحددة في الادعاء (حدث مختلف، تصريح مختلف، واقعة مختلفة).\n\n"
            "مهم جداً: يجب أن تكون الاستجابة ثلاثة أسطر فقط دائماً، مهما كانت الإجابة."
        )

    user_prompt = (
        f"الادعاء:\n{claim[:600]}\n\n"
        f"المصادر:\n{snippets_text}"
    )

    # Backup phrase list — used ONLY as a secondary safety net on top of the
    # structured TOPIC_MATCH/TOPIC_MISMATCH signal, in case the model's
    # third line gets truncated or it drifts from the exact format.
    _NEGATION_PATTERNS = (
        "لا تتحدث عن", "لا تتناول", "لا تذكر", "لا تغطي",
        "غير ذات صلة", "لا علاقة", "بل تتناول", "لا يوجد",
    )

    try:
        resp  = await rotator.chat_completion(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=300,   # ← bumped from 200 — was likely truncating line 3
            temperature=0.1,
        )
        raw       = resp.choices[0].message.content.strip()
        lines     = [l.strip() for l in raw.splitlines() if l.strip()]
        llm_label = lines[0].upper() if lines else "UNCONFIRMED"
        summary   = lines[1] if len(lines) > 1 else ""
        topic_raw = lines[2].upper() if len(lines) > 2 else ""   # ← no permissive default

        for tag in ("CONFIRMED", "CONTRADICTED", "UNCONFIRMED"):
            if tag in llm_label:
                llm_label = tag
                break
        else:
            llm_label = "UNCONFIRMED"

        topic_mismatch_explicit = "TOPIC_MISMATCH" in topic_raw
        topic_match_explicit    = "TOPIC_MATCH" in topic_raw and not topic_mismatch_explicit

        # Secondary safety net: if the model didn't clearly say TOPIC_MATCH
        # (either it said TOPIC_MISMATCH, or the line was missing/malformed),
        # fall back to scanning the summary text for negation phrasing.
        text_signals_mismatch = any(p in (summary or "") for p in _NEGATION_PATTERNS)

        if topic_mismatch_explicit:
            topic_mismatch = True
        elif topic_match_explicit:
            topic_mismatch = False
        else:
            # Line 3 missing/unparseable — don't default to "trusted", check
            # the text, and if that's inconclusive too, be conservative.
            topic_mismatch = True if text_signals_mismatch else True
            # (kept explicit rather than collapsed, so the conservative
            # intent is obvious: unclear signal → treat as mismatch)

        if topic_mismatch_explicit or text_signals_mismatch:
            topic_mismatch = True

        if llm_label == "CONFIRMED" and topic_mismatch:
            print("[HAQQ graph] LLM said CONFIRMED but topic signal says mismatch — downgrading to UNCONFIRMED")
            llm_label = "UNCONFIRMED"

        print(f"[HAQQ graph] LLM → {llm_label} | topic_match={not topic_mismatch} "
              f"(explicit_line3='{topic_raw}', text_backup_fired={text_signals_mismatch}) | {summary[:80]}")

        return {
            **state,
            "llm_verdict":        llm_label,
            "llm_reasoning":      summary,
            "llm_topic_mismatch": topic_mismatch,
        }

    except Exception as exc:
        print(f"[HAQQ graph] LLM error: {exc}")
        return {
            **state,
            "llm_verdict":        "UNCONFIRMED",
            "llm_reasoning":      "",
            "llm_topic_mismatch": True,
        }


def score_node(state: HAQQState) -> HAQQState:
    """
    Decision table.

    LLM=NON_NEWS                    → non_news (personal opinion, not a checkable claim)
    LLM=CONFIRMED    + topic_mismatch → unverified (never promote a topic-mismatched
                                        result to "fact", regardless of llm_verdict —
                                        second layer of defense on top of the
                                        downgrade already attempted in llm_verify_node)
    LLM=CONFIRMED    + ≥1 trusted   → fact   (high confidence)
    LLM=CONFIRMED    + 0 trusted    → fact   (medium confidence)
    LLM=CONTRADICTED                → fake
    LLM=UNCONFIRMED  + ≥3 untrusted → unverified (widespread but unconfirmed)
    LLM=UNCONFIRMED  otherwise      → unverified

    The LLM's verdict is authoritative for promoting a claim to `fact` —
    trusted-source count no longer overrides an UNCONFIRMED verdict, since
    keyword-overlap trust can't tell "on-topic" from "actually confirms this
    specific claim" (e.g. CDC pages *about* the common cold being miscounted
    as corroboration for a *cure-found* claim they never made).
    """
    articles     = state["articles"]
    keywords     = (state.get("keywords") or "").split()
    llm_verdict  = state.get("llm_verdict", "UNCONFIRMED")
    reasoning    = state.get("llm_reasoning", "")
    content_type = state.get("content_type", "news")
    llm_off_topic = state.get("llm_topic_mismatch", False)

    if llm_verdict == "NON_NEWS":
        return {**state, **_make_result(
            "non_news",
            0.0,
            reasoning or "هذا رأي شخصي وليس ادعاءً إخبارياً يمكن التحقق منه",
            [],
        )}
    kws = [_normalise(k) for k in keywords if len(k) > 2]

    trusted_matches   = 0
    untrusted_matches = 0
    total_overlap     = 0
    candidate_sources: list[dict] = []

    for a in articles:
        blob    = _normalise(
            f"{a.get('title','')} {a.get('description','')} {a.get('body','')}"
        )
        overlap = sum(1 for k in kws if k in blob)
        # For medical content, also check against MEDICAL_TRUSTED sources
        if content_type == "medical":
            sid = a.get("source_id", "").lower()
            snam = a.get("source_name", "").lower()
            trusted = (
                _is_trusted(sid, snam)
                or any(mt in sid or mt in snam for mt in MEDICAL_TRUSTED)
            )
        else:
            trusted = _is_trusted(a.get("source_id", ""), a.get("source_name", ""))
        total_overlap += overlap

        if trusted:
            print(f"[HAQQ graph] score_node — trusted candidate "
                  f"'{a.get('title','')[:40]}' overlap={overlap}")

        # Every source — trusted or not — must actually overlap with the
        # claim's keywords to be included. Being from a trusted outlet no
        # longer waives this: it used to allow overlap==0, which is exactly
        # how unrelated-but-trusted articles (e.g. a different match
        # entirely) ended up listed as "sources" for an unrelated claim.
        # Trusted-ness now only affects ranking/confidence, not inclusion.
        min_overlap_for_inclusion = 1

        if overlap >= min_overlap_for_inclusion:
            url   = a.get("link", "#")
            title = a.get("title", "") or a.get("source_name", "") or url
            if url and url != "#":
                candidate_sources.append({
                    "url": url,
                    "title": title,
                    "trusted": trusted,
                    "overlap": overlap,
                })

        if overlap >= 2:
            if trusted:
                trusted_matches += 1
            else:
                untrusted_matches += 1

    candidate_sources.sort(key=lambda s: (s["trusted"], s["overlap"]), reverse=True)
    sources = candidate_sources[:5]

    print(f"[HAQQ graph] score_node — final sources order: "
          f"{[(s['title'][:30], s['trusted'], s['overlap']) for s in sources]}")

    ratio   = min(total_overlap / (len(kws) * len(articles) + 0.001), 1.0)
    summary = reasoning

    if llm_verdict == "CONFIRMED":
        # Second layer of defense: llm_verify_node already tries to downgrade
        # CONFIRMED+mismatch to UNCONFIRMED before this node ever sees it,
        # but score_node shouldn't rely on that alone. If the topic-mismatch
        # flag made it through anyway, never promote to "fact" here.
        if llm_off_topic:
            print("[HAQQ graph] score_node — CONFIRMED arrived with topic_mismatch=True — treating as unverified")
            return {**state, **_make_result(
                "unverified",
                0.25,
                summary or "⚠️ المصادر لا تتطابق مع تفاصيل الادعاء المحدد",
                sources,
            )}

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

    # UNCONFIRMED — the LLM's verdict is authoritative here. Trusted-source
    # count is no longer used to promote UNCONFIRMED into fact, since
    # keyword-overlap trust can't distinguish "on-topic" from "actually
    # confirms this claim" (e.g. CDC pages *about* the common cold being
    # miscounted as corroboration for a *cure-found* claim they never made).
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