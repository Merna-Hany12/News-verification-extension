from typing import Any, Optional
from typing_extensions import TypedDict

class HAQQState(TypedDict):
    # inputs
    text:           str
    lang:           str

    # set by classify_node
    content_type:   Optional[str]   # "news" | "historical_scientific" | "medical" | "non_news"
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
    llm_verdict:         Optional[str]   # CONFIRMED | UNCONFIRMED | CONTRADICTED | NON_NEWS
    llm_reasoning:       Optional[str]
    llm_topic_mismatch:  bool            # FIX: was set by llm_verify_node/read by score_node
                                          # but never declared here — undeclared keys aren't
                                          # guaranteed to survive LangGraph's state merge
                                          # between node transitions.
    total_tokens:        int             # FIX: same issue — llm_verify_node attaches these
    total_cost_usd:      float           # on every real Groq call, but since they weren't
    prompt_tokens:       int             # part of the schema, they were dropped before
    completion_tokens:   int             # reaching `final` in run_verify(), showing up as
                                          # 0 tokens / $0 cost for every non-agent run even
                                          # though the LLM was genuinely being called.

    # set by score_node / early exits
    verdict:        Optional[str]   # fact | unverified | fake | non_news
    confidence:     float
    explanation:    str
    sources:        list[dict[str, str]]


def _make_result(verdict: str, confidence: float, explanation: str, sources: list[dict]) -> dict:
    return {"verdict": verdict, "confidence": confidence, "explanation": explanation, "sources": sources}