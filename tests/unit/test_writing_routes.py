"""The quality/cost contract: premium final text and review, inexpensive helpers."""
from types import SimpleNamespace
import json

import pytest

import config
import telemetry
from researcher import synthesis
from providers import openrouter


@pytest.mark.parametrize("depth,expected", [
    ("standard", ["report"]), ("deep", ["report"]),
    ("comprehensive", ["report_draft", "critique", "revision"]),
])
def test_writing_route_and_review_handoff(monkeypatch, depth, expected):
    calls = []
    def fake(**kwargs):
        calls.append(kwargs)
        return {"report_draft": "DRAFT", "critique": "AUDIT", "revision": "FINAL"}.get(kwargs["task"], "FINAL")
    monkeypatch.setattr(synthesis, "llm_simple", fake)
    out = synthesis._synthesise("Question", [{"title": "Evidence", "analysis": "Observed result"}], depth)
    assert out == "FINAL"
    assert [c["task"] for c in calls] == expected
    if depth == "comprehensive":
        assert "DRAFT" in calls[1]["prompt"]
        assert "AUDIT" in calls[2]["prompt"]
        assert "unavailable data" in calls[2]["prompt"]


def test_failed_review_cannot_publish_cheap_draft(monkeypatch):
    calls = []
    def fake(**kwargs):
        calls.append(kwargs["task"])
        if kwargs["task"] == "critique":
            raise RuntimeError("review unavailable")
        return "DRAFT"
    monkeypatch.setattr(synthesis, "llm_simple", fake)
    with pytest.raises(RuntimeError, match="review unavailable"):
        synthesis._synthesise("Question", [{"title": "Evidence"}], "comprehensive")
    assert calls == ["report_draft", "critique"]


def test_premium_routes_have_no_lower_tier_fallback_and_helpers_stay_cheap():
    for task in ("report", "critique", "revision", "correction"):
        assert [(p, m) for p, m, _ in config.ROUTING[task]] == [("openrouter", config.OR_SONNET)]
    for task in ("report_draft", "citation_repair", "concept"):
        assert [(p, m) for p, m, _ in config.ROUTING[task]] == [("openrouter", config.OR_PRO)]
    for task in ("clip", "moc"):
        assert config.ROUTING[task][0][:2] == ("gemini", "gemini-3.8-flash")
        assert config.ROUTING[task][1][:2] == ("openrouter", config.OR_FLASH)
    assert config.ROUTING["research"][0][:2] == ("openrouter", config.OR_FLASH)
    assert config.ROUTING["research"][0][2]["reasoning_effort"] == "high"
    assert all(opts.get("json_mode") for _, _, opts in config.ROUTING["clip"])
    assert not any(opts.get("json_mode") for _, _, opts in config.ROUTING["moc"])
    assert openrouter._completion_kwargs({"json_mode": True})["response_format"] == {"type": "json_object"}


def test_provider_usage_preserves_reasoning_cache_and_charge_metadata():
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=600, model_extra={
        "cost": .012,
        "prompt_tokens_details": {"cached_tokens": 700, "cache_write_tokens": 100},
        "completion_tokens_details": {"reasoning_tokens": 400},
    })
    with telemetry.run("research", "Trial"):
        telemetry.phase("synthesis", detail="critiquing")
        openrouter._record_usage(SimpleNamespace(usage=usage, id="response-1", provider="endpoint"), "model")
        telemetry.flush_usage("critique", request_opts={"reasoning_effort": "xhigh", "max_tokens": 32000})
    events = [json.loads(line) for line in telemetry._events_path.read_text().splitlines()]
    event = next(e for e in events if e["type"] == "llm")
    assert event["cost_usd"] == .012 and event["cost_reported"] is True
    assert event["cached_tokens"] == 700 and event["cache_write_tokens"] == 100
    assert event["tokens_out"] == 600 and event["reasoning_tokens"] == 400
    assert event["task"] == "critique" and event["phase"] == "synthesis"
    assert event["reasoning_effort"] == "xhigh" and event["max_tokens"] == 32000
    assert event["run_id"] and event["endpoint_provider"] == "endpoint"


def test_missing_cost_and_detail_are_unknown_not_measured_zero():
    openrouter._record_usage(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2)), "model")
    telemetry.flush_usage("report")
    event = json.loads(telemetry._events_path.read_text().splitlines()[-1])
    assert event["cost_reported"] is False
    assert event["cached_tokens"] is None and event["reasoning_tokens"] is None


def test_gemini_counts_thinking_in_total_output(monkeypatch):
    # Constructing the SDK client performs no requests; this is not a real LLM call.
    monkeypatch.setattr(config, "GEMINI_API_KEY", "unit-test-placeholder")
    from providers import gemini
    meta = SimpleNamespace(prompt_token_count=100, candidates_token_count=20,
                           thoughts_token_count=30, cached_content_token_count=50)
    gemini._record_usage(SimpleNamespace(usage_metadata=meta, response_id="gemini-1"), "flash")
    event = telemetry.take_usage()[0]
    assert event["completion_tokens"] == 50
    assert event["reasoning_tokens"] == 30 and event["cached_tokens"] == 50


def test_capacity_outage_falls_back_and_is_not_retried_per_source(monkeypatch):
    import llm
    from providers.base import ProviderUnavailable
    monkeypatch.setattr(llm, "_cooldown_until", {})
    monkeypatch.setattr(llm, "ROUTING", {"clip": [("gemini", "flash", {}), ("openrouter", "backup", {})]})
    calls = []
    def invoke(provider, model, opts):
        calls.append(provider)
        if provider == "gemini":
            raise ProviderUnavailable("503 high demand")
        return "ok"
    assert llm._run("clip", invoke) == "ok"
    assert llm._run("clip", invoke) == "ok"
    assert calls == ["gemini", "openrouter", "openrouter"]


def test_gemini_timeout_uses_capacity_fallback(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "unit-test-placeholder")
    from providers import gemini
    from providers.base import ProviderUnavailable
    class ReadTimeout(Exception):
        pass
    assert isinstance(gemini._classify(ReadTimeout()), ProviderUnavailable)
    assert isinstance(gemini._classify(Exception("504 DEADLINE_EXCEEDED")), ProviderUnavailable)
