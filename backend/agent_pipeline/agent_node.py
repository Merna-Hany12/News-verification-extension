import json
import os
import re
import asyncio
from collections import defaultdict
from groq import AsyncGroq

from backend.core.config import GROQ_MODEL, GROQ_API_KEY
from backend.agent_pipeline.state import AgentHAQQState
from backend.agent_pipeline.tools import execute_tool, GROQ_TOOLS

# Grab the first API key from the environment, fallback to GROQ_API_KEY
api_key = os.environ.get("GROQ_API_KEY_1", GROQ_API_KEY)

client = AsyncGroq(api_key=api_key)

# ─── Per-tool call limits ────────────────────────────────────────────────────
# Each search tool can be called at most MAX_CALLS_PER_SEARCH times (initial +
# one refined query). fetch_article_body_tool has its own separate budget.
MAX_CALLS_PER_SEARCH = 2
MAX_FETCH_BODY_CALLS = 2

# Maximum number of times a specific tool can fail before it's blocked
MAX_FAILURES_PER_TOOL = 2

# ─── Tool strategy per content type ─────────────────────────────────────────
# Injected into the system prompt so the LLM knows the optimal tool order.
TOOL_STRATEGY = {
    "news": (
        "TOOL STRATEGY (news claim):\n"
        "1. Start with search_google_rss_tool to find news coverage.\n"
        "2. If insufficient results, use search_news_apis_tool as a second source.\n"
        "3. Use search_duckduckgo_tool ONLY as a last resort if both above return nothing.\n"
        "4. Do NOT use search_pubmed_tool — this is not a medical claim.\n"
        "5. Use fetch_article_body_tool only if a snippet is too short to verify (max 2 times)."
    ),
    "medical": (
        "TOOL STRATEGY (medical claim):\n"
        "1. Start with search_pubmed_tool to find peer-reviewed evidence.\n"
        "2. Then use search_duckduckgo_tool for WHO/CDC/Mayo Clinic sources.\n"
        "3. Optionally use search_google_rss_tool for recent medical news coverage.\n"
        "4. Do NOT use search_news_apis_tool — it rarely has medical research.\n"
        "5. Use fetch_article_body_tool only if a snippet is too short to verify (max 2 times)."
    ),
    "historical_scientific": (
        "TOOL STRATEGY (historical/scientific claim):\n"
        "1. Start with search_duckduckgo_tool — best for encyclopedic and scientific content.\n"
        "2. Then use search_google_rss_tool for journalistic coverage of the topic.\n"
        "3. Do NOT use search_news_apis_tool or search_pubmed_tool.\n"
        "4. Use fetch_article_body_tool only if a snippet is too short to verify (max 2 times)."
    ),
}


def _normalize_query(args_str: str) -> str:
    """
    Normalize tool arguments for duplicate detection.

    The LLM often bypasses exact-match duplicate detection by slightly tweaking
    queries (extra spaces, different punctuation, reordered words). This function
    normalizes to catch those cases.
    """
    try:
        args = json.loads(args_str)
    except Exception:
        return args_str.strip().lower()

    query = args.get("query", args.get("url", ""))
    # Lowercase, collapse whitespace, strip punctuation
    normalized = re.sub(r'[^\w\s]', '', str(query).lower())
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


