from backend.core.config import (
    CLASSIFY_LABELS, LABEL_TO_TYPE,
    MEDICAL_KEYWORDS_EN, MEDICAL_KEYWORDS_AR, MEDICAL_KEYWORD_THRESHOLD,
)
from backend.core.state import HAQQState

# Global classifier instance, to be injected at startup by the API layer
classifier = None


def _detect_medical(text: str) -> bool:
    """
    Keyword-based medical content detection.

    Why not use the zero-shot classifier? Because "medical" and "historical/scientific"
    overlap too heavily — the mDeBERTa model classifies "smoking causes lung cancer"
    as scientific (which is technically correct, but we want to route it through
    the PubMed search path for better medical sources).

    Returns True if the text contains ≥ MEDICAL_KEYWORD_THRESHOLD medical keywords.
    """
    text_lower = text.lower()
    count = 0

    for kw in MEDICAL_KEYWORDS_EN:
        if kw in text_lower:
            count += 1
            if count >= MEDICAL_KEYWORD_THRESHOLD:
                return True

    for kw in MEDICAL_KEYWORDS_AR:
        if kw in text:
            count += 1
            if count >= MEDICAL_KEYWORD_THRESHOLD:
                return True

    return False


def classify_node(state: HAQQState) -> HAQQState:
    """
    Two-stage classification:
      Stage 1: Three-class zero-shot (news / historical_scientific / non_news)
      Stage 2: Keyword-based medical detection — overrides to "medical" if
               the text contains enough medical keywords

    content_type values: "news" | "historical_scientific" | "medical" | "non_news"
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
        global classifier
        if classifier is None:
            raise RuntimeError("Classifier has not been initialized.")

        out     = classifier(text[:500], CLASSIFY_LABELS)
        best_label_text = out["labels"][0]
        best_score      = float(out["scores"][0])

        # Map the label string back to an index
        label_index = CLASSIFY_LABELS.index(best_label_text)
        content_type = LABEL_TO_TYPE[label_index]

        # Collect all scores for logging
        scores = dict(zip(out["labels"], out["scores"]))
        news_score     = float(scores.get(CLASSIFY_LABELS[0], 0.0))
        non_news_score = float(scores.get(CLASSIFY_LABELS[2], 0.0))

        # Ambiguity guard: if top score < 0.45, treat as news to avoid false negatives
        if best_score < 0.45:
            content_type = "news"

        # ── Stage 2: Medical keyword override ────────────────────────────
        # If the text contains medical keywords, override to "medical"
        # regardless of what the zero-shot classifier said (unless non_news
        # with high confidence — casual health chat shouldn't trigger medical)
        is_medical = _detect_medical(text)
        if is_medical and not (content_type == "non_news" and best_score > 0.60):
            content_type = "medical"
            print(f"[HAQQ graph] classify → medical override (keyword detection)")

        # Backward-compat: is_news stays True for verifiable content types
        is_news = content_type in ("news", "historical_scientific", "medical")

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

