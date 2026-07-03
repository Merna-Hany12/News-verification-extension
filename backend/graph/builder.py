from typing import Any
from langgraph.graph import END, StateGraph

from backend.core.state import HAQQState
from backend.nodes.classify import classify_node
from backend.nodes.search import (
    extract_keywords_node,
    search_node,
    fetch_bodies_node,
)
from backend.nodes.verify import llm_verify_node, score_node


def _after_classify(state: HAQQState) -> str:
    if state.get("verdict"):
        return END
    content_type = state.get("content_type", "news")
    if content_type == "non_news":
        return "non_news_exit"
    # Both "news" and "historical_scientific" go to keyword extraction
    return "extract_keywords"


def _after_keywords(state: HAQQState) -> str:
    return END if state.get("verdict") else "search"


def _after_search(state: HAQQState) -> str:
    return END if state.get("verdict") else "fetch_bodies"


def _after_bodies(state: HAQQState) -> str:
    return "llm_verify"


def build_graph() -> Any:
    g = StateGraph(HAQQState)

    def _non_news(state: HAQQState) -> HAQQState:
        return {
            **state,
            "verdict":     "non_news",
            "confidence":  state.get("non_news_score", 0.9),
            "explanation": "💬 هذا محتوى غير إخباري",
            "sources":     [],
        }

    g.add_node("classify",         classify_node)
    g.add_node("extract_keywords", extract_keywords_node)
    g.add_node("search",           search_node)
    g.add_node("fetch_bodies",     fetch_bodies_node)
    g.add_node("llm_verify",       llm_verify_node)
    g.add_node("score",            score_node)
    g.add_node("non_news_exit",    _non_news)

    g.set_entry_point("classify")
    g.add_conditional_edges("classify", _after_classify, {
        "non_news_exit":   "non_news_exit",
        "extract_keywords": "extract_keywords",
        END:               END,
    })
    g.add_edge("non_news_exit", END)
    g.add_conditional_edges("extract_keywords", _after_keywords, {
        "search": "search",
        END:      END,
    })
    g.add_conditional_edges("search", _after_search, {
        "fetch_bodies": "fetch_bodies",
        END:            END,
    })
    g.add_conditional_edges("fetch_bodies", _after_bodies, {
        "llm_verify": "llm_verify",
    })
    g.add_edge("llm_verify", "score")
    g.add_edge("score",      END)

    compiled = g.compile()
    print("[HAQQ graph] Graph compiled ✅  (v2 — 3-class + DDG + body fetch)")
    return compiled


async def run_verify(graph, text: str, lang: str) -> dict:
    initial_state: HAQQState = {
        "text":           text,
        "lang":           lang,
        "content_type":   None,
        "is_news":        None,
        "news_score":     0.0,
        "non_news_score":  0.0,
        "keywords":       None,
        "api_query":      None,
        "search_query":   None,
        "articles":       [],
        "bodies_fetched": False,
        "llm_verdict":    None,
        "llm_reasoning":  None,
        "verdict":        None,
        "confidence":     0.0,
        "explanation":    "",
        "sources":        [],
    }
    final = await graph.ainvoke(initial_state)
    return {
        "verdict":     final.get("verdict",     "unverified"),
        "confidence":  final.get("confidence",  0.0),
        "explanation": final.get("explanation", ""),
        "sources":     final.get("sources",     []),
    }
