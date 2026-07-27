"""
llm.py — Task-routed LLM facade.

The pipeline calls `llm_simple` / `llm_tool_loop` with a `task` ("clip", "moc",
"research"). The router looks up that task's chain in config.ROUTING and tries
each (provider, model, opts) in order, falling through on QuotaExhausted /
ProviderError (which also covers an unconfigured OpenRouter key). This keeps free
Gemini on the high-volume cheap tasks and routes research to DeepSeek V4 Pro
(via OpenRouter) at max reasoning — see AGENTS.md for the rationale.

`parse_json_response` is re-exported here so call sites have a single import.
"""

import time

from config import (
    ROUTING, MAX_SEARCH_ITERATIONS, QUOTA_COOLDOWN_SECS,
    RESEARCH_MIN_KEY_CREDITS, PREFLIGHT_CACHE_SECS,
)
from providers.base import QuotaExhausted, ProviderError, parse_json_response  # noqa: F401 (re-export)

# Lazy provider singletons — OpenRouter is only constructed on first use, and
# only imports the openai SDK then, so the dependency stays optional.
_PROVIDERS = {}

# Circuit breaker: (provider, model) -> epoch until which the model is treated as
# quota-exhausted and skipped. A single daily free-tier cap (limit: 20 on Gemini
# Flash) otherwise makes every clip/moc call re-attempt a doomed request. Tripped
# by QuotaExhausted, in-memory (resets on restart), keyed on model so a model
# shared across tasks is skipped everywhere once one task trips it.
_cooldown_until: dict[tuple[str, str], float] = {}


def _is_cooling(prov_name: str, model: str) -> bool:
    until = _cooldown_until.get((prov_name, model))
    return until is not None and time.time() < until


def _clear_cooldown(prov_name: str) -> None:
    """Un-park every model of a provider — used when preflight has live evidence
    the provider is usable again, so a stale timer doesn't outlive the outage."""
    for key in [k for k in _cooldown_until if k[0] == prov_name]:
        del _cooldown_until[key]


# Preflight result cache: (expiry_epoch, ok, reason). The /key poll is shared
# across a whole rescan batch instead of run per note.
_preflight_cache: tuple[float, bool, str] | None = None


def research_preflight(min_credits: float = RESEARCH_MIN_KEY_CREDITS) -> tuple[bool, str]:
    """
    Gate an OpenRouter-only run (research / concept / callout synthesis) on the
    key's live remaining credit. Returns (ok, reason); reason is "" when ok.

    Blocks ONLY on a confident low-credit reading. An unset key, a key with no
    spend cap, or an unreachable /key endpoint all pass (fail-open) so a transient
    blip can't wedge all research — a genuine outage is still caught by the
    per-call cooldown at synthesis time. A reading that shows usable credit clears
    any OpenRouter cooldown, so a just-raised limit resumes on the next rescan
    instead of waiting out a stale 30-minute timer.
    """
    global _preflight_cache
    if min_credits <= 0:
        return True, ""

    now = time.time()
    if _preflight_cache and now < _preflight_cache[0]:
        return _preflight_cache[1], _preflight_cache[2]

    from providers.openrouter import key_status
    status = key_status()

    ok, reason = True, ""
    if status is not None:
        remaining = status.get("limit_remaining")
        if remaining is not None and remaining < min_credits:
            ok = False
            reason = (
                f"OpenRouter key credit ${remaining:.2f} < ${min_credits:.2f} required — "
                f"research paused until the key limit is raised or usage resets"
            )
        else:
            # Live evidence the key is usable (credit above threshold, or no cap).
            _clear_cooldown("openrouter")

    _preflight_cache = (now + PREFLIGHT_CACHE_SECS, ok, reason)
    return ok, reason


def _provider(name: str):
    if name not in _PROVIDERS:
        if name == "gemini":
            from providers.gemini import GeminiProvider
            _PROVIDERS[name] = GeminiProvider()
        elif name == "openrouter":
            from providers.openrouter import OpenRouterProvider
            _PROVIDERS[name] = OpenRouterProvider()
        else:
            raise ValueError(f"Unknown provider: {name}")
    return _PROVIDERS[name]


def _chain(task: str) -> list[tuple]:
    chain = ROUTING.get(task)
    if not chain:
        raise ValueError(f"No routing configured for task: {task}")
    return chain


def _run(task: str, invoke):
    """
    Walk a task's fallback chain, skipping models currently in quota cooldown.

    Entries not cooling down are tried first; cooling ones are appended as a last
    resort so a chain whose every entry is cooling still gets attempted (a quota
    may have reset). `invoke(prov_name, model, opts)` performs the actual call.
    """
    chain = _chain(task)
    active = [e for e in chain if not _is_cooling(e[0], e[1])]
    ordered = active + [e for e in chain if e not in active]

    last_error = None
    for prov_name, model, opts in ordered:
        try:
            return invoke(prov_name, model, opts)
        except QuotaExhausted as e:
            _cooldown_until[(prov_name, model)] = time.time() + QUOTA_COOLDOWN_SECS
            print(f"[llm] {prov_name}/{model} quota exhausted for '{task}' — "
                  f"cooling down {QUOTA_COOLDOWN_SECS}s, trying next")
            last_error = e
        except ProviderError as e:
            print(f"[llm] {prov_name}/{model} unavailable for '{task}': {e} — trying next")
            last_error = e
    raise RuntimeError(f"All providers failed for task '{task}': {last_error}")


def llm_simple(prompt: str, system: str = "", task: str = "clip") -> str:
    """Single-turn call routed by task, walking the fallback chain."""
    return _run(
        task,
        lambda prov_name, model, opts: _provider(prov_name).simple(
            prompt, system=system, model=model, **opts,
        ),
    )


def llm_tool_loop(
    prompt: str,
    system: str,
    tool_schema: list[dict],
    tool_executor,
    task: str = "research",
    max_iterations: int = MAX_SEARCH_ITERATIONS,
) -> str:
    """Agentic tool loop routed by task, walking the fallback chain."""
    return _run(
        task,
        lambda prov_name, model, opts: _provider(prov_name).tool_loop(
            prompt, system, tool_schema, tool_executor,
            model=model, max_iterations=max_iterations, **opts,
        ),
    )
