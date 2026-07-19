import json
import os
import re
import asyncio
from groq import AsyncGroq

from backend.core.config import GROQ_MODEL, GROQ_API_KEY
from backend.agent_pipeline.state import AgentHAQQState
from backend.agent_pipeline.tools import execute_tool, GROQ_TOOLS

# Grab the first API key from the environment, fallback to GROQ_API_KEY
api_key = os.environ.get("GROQ_API_KEY_1", GROQ_API_KEY)

client = AsyncGroq(api_key=api_key)

async def agent_node(state: AgentHAQQState) -> AgentHAQQState:
    claim = state["text"]
    content_type = state.get("content_type", "news")
    keywords = state.get("keywords", claim)
    
    system_msg = {
        "role": "system",
        "content": f"""You are a professional fact-checking and news-verification agent.
Your task is to verify a claim. 
The claim has been categorized as: {content_type}
Extracted keywords for search: {keywords}

Use the provided tools to gather evidence. 
Keep searching until you have a solid conclusion. You can fetch article bodies if needed.
Once you are ready to decide, your final message MUST be a JSON object containing:
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
    
    max_iterations = 7
    final_message_content = ""
    
    called_tool_signatures = set()
    
    for _ in range(max_iterations):
        response = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=GROQ_TOOLS,
            tool_choice="auto",
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
                sig = (fn_name, args_str)
                
                if sig in called_tool_signatures:
                    return {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn_name,
                        "content": "Error: You have already called this tool with these exact arguments. Do not repeat failed or identical tool calls. Please try a different approach."
                    }
                called_tool_signatures.add(sig)
                
                try:
                    arguments = json.loads(args_str)
                except Exception:
                    arguments = {}
                    
                tool_result = await execute_tool(fn_name, arguments)
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
