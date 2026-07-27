"""
Tier 1 — the run ledger's pure bookkeeping.

Everything the dashboard shows is derived from these records, so the things worth
pinning down are: a run lands in `recent` with the right status, a failed run is
recorded *and* still raises, a nested run doesn't hijack the outer one, and cost
is attributed to the task the router chose rather than the provider that billed it.
"""

import json

import pytest

import telemetry


def test_completed_run_is_recorded_and_cleared():
    with telemetry.run("research", "Quantum error correction", meta={"depth": "deep"}):
        assert telemetry.current_run()["title"] == "Quantum error correction"

    state = telemetry.snapshot()
    assert state["current"] is None
    assert state["recent"][0]["status"] == "done"
    assert state["recent"][0]["meta"]["depth"] == "deep"


def test_failed_run_is_recorded_and_still_raises():
    with pytest.raises(ValueError):
        with telemetry.run("research", "Doomed topic"):
            raise ValueError("synthesis blew up")

    run = telemetry.snapshot()["recent"][0]
    assert run["status"] == "failed"
    assert "synthesis blew up" in run["error"]


def test_nested_run_annotates_rather_than_replaces():
    """A clip processed inside a research run must not evict it from the dashboard."""
    with telemetry.run("research", "Diffusion models"):
        telemetry.phase("sources", progress=(1, 3))
        with telemetry.run("clip", "Some Paper"):
            current = telemetry.current_run()
            assert current["kind"] == "research"          # outer run survives
            assert "Some Paper" in current["detail"]
        # The outer run's own detail is restored on the way out.
        assert "Some Paper" not in telemetry.current_run()["detail"]

    assert len(telemetry.snapshot()["recent"]) == 1        # one run, not two


def test_nested_phase_calls_do_not_clobber_the_outer_stepper():
    """
    Regression: phase ③ runs the clip pipeline per source, and the clip's own
    telemetry.phase("analysing") was writing straight through to the research
    run — knocking its stepper onto a phase it doesn't have and clearing the
    source-by-source progress, exactly when the display matters most.
    """
    with telemetry.run("research", "Diffusion models"):
        telemetry.phase("sources", detail="Paper A", progress=(3, 11))
        with telemetry.run("clip", "Paper A"):
            telemetry.phase("analysing")               # inner pipeline's phase
            current = telemetry.current_run()
            assert current["phase"] == "sources"       # outer stepper unmoved
            assert current["progress"] == {"done": 3, "total": 11}
            assert "analysing" in current["detail"]    # still visible, as detail

        assert telemetry.current_run()["phase"] == "sources"
        assert telemetry.current_run()["progress"] == {"done": 3, "total": 11}


def test_phase_and_progress_are_exposed():
    with telemetry.run("research", "Topic"):
        telemetry.phase("sources", detail="Paper A", progress=(2, 9))
        current = telemetry.current_run()
        assert current["phase"] == "sources"
        assert current["progress"] == {"done": 2, "total": 9}


def test_cost_is_attributed_to_task_and_run():
    with telemetry.run("research", "Costly topic"):
        telemetry.push_usage("openrouter", "deepseek/deepseek-v4-pro",
                             cost=0.21, prompt_tokens=1000, completion_tokens=500)
        telemetry.flush_usage("synthesis")
        assert telemetry.current_run()["cost_usd"] == pytest.approx(0.21)

    spend = telemetry.snapshot()["spend"]
    assert spend["total_usd"] == pytest.approx(0.21)
    assert spend["by_task"]["synthesis"] == pytest.approx(0.21)
    assert spend["by_model"]["deepseek/deepseek-v4-pro"] == pytest.approx(0.21)
    assert telemetry.today_totals()["usd"] == pytest.approx(0.21)


def test_flush_drains_the_buffer_so_cost_is_never_double_counted():
    telemetry.push_usage("openrouter", "m", cost=0.10)
    telemetry.flush_usage("clip")
    telemetry.flush_usage("clip")                          # nothing left to bill
    assert telemetry.snapshot()["spend"]["total_usd"] == pytest.approx(0.10)


def test_free_gemini_calls_are_counted_but_not_charged():
    telemetry.push_usage("gemini", "gemini-3.5-flash", cost=0.0,
                         prompt_tokens=800, completion_tokens=200)
    telemetry.flush_usage("clip")

    today = telemetry.today_totals()
    assert today["gemini_calls"] == 1
    assert today.get("usd", 0) == 0
    assert today["tokens_in"] == 800


def test_record_api_counts_free_tier_calls():
    telemetry.record_api("tavily")
    telemetry.record_api("tavily", 2)
    assert telemetry.today_totals()["tavily_calls"] == 3


def test_queue_counts_merge_across_calls():
    telemetry.set_queue(clips=2, triggers=1)
    telemetry.set_queue(callouts=3)
    assert telemetry.snapshot()["queue"] == {"clips": 2, "triggers": 1, "callouts": 3}


def test_state_survives_restart_and_retires_a_stranded_run(tmp_path):
    """
    A run left `running` in the file means the process was killed mid-flight. It
    must not come back as live — it is retired to `recent` as interrupted, while
    the spend ledger carries over.
    """
    state_path = tmp_path / "restart" / "dashboard_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "current": {"kind": "research", "title": "Killed mid-run", "status": "running"},
        "recent": [{"kind": "clip", "title": "Older", "status": "done"}],
        "spend": {"total_usd": 4.25, "by_task": {"synthesis": 4.25},
                  "by_model": {}, "daily": [], "key": None},
    }), encoding="utf-8")

    telemetry.configure(state_path.parent)
    state = telemetry.snapshot()

    assert state["current"] is None
    assert state["recent"][0]["status"] == "interrupted"
    assert state["recent"][1]["title"] == "Older"
    assert state["spend"]["total_usd"] == 4.25


def test_telemetry_failures_never_propagate(monkeypatch):
    """A broken state file must not take the pipeline down with it."""
    monkeypatch.setattr(telemetry, "_state_path", None)     # every write will raise
    telemetry.phase("discovery")
    telemetry.set_queue(clips=1)
    telemetry.record_api("tavily")
    with telemetry.run("clip", "still works"):
        pass
