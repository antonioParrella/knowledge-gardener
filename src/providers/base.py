"""
base.py — Provider interface, control-flow exceptions, and shared JSON parsing.

A provider wraps one vendor's SDK and exposes two methods:
  - simple()     single-turn text in / text out
  - tool_loop()  agentic function-calling loop

Both take an explicit `model` plus provider-specific `**opts` (e.g. DeepSeek's
reasoning_effort). To signal the router in llm.py, providers raise:
  - QuotaExhausted  this model is out of quota for the day → try the next chain entry
  - ProviderError   this model errored / is misconfigured → try the next chain entry

`parse_json_response` lives here because it is provider-neutral (the vendors all
return JSON the same way once you strip markdown fences).
"""

import json
import re
import sys
from abc import ABC, abstractmethod


def safe_print(msg: str) -> None:
    """
    print() that never raises on un-encodable characters.

    config.py reconfigures stdout to UTF-8, but as defense-in-depth (a crash here
    aborts a whole research run) we fall back to encoding with replacement if the
    active stdout still can't represent a character.
    """
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        sys.stdout.write(msg.encode(enc, errors="replace").decode(enc) + "\n")


class QuotaExhausted(Exception):
    """Raised when a model's quota is spent — the router should fall through."""


class ProviderError(Exception):
    """Raised on an unrecoverable model/provider error — the router falls through."""


class Provider(ABC):
    """Common interface every LLM provider implements."""

    @abstractmethod
    def simple(self, prompt: str, system: str = "", model: str | None = None, **opts) -> str:
        """Single-turn call. Returns the text response."""

    @abstractmethod
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
        """Agentic function-calling loop. Returns the final text response."""


def parse_json_response(text: str) -> dict:
    """
    Safely parse a JSON response from a model.
    Strips markdown fences if present.
    """
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}
