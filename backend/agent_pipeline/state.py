from typing import Annotated, Any
import operator
from backend.core.state import HAQQState

class AgentHAQQState(HAQQState):
    """
    Extended state for the agent-based pipeline.
    Tracks messages, tokens, and cost.
    """
    messages: Annotated[list[dict[str, Any]], operator.add]
    
    # Cost tracking
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    total_cost_usd: float
