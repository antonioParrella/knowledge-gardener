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


# The escapes JSON actually defines. Anything else after a backslash is a parse
# error — which is precisely what a LaTeX command is.
_JSON_ESCAPES = set('"\\/bfnrtu')


def repair_invalid_escapes(text: str) -> str:
    r"""
    Escape backslashes that don't begin a valid JSON escape sequence.

    Clip analyses of papers carry maths, and a model writing `$\alpha$` or `\[ x \]`
    into a JSON string emits an invalid escape: `json.loads` rejects the whole
    response, `parse_json_response` returns {}, and the caller drops the document.
    That accounted for 45 silently-lost sources in one month of the live log, and 9
    of 29 attempts in the first full-text backfill run — full-text papers are the
    worst case precisely because they are the ones with maths in them.

    Only genuinely INVALID escapes are touched, so a response that already parses is
    never rewritten. Note the asymmetry that leaves behind: `\beta`, `\theta`,
    `\frac`, `\nu` and `\rho` ARE valid escapes (backspace, tab, formfeed, newline,
    carriage return), so they parse cleanly while silently eating the command. Telling
    those apart from an intended newline is not decidable from the text alone, so they
    are deliberately left for the prompt to prevent — see DESIGN_NOTES § Math rendering.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt not in _JSON_ESCAPES:
            out.append("\\\\")          # invalid escape -> a literal backslash
            i += 1
            continue
        if nxt == "u" and not re.match(r"[0-9a-fA-F]{4}", text[i + 2:i + 6]):
            out.append("\\\\")          # \u not followed by 4 hex digits
            i += 1
            continue
        out.append(ch)
        out.append(nxt)
        i += 2
    return "".join(out)


def parse_json_response(text: str) -> dict:
    """
    Safely parse a JSON response from a model.
    Strips markdown fences if present.

    Strict parsing is tried FIRST and the escape repair only on failure, so this can
    add recoveries but can never change how an already-valid response is read. A
    response truncated mid-structure stays unparseable, correctly — repair fixes
    malformed escapes, not missing text.
    """
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(repair_invalid_escapes(text))
    except json.JSONDecodeError:
        return {}
