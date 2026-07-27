"""
Tier 1 — the vault dashboard note's rendering.

The note is the phone-native presentation, so what matters is that it always says
which of the three states the pipeline is in (idle / running / paused), that the
phase checklist points at the right step, and that a note title containing a '|'
can't shred the markdown table it sits in.
"""

import json

import dashboard
import telemetry


def _note() -> str:
    return dashboard._render_note(dashboard._payload())


def test_meter_renders_a_proportional_bar():
    assert "100%" in dashboard._meter(10, 10)
    assert "0%" in dashboard._meter(0, 10)
    assert dashboard._meter(5, 10).count("█") == 8       # half of the 16-wide bar
    assert dashboard._meter(5, 0) == ""                  # no limit → no meter


def test_meter_clamps_past_the_limit():
    """Blowing through a free-tier cap shows a full bar, not a bar wider than itself."""
    rendered = dashboard._meter(2000, 1500, width=16)
    assert rendered.count("█") == 16
    assert "░" not in rendered


def test_duration_switches_to_minutes():
    assert dashboard._duration(45) == "45s"
    assert dashboard._duration(125) == "2m05s"


def test_idle_note_says_so():
    note = _note()
    assert "Idle — nothing processing" in note
    assert "## Spend" in note


def test_running_note_shows_the_phase_checklist():
    with telemetry.run("research", "Quantum error correction", meta={"depth": "deep"}):
        telemetry.phase("sources", detail="A Paper", progress=(2, 5))
        note = _note()

    assert "Running — Research: Quantum error correction" in note
    assert "- [x] prior-knowledge" in note                # phases already passed
    assert "- [x] discovery" in note
    assert "- [ ] sources ← now" in note                  # the phase it's in
    assert "- [ ] synthesis" in note                      # not yet reached
    assert "A Paper (2/5)" in note


def test_blocked_note_leads_with_the_reason():
    telemetry.set_blocked("OpenRouter key credit $0.05 < $0.20 required")
    try:
        note = _note()
        assert "> [!warning] Research paused" in note
        assert "$0.05" in note
    finally:
        telemetry.set_blocked(None)


def test_run_titles_cannot_break_the_recent_runs_table():
    """A pipe in a note title would otherwise open a new column in the markdown table."""
    with telemetry.run("clip", "Rock | Paper | Scissors"):
        pass
    row = [ln for ln in _note().splitlines() if "Rock" in ln][0]
    assert r"Rock \| Paper \| Scissors" in row
    assert row.count("|") - row.count(r"\|") == 6         # 5 columns + closing pipe


def test_note_links_the_best_reachable_address():
    """
    The note is read on the phone, so its link must be the address that works
    from anywhere (the tailnet one when Tailscale is up), not the LAN-only one.
    """
    best = dashboard.reachable_urls()[0][1]
    assert f"Live version: {best}" in _note()


def test_queue_is_reported_when_work_is_waiting():
    telemetry.set_queue(clips=3, triggers=1, concepts=0)
    note = _note()
    assert "**3** clips" in note
    assert "**1** triggers" in note
    assert "concepts" not in note.split("## Spend")[0].split("## Queue")[1]


def test_payload_is_json_serialisable():
    """The web dashboard ships this straight to the browser."""
    with telemetry.run("concept", "Reward Prediction Error"):
        telemetry.push_usage("openrouter", "m", cost=0.02)
        telemetry.flush_usage("synthesis")
        payload = dashboard._payload()

    json.dumps(payload)                                   # must not raise
    assert payload["current"]["elapsed"] >= 0
    assert payload["derived"]["gemini_limit"] == 1500
    assert "research" in payload["phases"]
