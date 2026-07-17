"""
openrouter.py — OpenRouter provider via the OpenAI-compatible SDK.

OpenRouter is a single OpenAI-wire gateway to many models, so this uses the
`openai` client with OpenRouter's base_url and namespaced model slugs
(e.g. "deepseek/deepseek-v4-pro"). Two OpenRouter specifics are handled here:

  - Reasoning: OpenRouter normalizes reasoning into a `reasoning` object
    (extra_body), e.g. {"reasoning": {"effort": "xhigh"}}. Valid efforts are
    minimal/low/medium/high/xhigh — NOT "max" (rejected). Do NOT also send the
    flat `reasoning_effort` field; sending both 400s.
  - reasoning round-trip: across tool-call turns OpenRouter expects the assistant
    message's `reasoning_details` (preferred) or `reasoning` echoed back, or the
    next request loses reasoning context. We capture and resend it.

Any error (including a missing API key) raises ProviderError so the router in
llm.py falls through to the next chain entry.
"""

import json

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL
from .base import Provider, ProviderError, safe_print

_client = None


def _get_client():
    global _client
    if not OPENROUTER_API_KEY:
        raise ProviderError("OPENROUTER_API_KEY not set")
    if _client is None:
        from openai import OpenAI  # imported lazily so the dep is optional
        _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    return _client


def _build_messages(prompt: str, system: str) -> list[dict]:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _reasoning_extra_body(opts: dict) -> dict | None:
    """Translate routing opts into OpenRouter's unified `reasoning` object."""
    effort = opts.get("reasoning_effort")
    if not effort:
        return None
    return {"reasoning": {"effort": effort}}


def _to_openai_tools(tool_schema: list[dict]) -> list[dict]:
    """Adapt the canonical TOOL_SCHEMA (name/description/parameters) to OpenAI form."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["parameters"],
            },
        }
        for t in tool_schema
    ]


def _check_complete(choice, model):
    """
    Raise if the model was cut off mid-generation.

    finish_reason == "length" means the response hit the output cap, which for a
    synthesis pass yields a report that ends mid-sentence. Silently accepting it
    writes a truncated report into the vault, so treat it as a provider error and
    let the router fall through to the next chain entry.
    """
    if getattr(choice, "finish_reason", None) == "length":
        raise ProviderError(f"{model}: response truncated (hit output token limit)")


def _get_field(msg, key):
    """Read a field that may be a typed attr or an extra (unknown) field on the SDK model."""
    val = getattr(msg, key, None)
    if val is None:
        extra = getattr(msg, "model_extra", None)
        if extra:
            val = extra.get(key)
    return val


class OpenRouterProvider(Provider):
    def simple(self, prompt: str, system: str = "", model: str | None = None, **opts) -> str:
        try:
            kwargs = {}
            extra = _reasoning_extra_body(opts)
            if extra:
                kwargs["extra_body"] = extra
            resp = _get_client().chat.completions.create(
                model=model,
                messages=_build_messages(prompt, system),
                **kwargs,
            )
            _check_complete(resp.choices[0], model)
            return resp.choices[0].message.content or ""
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"{model}: {e}") from e

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
        client = _get_client()
        messages = _build_messages(prompt, system)
        tools = _to_openai_tools(tool_schema)
        extra = _reasoning_extra_body(opts)
        kwargs = {"extra_body": extra} if extra else {}

        try:
            for _iteration in range(max_iterations):
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    **kwargs,
                )
                msg = resp.choices[0].message

                if not msg.tool_calls:
                    _check_complete(resp.choices[0], model)
                    return (msg.content or "").strip()

                # Echo the assistant turn back, preserving reasoning across tool
                # calls — OpenRouter wants reasoning_details (preferred) or reasoning.
                assistant_msg = {"role": "assistant", "content": msg.content or ""}
                reasoning_details = _get_field(msg, "reasoning_details")
                if reasoning_details:
                    assistant_msg["reasoning_details"] = reasoning_details
                else:
                    reasoning = _get_field(msg, "reasoning")
                    if reasoning:
                        assistant_msg["reasoning"] = reasoning
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ]
                messages.append(assistant_msg)

                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    safe_print(f"[openrouter] Tool call: {tc.function.name}({args})")
                    result = tool_executor(tc.function.name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

            return "Research timed out after maximum iterations."
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"{model}: {e}") from e
