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
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODELS, MAX_SEARCH_ITERATIONS, GEMINI_THINKING_LEVEL

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
                cfg = {"thinking_config": {"thinking_level": GEMINI_THINKING_LEVEL}}
                if system:
                    cfg["system_instruction"] = system
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=cfg,
                )
                return response.candidates[0].content.parts[0].text
            except Exception as e:
                err = str(e)
                if "429" in err or "quota" in err.lower():
                    if "PerDay" in err or "per_day" in err.lower():
                        print(f"[gemini] Daily quota exhausted for {model_name}, trying next model...")
                        break
                    print(f"[gemini] RPM limit hit on {model_name}, waiting 60s...")
                    time.sleep(60)
                elif "503" in err or "UNAVAILABLE" in err:
                    print(f"[gemini] {model_name} unavailable, waiting 60s...")
                    time.sleep(60)
                else:
                    print(f"[gemini] Error with {model_name}: {e}")
                    break
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
        messages = [types.Content(role="user", parts=[types.Part(text=prompt)])]

        for iteration in range(max_iterations):
            response = None
            for attempt in range(3):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=messages,
                        config={
                            "system_instruction": system,
                            "tools": [{"function_declarations": tool_schema}],
                            "automatic_function_calling": {"disable": True},
                            "thinking_config": {"thinking_level": GEMINI_THINKING_LEVEL},
                        }
                    )
                    break
                except Exception as e:
                    err = str(e)
                    if "429" in err or "quota" in err.lower():
                        if "PerDay" in err or "per_day" in err.lower():
                            print(f"[gemini] Daily quota exhausted for {model_name}, trying next model...")
                            fatal = True
                            break
                        print(f"[gemini] RPM limit hit on {model_name}, waiting 60s...")
                        time.sleep(60)
                    elif "503" in err or "UNAVAILABLE" in err:
                        print(f"[gemini] {model_name} unavailable, waiting 60s...")
                        time.sleep(60)
                    else:
                        # Surface unexpected errors and move to the next model.
                        print(f"[gemini] Tool-loop error on {model_name}: {e}")
                        break

            if response is None:
                # All attempts failed (rate-limited out or fatal) — try the next model.
                break

            candidate = response.candidates[0]
            parts = candidate.content.parts or []

            tool_calls = [
                p.function_call for p in parts
                if getattr(p, "function_call", None) and p.function_call.name
            ]

            if not tool_calls:
                text_parts = [p.text for p in parts if getattr(p, "text", None)]
                return "\n".join(text_parts).strip()

            # Append the model's turn (with its function-call parts) to history,
            # then reply with a function_response part for each call.
            messages.append(candidate.content)
            response_parts = []
            for fc in tool_calls:
                print(f"[gemini] Tool call: {fc.name}({dict(fc.args)})")
                result = tool_executor(fc.name, dict(fc.args))
                response_parts.append(
                    types.Part.from_function_response(
                        name=fc.name, response={"result": result}
                    )
                )
            messages.append(types.Content(role="user", parts=response_parts))

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