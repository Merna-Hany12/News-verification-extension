from backend.core.config import CLASSIFY_LABELS, LABEL_TO_TYPE
from backend.core.state import HAQQState

# Global classifier instance, to be injected at startup by the API layer
classifier = None


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
        global classifier
        if classifier is None:
            # If classifier is not loaded (e.g. running in testing without full startup),
            # we could try to fallback or raise. Let's raise to fail fast, but catch and fail open.
            raise RuntimeError("Classifier has not been initialized.")

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
