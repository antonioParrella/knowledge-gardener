"""
Tier 1 — OpenRouter error classification + research preflight gate (pure logic,
no network). Covers:

  * a spent key/credit cap → QuotaExhausted (so the router cools the model down
    instead of hammering it every call — the runaway-loop bug), while other
    errors stay ProviderError;
  * research_preflight blocking only on a confident low-credit reading, failing
    open on an unreachable/uncapped key, and clearing the OpenRouter cooldown on
    a healthy reading so a just-raised limit resumes promptly.
"""

import time

import pytest

import llm
from providers.base import QuotaExhausted, ProviderError
from providers import openrouter


class _FakeAPIError(Exception):
    """Stand-in for an openai SDK APIStatusError carrying an HTTP status_code."""
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


# ── _classify ────────────────────────────────────────────────────────────────

def test_403_key_limit_is_quota_exhausted():
    e = _FakeAPIError("Error code: 403 - Key limit exceeded (total limit)", status_code=403)
    assert isinstance(openrouter._classify("deepseek/deepseek-v4-pro", e), QuotaExhausted)


def test_402_out_of_credits_is_quota_exhausted():
    e = _FakeAPIError("insufficient credit for this request", status_code=402)
    assert isinstance(openrouter._classify("m", e), QuotaExhausted)


def test_generic_403_is_provider_error():
    # A bad key / forbidden that is NOT a spend cap must not be parked as quota.
    e = _FakeAPIError("Error code: 403 - Invalid API key", status_code=403)
    assert isinstance(openrouter._classify("m", e), ProviderError)


def test_network_error_is_provider_error():
    assert isinstance(openrouter._classify("m", Exception("Connection reset")), ProviderError)


# ── research_preflight ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_router_state(monkeypatch):
    """Each case starts with a clean cache and cooldown table."""
    monkeypatch.setattr(llm, "_preflight_cache", None, raising=False)
    monkeypatch.setattr(llm, "_cooldown_until", {}, raising=False)
    yield


def _stub_key_status(monkeypatch, value):
    monkeypatch.setattr(openrouter, "key_status", lambda *a, **k: value)


def test_blocks_when_remaining_below_threshold(monkeypatch):
    _stub_key_status(monkeypatch, {"limit_remaining": 0.05, "limit": 5.0})
    ok, reason = llm.research_preflight(min_credits=0.20)
    assert ok is False
    assert "0.05" in reason and "0.20" in reason


def test_passes_when_remaining_above_threshold(monkeypatch):
    _stub_key_status(monkeypatch, {"limit_remaining": 4.0, "limit": 5.0})
    ok, reason = llm.research_preflight(min_credits=0.20)
    assert ok is True and reason == ""


def test_passes_when_no_cap(monkeypatch):
    _stub_key_status(monkeypatch, {"limit_remaining": None, "limit": None})
    ok, _ = llm.research_preflight(min_credits=0.20)
    assert ok is True


def test_fails_open_on_inconclusive_reading(monkeypatch):
    # /key unreachable (None) must not wedge research.
    _stub_key_status(monkeypatch, None)
    ok, _ = llm.research_preflight(min_credits=0.20)
    assert ok is True


def test_disabled_when_threshold_zero(monkeypatch):
    # Should not even poll when preflight is turned off.
    called = {"n": 0}
    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("key_status should not be called when disabled")
    monkeypatch.setattr(openrouter, "key_status", _boom)
    ok, _ = llm.research_preflight(min_credits=0)
    assert ok is True and called["n"] == 0


def test_healthy_reading_clears_openrouter_cooldown(monkeypatch):
    llm._cooldown_until[("openrouter", "deepseek/deepseek-v4-pro")] = time.time() + 9999
    llm._cooldown_until[("gemini", "gemini-3-flash-preview")] = time.time() + 9999
    _stub_key_status(monkeypatch, {"limit_remaining": 4.0})
    ok, _ = llm.research_preflight(min_credits=0.20)
    assert ok is True
    # OpenRouter un-parked by live evidence; Gemini's own cooldown untouched.
    assert not any(p == "openrouter" for (p, _m) in llm._cooldown_until)
    assert ("gemini", "gemini-3-flash-preview") in llm._cooldown_until


def test_inconclusive_reading_does_not_clear_cooldown(monkeypatch):
    llm._cooldown_until[("openrouter", "deepseek/deepseek-v4-pro")] = time.time() + 9999
    _stub_key_status(monkeypatch, None)
    llm.research_preflight(min_credits=0.20)
    assert ("openrouter", "deepseek/deepseek-v4-pro") in llm._cooldown_until


def test_result_is_cached(monkeypatch):
    calls = {"n": 0}
    def _counting(*a, **k):
        calls["n"] += 1
        return {"limit_remaining": 4.0}
    monkeypatch.setattr(openrouter, "key_status", _counting)
    llm.research_preflight(min_credits=0.20)
    llm.research_preflight(min_credits=0.20)
    assert calls["n"] == 1  # second call served from cache
