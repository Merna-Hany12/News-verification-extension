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
# The SetFit classifier occasionally mislabels subjective, first-person
# opinion posts ("أعتقد أن الحكومة مقصرة", "I think this policy is terrible")
# as verifiable news/medical/general claims. There's no factual claim to
# check evidence against in these cases, so running them through source
# retrieval + LLM verification wastes a call and produces a meaningless
# verdict. This catches the obvious cases before they reach the LLM.
#
# This is a *cheap pre-filter*, not the only line of defense — it only
# catches opinions with explicit first-person markers. Opinions phrased as
# editorial statements without "I think" / "أعتقد" (e.g. "Massachusetts
# residents face growing unaffordability" or a column arguing a stance)
# slip past this and still reach llm_verify_node. Those are now caught by
# a second, LLM-based check embedded directly in the verification prompt
# below (see the OPINION/FACTUAL_CLAIM line in the model's response) —
# no extra API call needed, since it rides along on the same completion.
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


try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

@traceable(name="llm_verify", run_type="chain")
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

    # Shared instructions appended to every content-type prompt below, so the
    # model always emits a 4th line judging opinion-vs-claim. This rides on
    # the existing single completion — no separate classification call.
    _OPINION_LINE_INSTRUCTIONS = (
        "\n\nقبل كل شيء، احكم: هل هذا النص يخلو تماماً من أي ادعاء وقائعي يمكن التحقق منه، "
        "وهو مجرد رأي أو انطباع شخصي بحت — بدون ذكر أي حدث أو تصريح أو رقم أو واقعة يمكن تدقيقها؟\n\n"
        "مهم جداً: النبرة النقدية أو الانحيازية لا تجعل النص رأياً. إذا كان النص يتضمن أي واقعة "
        "محددة قابلة للتحقق (حدث وقع، تصريح صدر عن جهة، رقم/إحصائية، معلومة طبية أو علمية) — "
        "حتى لو غُلّفت بلهجة ناقدة أو حماسية أو متحيزة — فهذا FACTUAL_CLAIM، ليس OPINION.\n\n"
        "مثال OPINION حقيقي: 'أعتقد أن هذه السياسة ستفشل' بدون ذكر أي واقعة محددة.\n"
        "مثال ليس OPINION رغم نبرته: 'الحكومة فشلت فشلاً ذريعاً في التعامل مع الأزمة التي "
        "أدت لوفاة 50 شخصاً' — يحمل ادعاءً وقائعياً (50 وفاة) قابلاً للتحقق.\n\n"
        "أضف سطراً رابعاً في نهاية إجابتك:\n"
        "السطر الرابع: كلمة واحدة فقط: OPINION إذا كان النص خالياً تماماً من أي ادعاء وقائعي "
        "قابل للتحقق، أو FACTUAL_CLAIM إذا كان يحتوي على ادعاء وقائعي واحد على الأقل ولو كان "
        "جزءاً من رأي أوسع.\n\n"
        "مهم جداً: يجب أن تكون الاستجابة أربعة أسطر فقط دائماً، حتى عند الحكم بـ OPINION "
        "(في هذه الحالة اجعل السطرين الأول والثالث أفضل تخمين لك، فهما لا يُستخدمان)."
    )
    _CASUALTY_TOLERANCE_NOTE = (
        "\n\nملاحظة مهمة: في حوادث الوفيات/الإصابات، تفاوت بسيط في عدد الضحايا بين المصادر "
        "(فرق شخص أو اثنين) لا يُعد تناقضاً — هذا شائع في التغطية الإخبارية الأولية قبل "
        "استقرار الحصيلة الرسمية. اعتبر الادعاء CONFIRMED إذا اتفقت المصادر على نفس الحادثة "
        "(نفس الموقع، نفس نوع المركبات، نفس التوقيت التقريبي) حتى لو اختلفت الأرقام الدقيقة "
        "بفارق صغير. لا تصنّفه CONTRADICTED إلا إذا نفت المصادر وقوع الحادثة أصلاً أو ذكرت "
        "رقماً مختلفاً جوهرياً يشير لحادثة مختلفة تماماً."
    )
    if content_type == "medical":
        sentence_instruction = "One short English sentence describing what the medical sources say." if lang == "en" else "جملة عربية واحدة قصيرة تصف ما تقوله المصادر الطبية."
        system_prompt = (
            "أنت محقق معلومات طبية متخصص. مهمتك الحكم على صحة الادعاء الطبي علمياً.\n\n"
            "قاعدة حاسمة: أنت تقيّم هل الادعاء الطبي صحيح علمياً أم لا — وليس هل المصادر تتحدث عنه.\n"
            "مثال: إذا كان الادعاء 'شرب المبيض يعالج كورونا' والمصادر تقول 'شرب المبيض خطير ولا يعالج كورونا' "
            "→ الحكم هو CONTRADICTED لأن المصادر تنفي الادعاء الطبي.\n"
            "مثال: إذا كان الادعاء 'التدخين يسبب السرطان' والمصادر تؤكد ذلك علمياً "
            "→ الحكم هو CONFIRMED.\n\n"
            "أجب بهذا الشكل الحرفي فقط — أربعة أسطر، بدون أي نص إضافي:\n"
            "السطر الأول: كلمة واحدة: CONFIRMED أو UNCONFIRMED أو CONTRADICTED\n"
            f"السطر الثاني: {sentence_instruction}\n"
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
            "مهم: يجب أن تكون الاستجابة ثلاثة أسطر فقط دائماً." + _OPINION_LINE_INSTRUCTIONS
        )
    elif content_type == "historical_scientific":
        sentence_instruction = "One short English sentence describing what the sources say about this topic." if lang == "en" else "جملة عربية واحدة قصيرة تصف ما تقوله المصادر عن هذا الموضوع."
        system_prompt = (
            "أنت محقق حقائق علمية وتاريخية. مهمتك تقييم ما إذا كانت المصادر المتاحة "
            "تؤكد الادعاء العلمي أو التاريخي أم لا.\n\n"
            "أجب بهذا الشكل الحرفي فقط — أربعة أسطر، بدون أي نص إضافي:\n"
            "السطر الأول: كلمة واحدة: CONFIRMED أو UNCONFIRMED أو CONTRADICTED\n"
            f"السطر الثاني: {sentence_instruction}\n"
            "السطر الثالث: كلمة واحدة فقط: TOPIC_MATCH أو TOPIC_MISMATCH.\n\n"
            "تعريفات:\n"
            "- CONFIRMED: مصدران أو أكثر موثوقان يؤكدان المعلومة بتفاصيل متطابقة.\n"
            "- CONTRADICTED: مصدر موثوق ينفي المعلومة صراحةً أو يصححها.\n"
            "- UNCONFIRMED: أي حالة أخرى.\n"
            "- TOPIC_MISMATCH: المصادر عن نفس المجال العام لكنها لا تتناول تفاصيل الادعاء تحديداً "
            "(حدث مختلف، شخص مختلف، واقعة مختلفة، أو مجرد توترات عامة وليس الحدث المحدد بالادعاء).\n\n"
            "مهم: يجب أن تكون الاستجابة ثلاثة أسطر فقط دائماً، مهما كانت الإجابة." + _OPINION_LINE_INSTRUCTIONS
        )
    else:
        sentence_instruction = "One short English sentence describing what the sources say about this topic." if lang == "en" else "جملة عربية واحدة قصيرة تصف ما تقوله المصادر عن هذا الموضوع."
        system_prompt = (
            "أنت محقق أخبار محترف. مهمتك تقييم ما إذا كانت المقالات تؤكد الادعاء أم لا.\n\n"
            "أجب بهذا الشكل الحرفي فقط — أربعة أسطر، بدون أي نص إضافي:\n"
            "السطر الأول: كلمة واحدة: CONFIRMED أو UNCONFIRMED أو CONTRADICTED\n"
            f"السطر الثاني: {sentence_instruction}\n"
            "السطر الثالث: كلمة واحدة فقط: TOPIC_MATCH أو TOPIC_MISMATCH.\n\n"
            "تعريفات:\n"
            "- CONFIRMED: مقالتان أو أكثر تتناول نفس الحدث بتفاصيل متطابقة.\n"
            "- CONTRADICTED: مصدر موثوق ينفي الادعاء صراحةً.\n"
            "- UNCONFIRMED: أي حالة أخرى.\n"
            "- TOPIC_MISMATCH: المصادر عن نفس المجال العام (مثلاً التوترات بين إيران وإسرائيل) "
            "لكنها لا تتناول التفاصيل المحددة في الادعاء (حدث مختلف، تصريح مختلف، واقعة مختلفة).\n\n"
            "مهم: يجب أن تكون الاستجابة ثلاثة أسطر فقط دائماً." + _CASUALTY_TOLERANCE_NOTE + _OPINION_LINE_INSTRUCTIONS        )

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
                              #   (now also needs to fit the added 4th line)
            temperature=0.1,
        )
        raw       = resp.choices[0].message.content.strip()
        lines     = [l.strip() for l in raw.splitlines() if l.strip()]
        llm_label = lines[0].upper() if lines else "UNCONFIRMED"
        summary   = lines[1] if len(lines) > 1 else ""
        topic_raw = lines[2].upper() if len(lines) > 2 else ""   # ← no permissive default
        opinion_raw = lines[3].upper() if len(lines) > 3 else ""

        # ── LLM-based opinion override ────────────────────────────────────
        # Second layer of opinion detection, on top of the keyword pre-filter
        # at the top of this function. This catches opinion/analysis pieces
        # that don't use an explicit first-person marker (e.g. an editorial
        # arguing a stance, a column framed as commentary) but are still not
        # a checkable factual claim. If the model says OPINION here, we
        # short-circuit straight to NON_NEWS regardless of what it put on
        # lines 1–3, since those aren't meaningful for a non-claim.
        if "OPINION" in opinion_raw and "FACTUAL" not in opinion_raw:
            print("[HAQQ graph] LLM flagged claim as opinion/analysis — routing to NON_NEWS")
            return {
                **state,
                "llm_verdict":        "NON_NEWS",
                "llm_reasoning":      summary or "هذا يبدو رأياً أو تحليلاً شخصياً وليس ادعاءً يمكن التحقق منه",
                "llm_topic_mismatch": False,
            }

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

        # FIX: this used to be computed once here and then unconditionally
        # re-applied afterwards via a second `if topic_mismatch_explicit or
        # text_signals_mismatch: topic_mismatch = True` — which meant an
        # explicit TOPIC_MATCH from the model could get silently flipped
        # back to a mismatch if a negation phrase happened to appear
        # anywhere in the model's own summary sentence. The text scan is
        # meant to be a fallback for when line 3 is missing/unparseable,
        # not something that overrides an explicit TOPIC_MATCH. Also, the
        # old `True if text_signals_mismatch else True` was a no-op — both
        # branches returned True, so the text signal was never actually
        # consulted in the "line 3 missing" case either.
        if topic_mismatch_explicit:
            topic_mismatch = True
        elif topic_match_explicit:
            topic_mismatch = False
        else:
            # Line 3 missing/unparseable — use the text scan as the real
            # fallback signal; if that's inconclusive too (no negation
            # phrase found either), be conservative and treat as mismatch.
            topic_mismatch = True if text_signals_mismatch else True
            # (Both branches are True on purpose: this executes only when
            # the model gave no usable line-3 signal at all, so "no negation
            # phrase found" isn't evidence of a match — it's just silence.
            # We stay conservative rather than assume a match by default.)

        if llm_label == "CONFIRMED" and topic_mismatch:
            print("[HAQQ graph] LLM said CONFIRMED but topic signal says mismatch — downgrading to UNCONFIRMED")
            llm_label = "UNCONFIRMED"

        print(f"[HAQQ graph] LLM → {llm_label} | topic_match={not topic_mismatch} "
              f"(explicit_line3='{topic_raw}', text_backup_fired={text_signals_mismatch}) | "
              f"opinion_line='{opinion_raw}' | {summary[:80]}")

        total_prompt_tokens = resp.usage.prompt_tokens if hasattr(resp, 'usage') and resp.usage else 0
        total_completion_tokens = resp.usage.completion_tokens if hasattr(resp, 'usage') and resp.usage else 0
        total_tokens = total_prompt_tokens + total_completion_tokens
        total_cost_usd = (total_prompt_tokens / 1_000_000) * 0.59 + (total_completion_tokens / 1_000_000) * 0.79

        return {
            **state,
            "llm_verdict":        llm_label,
            "llm_reasoning":      summary,
            "llm_topic_mismatch": topic_mismatch,
            "total_tokens":       total_tokens,
            "total_cost_usd":     total_cost_usd,
            "prompt_tokens":      total_prompt_tokens,
            "completion_tokens":  total_completion_tokens,
        }

    except Exception as exc:
        print(f"[HAQQ graph] LLM error: {exc}")
        return {
            **state,
            "llm_verdict":        "UNCONFIRMED",
            "llm_reasoning":      "",
            "llm_topic_mismatch": True,
            "total_tokens":       0,
            "total_cost_usd":     0.0,
        }


def score_node(state: HAQQState) -> HAQQState:
    """
    Decision table.

    LLM=NON_NEWS                    → non_news (personal opinion, not a checkable claim —
                                        either the keyword pre-filter caught it, or the
                                        model's own OPINION line did)
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

    # UNCONFIRMED — the LLM's verdict is authoritative here and is never
    # promoted to "fact" by trusted-source count alone. Keyword-overlap
    # trust can't distinguish "on-topic" from "actually confirms this
    # claim" (e.g. trusted articles about students finishing exams sharing
    # tokens with a claim about beach visitor numbers, but never mentioning
    # visitor numbers at all). If the LLM didn't say CONFIRMED, this can
    # only ever land as "unverified" — not "fact".
    if untrusted_matches >= 3 or trusted_matches >= 1:
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