async def agent_node(state: AgentHAQQState) -> AgentHAQQState:
    claim = state["text"]
    content_type = state.get("content_type", "news")
    keywords = state.get("keywords", claim)

    # Select the right tool strategy; fall back to news if unknown
    strategy = TOOL_STRATEGY.get(content_type, TOOL_STRATEGY["news"])

    system_msg = {
        "role": "system",
        "content": f"""You are a professional fact-checking and news-verification agent.
Your task is to verify a claim. 
The claim has been categorized as: {content_type}
Extracted keywords for search: {keywords}

{strategy}

IMPORTANT RULES:
- Do NOT call the same tool with the same or very similar query twice.
- Each search tool can be used at most {MAX_CALLS_PER_SEARCH} times total.
- fetch_article_body_tool can be used at most {MAX_FETCH_BODY_CALLS} times total.
- If a tool returns an error, try a DIFFERENT tool instead of retrying the same one.
- Stop searching once you have enough evidence (2-3 relevant sources is sufficient).
- You have a maximum of 5 tool-calling rounds. Use them wisely.

Once you have gathered enough evidence, your final message MUST be a JSON object containing:
{{
  "verdict": "fact" | "unverified" | "fake" | "non_news",
  "confidence": <float between 0.0 and 1.0>,
  "explanation": "<short Arabic explanation of the findings>",
  "sources": [
     {{"url": "...", "title": "..."}}
  ]
}}
Ensure the JSON is valid and complete. Do not add any text outside the JSON in your final message.
"""
    }
    
    user_msg = {
        "role": "user",
        "content": f"Please verify this claim:\n{claim}"
    }
    
    messages = [system_msg, user_msg]
    
    total_prompt_tokens = 0
    total_completion_tokens = 0
    
    max_iterations = 5
    final_message_content = ""
    
    # ─── Tracking state ──────────────────────────────────────────────────────
    # Normalized signatures of already-called tool invocations (for dedup)
    called_signatures: set[tuple[str, str]] = set()
    # How many times each tool has been called
    tool_call_counts: dict[str, int] = defaultdict(int)
    # How many times each tool has returned an error
    tool_failure_counts: dict[str, int] = defaultdict(int)
    
    for iteration in range(max_iterations):
        # On the final iteration, force the LLM to produce a verdict
        # instead of wasting the last turn on another tool call
        is_final_iteration = (iteration == max_iterations - 1)
        
        response = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=GROQ_TOOLS,
            tool_choice="none" if is_final_iteration else "auto",
            temperature=0.1
        )
        
        message = response.choices[0].message
        
        # Add usage stats
        if response.usage:
            total_prompt_tokens += response.usage.prompt_tokens
            total_completion_tokens += response.usage.completion_tokens
            
        assistant_message = {
            "role": "assistant",
            "content": message.content
        }
        
        if message.tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                } for tc in message.tool_calls
            ]
            messages.append(assistant_message)
            
            async def run_tool(tc):
                fn_name = tc.function.name
                args_str = tc.function.arguments
                
                # ── Check: tool blocked due to repeated failures ─────────
                if tool_failure_counts[fn_name] >= MAX_FAILURES_PER_TOOL:
                    return {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn_name,
                        "content": (
                            f"Error: {fn_name} has failed {MAX_FAILURES_PER_TOOL} times and is now "
                            f"unavailable. Please use a different tool to continue."
                        )
                    }
                
                # ── Check: per-tool call limit ───────────────────────────
                limit = MAX_FETCH_BODY_CALLS if fn_name == "fetch_article_body_tool" else MAX_CALLS_PER_SEARCH
                if tool_call_counts[fn_name] >= limit:
                    return {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn_name,
                        "content": (
                            f"Error: {fn_name} has already been called {limit} times (maximum reached). "
                            f"Use a different tool or produce your final verdict now."
                        )
                    }
                
                # ── Check: normalized duplicate detection ────────────────
                normalized = _normalize_query(args_str)
                sig = (fn_name, normalized)
                
                if sig in called_signatures:
                    return {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn_name,
                        "content": (
                            "Error: You have already called this tool with a very similar query. "
                            "Do not repeat tool calls. Try a DIFFERENT tool or produce your final verdict."
                        )
                    }
                called_signatures.add(sig)
                tool_call_counts[fn_name] += 1
                
                try:
                    arguments = json.loads(args_str)
                except Exception:
                    arguments = {}
                    
                tool_result = await execute_tool(fn_name, arguments)
                
                # Track failures (results starting with common error prefixes)
                if tool_result.startswith(("Error:", "Failed", "Could not")):
                    tool_failure_counts[fn_name] += 1
                
                return {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": tool_result
                }
            
            # Run all tools in parallel
            tool_results = await asyncio.gather(*(run_tool(tc) for tc in message.tool_calls))
            messages.extend(tool_results)
        else:
            messages.append(assistant_message)
            final_message_content = message.content or ""
            break

    if not final_message_content and messages and messages[-1].get("role") == "assistant":
        final_message_content = messages[-1].get("content") or ""
        
    total_tokens = total_prompt_tokens + total_completion_tokens
    # Llama 3.3 70b pricing (Groq): $0.59 / 1M prompt, $0.79 / 1M completion
    cost = (total_prompt_tokens / 1_000_000) * 0.59 + (total_completion_tokens / 1_000_000) * 0.79
    
    try:
        # Extract json if wrapped in markdown
        match = re.search(r'\{.*\}', final_message_content, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
        else:
            parsed = json.loads(final_message_content)
            
        verdict = parsed.get("verdict", "unverified")
        confidence = float(parsed.get("confidence", 0.0))
        explanation = parsed.get("explanation", "Could not verify.")
        sources = parsed.get("sources", [])
    except Exception as e:
        verdict = "unverified"
        confidence = 0.0
        explanation = f"⚠️ Agent failed to return valid JSON: {e}"
        sources = []
        
    return {
        **state,
        "verdict": verdict,
        "confidence": confidence,
        "explanation": explanation,
        "sources": sources,
        "total_tokens": total_tokens,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_cost_usd": cost,
        "messages": messages
    }
