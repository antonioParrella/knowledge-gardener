"""
gemini_client.py — Gemini API wrapper.

Provides two call patterns:
  - gemini_simple()        Single-turn text in / text out
  - gemini_tool_loop()     Agentic loop with function calling

Automatically falls back through GEMINI_MODELS if rate limited.
"""

import json
import re
import time
from google.genai import Client
from config import GEMINI_API_KEY, GEMINI_MODELS, MAX_SEARCH_ITERATIONS

client = Client(api_key=GEMINI_API_KEY)


def _get_model(tools=None, system: str = ""):
    """Return a model wrapper, trying fallback models if needed."""
    for model_name in GEMINI_MODELS:
        try:
            model = client.models
            if tools:
                return model, model_name, tools
            return model, model_name, None
        except Exception:
            continue
    raise RuntimeError("All Gemini models unavailable.")


def gemini_simple(prompt: str, system: str = "") -> str:
    """
    Single-turn Gemini call. Returns the text response.
    Retries with exponential backoff on rate limit errors.
    """
    for model_name in GEMINI_MODELS:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={"system_instruction": system} if system else {}
                )
                return response.candidates[0].content.parts[0].text
            except Exception as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    wait = 2 ** attempt * 5
                    print(f"[gemini] Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    continue
    raise RuntimeError("Gemini simple call failed after retries.")


def gemini_tool_loop(
    prompt: str,
    system: str,
    tool_schema: list[dict],
    tool_executor,
    max_iterations: int = MAX_SEARCH_ITERATIONS,
) -> str:
    """
    Agentic function-calling loop.

    Sends prompt to Gemini with tools available. When Gemini calls a tool,
    tool_executor is called with (tool_name, args_dict) and the result is
    sent back. Loop continues until Gemini returns a plain text response
    or max_iterations is reached.

    Returns the final text response.
    """
    for model_name in GEMINI_MODELS:
        messages = [{"role": "user", "parts": [{"text": prompt}]}]

        for iteration in range(max_iterations):
            for attempt in range(3):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=messages,
                        config={
                            "system_instruction": system,
                            "tools": tool_schema,
                        }
                    )
                    break
                except Exception as e:
                    if "429" in str(e) or "quota" in str(e).lower():
                        wait = 2 ** attempt * 5
                        print(f"[gemini] Rate limited on iteration {iteration}, waiting {wait}s...")
                        time.sleep(wait)
                    else:
                        continue
            else:
                return "Research interrupted due to repeated rate limiting."

            candidate = response.candidates[0]
            parts = candidate.content.parts

            tool_calls = [p.function_call for p in parts if hasattr(p, "function_call") and p.function_call.name]

            if not tool_calls:
                text_parts = [p.text for p in parts if hasattr(p, "text")]
                return "\n".join(text_parts).strip()

            messages.append({"role": "model", "parts": [p for p in parts if hasattr(p, "function_call")]})
            tool_results = []
            for fc in tool_calls:
                print(f"[gemini] Tool call: {fc.name}({dict(fc.args)})")
                result = tool_executor(fc.name, dict(fc.args))
                tool_results.append({
                    "role": "tool",
                    "parts": [{"text": result}],
                })
            messages.append({"role": "user", "parts": tool_results})

        return "Research timed out after maximum iterations."

    raise RuntimeError("All Gemini models unavailable.")


def parse_json_response(text: str) -> dict:
    """
    Safely parse a JSON response from Gemini.
    Strips markdown fences if present.
    """
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}