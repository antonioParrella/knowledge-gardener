"""
gemini.py — Google Gemini provider (google-genai SDK).

Ported from gemini_client.py. Each call targets a single model; cross-model /
cross-provider fallback is handled by the router in llm.py. This provider only
retries transient per-minute (RPM) and 503 conditions internally, and raises
QuotaExhausted on daily-quota exhaustion so the router falls through.
"""

import time

from google.genai import Client
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_THINKING_LEVEL
from .base import Provider, QuotaExhausted, ProviderError, safe_print

client = Client(api_key=GEMINI_API_KEY)

_RPM_WAIT_SECS = 60
_MAX_RPM_RETRIES = 3


def _classify(err: Exception):
    """Map an exception to a control-flow exception, or None to retry-after-wait."""
    msg = str(err)
    low = msg.lower()
    if "429" in msg or "quota" in low:
        if "perday" in low or "per_day" in low:
            return QuotaExhausted(msg)
        return None  # RPM limit — caller should wait and retry
    if "503" in msg or "UNAVAILABLE" in msg:
        return None  # transient — wait and retry
    return ProviderError(msg)


class GeminiProvider(Provider):
    def simple(self, prompt: str, system: str = "", model: str | None = None, **opts) -> str:
        for attempt in range(_MAX_RPM_RETRIES):
            try:
                cfg = {"thinking_config": {"thinking_level": GEMINI_THINKING_LEVEL}}
                if system:
                    cfg["system_instruction"] = system
                response = client.models.generate_content(
                    model=model, contents=prompt, config=cfg,
                )
                return response.candidates[0].content.parts[0].text
            except Exception as e:
                control = _classify(e)
                if control is not None:
                    raise control from e
                print(f"[gemini] {model} transient limit, waiting {_RPM_WAIT_SECS}s...")
                time.sleep(_RPM_WAIT_SECS)
        raise ProviderError(f"{model}: retries exhausted")

    def tool_loop(
        self,
        prompt: str,
        system: str,
        tool_schema: list[dict],
        tool_executor,
        model: str | None = None,
        max_iterations: int = 15,
        **opts,
    ) -> str:
        messages = [types.Content(role="user", parts=[types.Part(text=prompt)])]

        for _iteration in range(max_iterations):
            response = None
            for attempt in range(_MAX_RPM_RETRIES):
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=messages,
                        config={
                            "system_instruction": system,
                            "tools": [{"function_declarations": tool_schema}],
                            "automatic_function_calling": {"disable": True},
                            "thinking_config": {"thinking_level": GEMINI_THINKING_LEVEL},
                        },
                    )
                    break
                except Exception as e:
                    control = _classify(e)
                    if control is not None:
                        raise control from e
                    print(f"[gemini] {model} transient limit, waiting {_RPM_WAIT_SECS}s...")
                    time.sleep(_RPM_WAIT_SECS)

            if response is None:
                raise ProviderError(f"{model}: tool-loop retries exhausted")

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
                safe_print(f"[gemini] Tool call: {fc.name}({dict(fc.args)})")
                result = tool_executor(fc.name, dict(fc.args))
                response_parts.append(
                    types.Part.from_function_response(
                        name=fc.name, response={"result": result}
                    )
                )
            messages.append(types.Content(role="user", parts=response_parts))

        return "Research timed out after maximum iterations."
