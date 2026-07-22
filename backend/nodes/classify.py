from backend.core.config import (
    LABEL_TO_TYPE,
    MEDICAL_KEYWORDS_EN, MEDICAL_KEYWORDS_AR, MEDICAL_KEYWORD_THRESHOLD,
)
from backend.core.state import HAQQState

# Global classifier instance, to be injected at startup by the API layer
classifier = None


def _detect_medical(text: str) -> bool:
    """
    Keyword-based medical content detection.

    Why do we still have keyword-based medical detection? As a secondary check/override
    safeguard, in case the SetFit classifier misclassifies a borderline medical query
    (which we want to route through the PubMed search path for better medical sources).

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
      Stage 1: Four-class SetFit model (news / historical_scientific / medical / non_news)
      Stage 2: Keyword-based medical detection — overrides to "medical" if
               the text contains enough medical keywords (as a safeguard)

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

        text_truncated = text[:500]
        probs = classifier.predict_proba([text_truncated])[0]
        pred_idx = int(classifier.predict([text_truncated])[0])

        content_type = LABEL_TO_TYPE[pred_idx]
        best_score = float(probs[pred_idx])

        # We can map news_score and non_news_score
        # Class 0, 1, 2 are news-like. Class 3 is non_news.
        news_score = float(probs[0] + probs[1] + probs[2])
        non_news_score = float(probs[3])

        # Ambiguity guard: if top score < 0.45, treat as news to avoid false negatives
        if best_score < 0.45:
            content_type = "news"

        # ── Stage 2: Medical keyword override ────────────────────────────
        # If the text contains medical keywords, override to "medical"
        # regardless of what the SetFit classifier predicted (unless non_news
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

