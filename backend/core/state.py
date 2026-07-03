from typing import Any, Optional
from typing_extensions import TypedDict

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


def _make_result(verdict: str, confidence: float, explanation: str, sources: list[dict]) -> dict:
    return {"verdict": verdict, "confidence": confidence, "explanation": explanation, "sources": sources}
