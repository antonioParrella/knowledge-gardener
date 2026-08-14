"""
Tier 1 — the arXiv rate-limit gate (academic.pace_arxiv / arxiv_get).

A 429 from arXiv used to become `[{"error": ...}]` handed straight to the discovery
model, which reads identically to "arXiv has no papers on this" — one comprehensive
run made 16 searches, queued zero sources, and wrote a report anyway. These assert
the two properties that keep that from recurring: requests are spaced, and a 429 is
retried and then reported rather than swallowed.

Clock and sleep are faked; nothing here touches the network.
"""

import pytest
import requests

import academic


@pytest.fixture
def fake_clock(monkeypatch):
    """Replace time.monotonic/time.sleep in academic with a controllable clock."""
    state = {"now": 1000.0, "slept": []}

    def sleep(secs):
        state["slept"].append(secs)
        state["now"] += secs

    monkeypatch.setattr(academic.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(academic.time, "sleep", sleep)
    monkeypatch.setattr(academic, "_arxiv_last", 0.0)
    return state


class _Resp:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class TestIsArxiv:
    def test_matches_arxiv_hosts(self):
        assert academic.is_arxiv("https://arxiv.org/pdf/2211.17192")
        assert academic.is_arxiv("http://export.arxiv.org/api/query?x=1")

    def test_rejects_lookalike_hosts(self):
        # Substring matching would accept both of these.
        assert not academic.is_arxiv("https://arxiv.org.evil.com/pdf/1")
        assert not academic.is_arxiv("https://example.com/?ref=arxiv.org")

    def test_rejects_other_sources(self):
        assert not academic.is_arxiv("https://api.openalex.org/works")


class TestPacing:
    def test_first_request_does_not_wait(self, fake_clock):
        academic.pace_arxiv()
        assert fake_clock["slept"] == []

    def test_back_to_back_requests_are_spaced(self, fake_clock):
        academic.pace_arxiv()
        academic.pace_arxiv()
        academic.pace_arxiv()
        # Each subsequent call waits the full interval — this is the burst that
        # tripped arXiv's limiter when three searches ran in one model turn.
        assert fake_clock["slept"] == pytest.approx(
            [academic.ARXIV_MIN_INTERVAL_SECS] * 2
        )

    def test_no_wait_when_interval_already_elapsed(self, fake_clock):
        academic.pace_arxiv()
        fake_clock["now"] += academic.ARXIV_MIN_INTERVAL_SECS + 1
        academic.pace_arxiv()
        assert fake_clock["slept"] == []


class TestArxivGet:
    def test_returns_response_on_success(self, fake_clock, monkeypatch):
        monkeypatch.setattr(academic.requests, "get", lambda *a, **k: _Resp(200))
        assert academic.arxiv_get("http://export.arxiv.org/api/query").status_code == 200

    def test_retries_429_then_succeeds(self, fake_clock, monkeypatch):
        calls = {"n": 0}

        def get(*a, **k):
            calls["n"] += 1
            return _Resp(429 if calls["n"] == 1 else 200)

        monkeypatch.setattr(academic.requests, "get", get)
        assert academic.arxiv_get("http://export.arxiv.org/api/query").status_code == 200
        assert calls["n"] == 2
        # Backoff on top of the pacing gate, so the retry isn't itself a burst.
        assert academic.ARXIV_BACKOFF_SECS in fake_clock["slept"]

    def test_persistent_429_raises_rather_than_returning_nothing(self, fake_clock, monkeypatch):
        calls = {"n": 0}

        def get(*a, **k):
            calls["n"] += 1
            return _Resp(429)

        monkeypatch.setattr(academic.requests, "get", get)
        with pytest.raises(requests.HTTPError):
            academic.arxiv_get("http://export.arxiv.org/api/query")
        assert calls["n"] == academic.ARXIV_MAX_ATTEMPTS

    def test_non_429_http_error_is_not_retried(self, fake_clock, monkeypatch):
        calls = {"n": 0}

        def get(*a, **k):
            calls["n"] += 1
            return _Resp(404)

        monkeypatch.setattr(academic.requests, "get", get)
        with pytest.raises(requests.HTTPError):
            academic.arxiv_get("https://arxiv.org/pdf/9999.99999")
        assert calls["n"] == 1  # a withdrawn paper won't reappear

    def test_transport_error_is_retried(self, fake_clock, monkeypatch):
        calls = {"n": 0}

        def get(*a, **k):
            calls["n"] += 1
            if calls["n"] < academic.ARXIV_MAX_ATTEMPTS:
                raise requests.ConnectionError("reset by peer")
            return _Resp(200)

        monkeypatch.setattr(academic.requests, "get", get)
        assert academic.arxiv_get("http://export.arxiv.org/api/query").status_code == 200
        assert calls["n"] == academic.ARXIV_MAX_ATTEMPTS


class TestSearchArxivSurfacesFailure:
    def test_rate_limited_search_returns_an_error_entry(self, fake_clock, monkeypatch):
        monkeypatch.setattr(academic.requests, "get", lambda *a, **k: _Resp(429))
        results = academic.search_arxiv("speculative decoding")
        assert len(results) == 1 and "error" in results[0]
        assert "429" in results[0]["error"]
