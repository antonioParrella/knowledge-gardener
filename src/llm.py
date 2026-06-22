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

from config import ROUTING, MAX_SEARCH_ITERATIONS
from providers.base import QuotaExhausted, ProviderError, parse_json_response  # noqa: F401 (re-export)

# Lazy provider singletons — OpenRouter is only constructed on first use, and
# only imports the openai SDK then, so the dependency stays optional.
_PROVIDERS = {}


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


def llm_simple(prompt: str, system: str = "", task: str = "clip") -> str:
    """Single-turn call routed by task, walking the fallback chain."""
    last_error = None
    for prov_name, model, opts in _chain(task):
        try:
            return _provider(prov_name).simple(prompt, system=system, model=model, **opts)
        except (QuotaExhausted, ProviderError) as e:
            print(f"[llm] {prov_name}/{model} unavailable for '{task}': {e} — trying next")
            last_error = e
    raise RuntimeError(f"All providers failed for task '{task}': {last_error}")


def llm_tool_loop(
    prompt: str,
    system: str,
    tool_schema: list[dict],
    tool_executor,
    task: str = "research",
    max_iterations: int = MAX_SEARCH_ITERATIONS,
) -> str:
    """Agentic tool loop routed by task, walking the fallback chain."""
    last_error = None
    for prov_name, model, opts in _chain(task):
        try:
            return _provider(prov_name).tool_loop(
                prompt, system, tool_schema, tool_executor,
                model=model, max_iterations=max_iterations, **opts,
            )
        except (QuotaExhausted, ProviderError) as e:
            print(f"[llm] {prov_name}/{model} unavailable for '{task}': {e} — trying next")
            last_error = e
    raise RuntimeError(f"All providers failed for task '{task}': {last_error}")
