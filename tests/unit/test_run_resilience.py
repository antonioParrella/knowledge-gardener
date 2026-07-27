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


def test_cheap_tasks_do_not_throttle_reasoning():
    """
    The fallback must not be dumber than the path it stands in for. Gemini runs
    clip/moc at thinking_level "high", and 52 of 53 observed calls on the
    OpenRouter fallback were fine — so throttling reasoning would degrade the
    many to prevent the rare outlier.
    """
    for task in ("clip", "moc"):
        for provider, _model, opts in config.ROUTING[task]:
            if provider == "openrouter":
                assert "reasoning_effort" not in opts


def test_the_cheap_cap_clears_the_observed_tail():
    """
    Sized off the measured distribution, not the mean. A legitimate
    find_relevant_clippings over the full MOC catalog ran to 16,635 output
    tokens; the runaway was 65,537. A ceiling between those is the whole design,
    and an earlier 16,000 would have truncated real work.
    """
    assert config.CHEAP_MAX_OUTPUT_TOKENS > 16635      # clears known-good work
    assert config.CHEAP_MAX_OUTPUT_TOKENS < 65537      # still bounds the runaway


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
