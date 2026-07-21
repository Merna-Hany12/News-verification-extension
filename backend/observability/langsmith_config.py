import os
from typing import Optional

def setup_langsmith() -> None:
    """
    Checks for LANGSMITH_API_KEY. If present, ensures LANGSMITH_TRACING=true
    is set so LangSmith automatically traces @traceable functions.
    """
    if os.environ.get("LANGSMITH_API_KEY"):
        os.environ["LANGSMITH_TRACING"] = "true"
        if not os.environ.get("LANGSMITH_PROJECT"):
            os.environ["LANGSMITH_PROJECT"] = "haqq-news-verification"
        print("[Observability] LangSmith tracing enabled.")
    else:
        # Silently disable if no key is found
        os.environ["LANGSMITH_TRACING"] = "false"
        print("[Observability] LangSmith tracing disabled (no API key).")

def get_langsmith_config(
    request_id: str,
    pipeline: str,
    content_type: Optional[str] = None
) -> dict:
    """
    Returns a RunnableConfig dictionary that can be passed to LangGraph.
    Includes metadata for filtering traces in the LangSmith UI.
    """
    return {
        "configurable": {
            "request_id": request_id,
            "pipeline": pipeline,
            "content_type": content_type
        },
        "metadata": {
            "request_id": request_id,
            "pipeline": pipeline,
            "content_type": content_type
        }
    }
