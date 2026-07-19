from typing import Any
from langgraph.graph import END, StateGraph

from backend.agent_pipeline.state import AgentHAQQState
from backend.nodes.classify import classify_node
from backend.nodes.search import extract_keywords_node
from backend.agent_pipeline.agent_node import agent_node

def _after_classify(state: AgentHAQQState) -> str:
    if state.get("verdict"):
        return END
    content_type = state.get("content_type", "news")
    if content_type == "non_news":
        return "non_news_exit"
    return "extract_keywords"

def _after_keywords(state: AgentHAQQState) -> str:
    return END if state.get("verdict") else "agent_verify"

def build_agent_graph() -> Any:
    g = StateGraph(AgentHAQQState)

    def _non_news(state: AgentHAQQState) -> AgentHAQQState:
        return {
            **state,
            "verdict":     "non_news",
            "confidence":  state.get("non_news_score", 0.9),
            "explanation": "💬 هذا محتوى غير إخباري",
            "sources":     [],
        }

    g.add_node("classify",         classify_node)
    g.add_node("extract_keywords", extract_keywords_node)
    g.add_node("agent_verify",     agent_node)
    g.add_node("non_news_exit",    _non_news)

    g.set_entry_point("classify")
    g.add_conditional_edges("classify", _after_classify, {
        "non_news_exit":   "non_news_exit",
        "extract_keywords": "extract_keywords",
        END:               END,
    })
    g.add_edge("non_news_exit", END)
    g.add_conditional_edges("extract_keywords", _after_keywords, {
        "agent_verify": "agent_verify",
        END:            END,
    })
    g.add_edge("agent_verify", END)

    compiled = g.compile()
    print("[HAQQ Agent Graph] Graph compiled ✅")
    return compiled


async def run_agent_verify(graph, text: str, lang: str) -> dict:
    initial_state = {
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
        "messages":       [],
        "total_tokens":   0,
        "prompt_tokens":  0,
        "completion_tokens": 0,
        "total_cost_usd": 0.0,
    }
    final = await graph.ainvoke(initial_state)
    return {
        "verdict":     final.get("verdict",     "unverified"),
        "confidence":  final.get("confidence",  0.0),
        "explanation": final.get("explanation", ""),
        "sources":     final.get("sources",     []),
        "total_tokens": final.get("total_tokens", 0),
        "total_cost_usd": final.get("total_cost_usd", 0.0),
    }
