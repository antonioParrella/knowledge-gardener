"""
Tier 1 — the guards that keep a cheap failure from destroying an expensive run.

Both of these exist because of one incident (2026-07-27): a MOC-assignment call
on the OpenRouter fallback burned its entire default output budget reasoning, hit
the cap, and was cut off. The router treats truncation as a hard provider error,
so a $0.35, 38-minute comprehensive callout died nine minutes in — during phase ①,
before it had gathered a single source.
"""

import config
from researcher.pipeline import _best_effort


# ── The routing cap (the proximate cause) ────────────────────────────────────────

def test_cheap_openrouter_fallbacks_have_an_output_ceiling():
    """
    clip/moc normally run on free Gemini, so their OpenRouter entry only fires
    once both free buckets are spent — which is why it went unnoticed without an
    output cap.

    Scoped to the cheap tasks deliberately. The `research` discovery loop is also
    uncapped on OpenRouter, but capping it is a quality trade (a truncated turn
    on a legitimate xhigh-reasoning step is worse than the runaway it prevents),
    so that one is an open decision rather than an oversight.
    """
    for task in ("clip", "moc"):
        for provider, model, opts in config.ROUTING[task]:
            if provider != "openrouter":
                continue
            assert opts.get("max_tokens"), (
                f"{task} → {model} has no max_tokens; a reasoning model can spend "
                f"its whole default budget thinking and get truncated"
            )


def test_cheap_tasks_bound_their_reasoning():
    """Classification does not need extended reasoning — that's what ran away."""
    for task in ("clip", "moc"):
        for provider, _model, opts in config.ROUTING[task]:
            if provider == "openrouter":
                assert opts.get("reasoning_effort") == "minimal"


def test_the_cheap_cap_is_well_under_the_observed_runaway():
    """65,537 tokens was the runaway. The cap must leave real room for an answer
    while making that impossible."""
    assert 4000 <= config.CHEAP_MAX_OUTPUT_TOKENS <= 32000
    assert config.CHEAP_MAX_OUTPUT_TOKENS < 65537


# ── Best-effort prior knowledge (the defence in depth) ───────────────────────────

def test_best_effort_returns_the_result_when_it_works():
    assert _best_effort("x", lambda: [{"title": "A"}]) == [{"title": "A"}]


def test_best_effort_swallows_the_failure_and_yields_no_prior_knowledge():
    def boom():
        raise RuntimeError("All providers failed for task 'moc': truncated")

    assert _best_effort("clippings", boom) == []


def test_best_effort_normalises_none_to_empty():
    """Downstream code branches on truthiness, so None must not leak through."""
    assert _best_effort("x", lambda: None) == []


def test_best_effort_forwards_arguments():
    got = {}

    def fn(topic, exclude_title=None):
        got.update(topic=topic, exclude_title=exclude_title)
        return []

    _best_effort("prior research", fn, "Diffusion models", exclude_title="Research - X")
    assert got == {"topic": "Diffusion models", "exclude_title": "Research - X"}
