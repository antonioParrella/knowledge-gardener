"""
telemetry.py — run state and the spend ledger behind the dashboard.

The pipeline used to be observable only through `watchdog.log` (raw stdout) and
trigger frontmatter, so "what is running right now, how far in, and what has it
cost" could only be reconstructed by reading prints. This module records that
directly, in two files beside the log (never inside the vault):

  dashboard_state.json  live snapshot — current run, queue depth, recent runs, spend
  events.jsonl          append-only history — one line per LLM call and per run

Both presentations (the local web dashboard and the vault dashboard note) read
`snapshot()`; neither computes anything the pipeline doesn't record here.

Everything in here is best-effort by construction: a telemetry failure must never
take down a research run, so every public entry point swallows its own errors.

Instrumentation is three calls:

    with telemetry.run("research", topic, meta={"depth": depth}):
        telemetry.phase("discovery")
        ...

    telemetry.push_usage(...)   # providers report what a call cost
    telemetry.flush_usage(task) # llm._run attributes it to the task + current run
"""

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from config import (
    STATE_PATH, EVENTS_PATH, EVENTS_MAX_BYTES, RECENT_RUNS, DAILY_HISTORY,
)

# Ordered phases per run kind. The dashboard renders these as a stepper, so this
# map IS the UI's notion of progress — a phase() name not listed here still shows
# as the current label, it just doesn't advance the stepper.
PHASES = {
    "clip":     ["analysing", "indexing"],
    "pdf":      ["extracting", "analysing", "indexing"],
    "research": ["prior-knowledge", "discovery", "sources", "synthesis",
                 "citations", "indexing", "concepts"],
    "callout":  ["prior-knowledge", "discovery", "sources", "synthesis",
                 "citations", "writing"],
    "concept":  ["prior-knowledge", "discovery", "sources", "synthesis", "indexing"],
}

# Human labels for the run kinds, used by both presentations.
KIND_LABELS = {
    "clip": "Clip", "pdf": "PDF", "research": "Research",
    "callout": "Callout", "concept": "Concept",
}

_lock = threading.RLock()
_local = threading.local()          # per-thread usage buffer + run stack
_last_write = 0.0
_MIN_WRITE_INTERVAL = 2.0           # seconds; forced through on run/phase changes

_state_path = STATE_PATH
_events_path = EVENTS_PATH

_state: dict = {}


# ── State scaffolding ────────────────────────────────────────────────────────────

def _blank_state() -> dict:
    return {
        "started": _iso(),
        "updated": _iso(),
        "pid": os.getpid(),
        "current": None,
        "queue": {},
        "blocked": None,
        "recent": [],
        "spend": {
            "total_usd": 0.0,
            "by_task": {},
            "by_model": {},
            "daily": [],
            "key": None,          # live OpenRouter /key reading
        },
    }


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def configure(directory: Path | None = None, *, state_path: Path | None = None,
              events_path: Path | None = None) -> None:
    """
    Point telemetry at a different directory (tests, or a relocated vault).
    Resets the in-memory state and reloads from the new location.
    """
    global _state_path, _events_path, _state
    if directory is not None:
        _state_path = Path(directory) / "dashboard_state.json"
        _events_path = Path(directory) / "events.jsonl"
    if state_path is not None:
        _state_path = Path(state_path)
    if events_path is not None:
        _events_path = Path(events_path)
    with _lock:
        _state = _blank_state()
    load()


def load() -> None:
    """
    Restore the ledger from disk at start-up.

    Spend history and recent runs are cumulative across restarts — losing them on
    every watchdog restart would make "what have I spent" meaningless. A run left
    `running` in the file was killed mid-flight (the process died), so it is
    retired as `interrupted` rather than shown as live forever.
    """
    global _state
    try:
        raw = json.loads(_state_path.read_text(encoding="utf-8"))
    except Exception:
        with _lock:
            _state = _blank_state()
        return

    with _lock:
        _state = _blank_state()
        spend = raw.get("spend")
        if isinstance(spend, dict):
            _state["spend"].update(spend)
        recent = raw.get("recent")
        if isinstance(recent, list):
            _state["recent"] = recent[:RECENT_RUNS]
        stranded = raw.get("current")
        if isinstance(stranded, dict) and stranded.get("status") == "running":
            stranded["status"] = "interrupted"
            stranded["finished"] = _iso()
            _state["recent"].insert(0, stranded)
            del _state["recent"][RECENT_RUNS:]


def snapshot() -> dict:
    """A consistent deep copy of the live state — what both presentations render."""
    with _lock:
        return json.loads(json.dumps(_state))


# ── Persistence ──────────────────────────────────────────────────────────────────

def _persist(force: bool = False) -> None:
    """Write state.json atomically. Debounced unless `force` (run/phase changes)."""
    global _last_write
    now = time.time()
    if not force and now - _last_write < _MIN_WRITE_INTERVAL:
        return
    _last_write = now
    try:
        _state["updated"] = _iso()
        _state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = _state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_state, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _state_path)
    except Exception:
        pass  # telemetry must never break the pipeline


def _event(event_type: str, **fields) -> None:
    """
    Append one line to events.jsonl, rotating it once it gets large.

    The parameter is `event_type`, not `kind`: a run event's own fields include
    `kind` (research / clip / …), and naming the positional the same thing makes
    every _event("run", kind=…) call a TypeError.
    """
    try:
        _events_path.parent.mkdir(parents=True, exist_ok=True)
        if _events_path.exists() and _events_path.stat().st_size > EVENTS_MAX_BYTES:
            os.replace(_events_path, _events_path.with_suffix(".jsonl.1"))
        line = json.dumps({"t": _iso(), "type": event_type, **fields}, ensure_ascii=False)
        with _events_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# ── Runs ─────────────────────────────────────────────────────────────────────────

def _stack() -> list:
    if not hasattr(_local, "stack"):
        _local.stack = []
    return _local.stack


def current_run() -> dict | None:
    with _lock:
        return _state.get("current")


@contextmanager
def run(kind: str, title: str, meta: dict | None = None):
    """
    Mark a unit of pipeline work as the current run for its duration.

    Nested runs (the clip pipeline invoked *inside* a research run to process a
    queued source) do not start a second run — the outer run already reports
    "sources 3/9", and replacing it with the clip would lose the research run from
    the dashboard entirely. A nested call only annotates the outer run's detail.
    """
    if _stack():
        outer_detail = (current_run() or {}).get("detail", "")
        set_detail(f"{KIND_LABELS.get(kind, kind)}: {title}")
        _stack().append(None)
        try:
            yield
        finally:
            _stack().pop()
            set_detail(outer_detail)
        return

    started = time.time()
    record = {
        "id": f"{int(started * 1000):x}",
        "kind": kind,
        "title": title,
        "meta": meta or {},
        "started": _iso(started),
        "started_ts": started,
        "finished": None,
        "secs": 0.0,
        "status": "running",
        "phase": "",
        "detail": "",
        "progress": None,
        "cost_usd": 0.0,
        "calls": 0,
        "error": None,
    }
    with _lock:
        _state["current"] = record
        _persist(force=True)
    _stack().append(record["id"])

    try:
        yield
    except BaseException as exc:
        _finish("failed", f"{type(exc).__name__}: {exc}")
        raise
    else:
        _finish("done", None)
    finally:
        _stack().pop()


def _finish(status: str, error: str | None) -> None:
    with _lock:
        record = _state.get("current")
        if not record:
            return
        record["status"] = status
        record["error"] = error
        record["finished"] = _iso()
        record["secs"] = round(time.time() - record["started_ts"], 1)
        _state["current"] = None
        _state["recent"].insert(0, record)
        del _state["recent"][RECENT_RUNS:]
        _bump_daily("runs", 1)
        _persist(force=True)

    _event("run", kind=record["kind"], title=record["title"], status=status,
           secs=record["secs"], cost_usd=round(record["cost_usd"], 6),
           calls=record["calls"], error=error)
    _notify_run(record)


def _notify_run(record: dict) -> None:
    """Push a phone notification for a finished run (see notify.py for the policy)."""
    try:
        from notify import notify_run
        notify_run(record)
    except Exception:
        pass


def phase(name: str, detail: str = "", progress: tuple[int, int] | None = None) -> None:
    """Advance the current run to a named phase (see PHASES for the known order)."""
    try:
        with _lock:
            record = _state.get("current")
            if not record:
                return
            record["phase"] = name
            record["detail"] = detail
            record["progress"] = ({"done": progress[0], "total": progress[1]}
                                  if progress else None)
            _persist(force=True)
    except Exception:
        pass


def set_detail(text: str, progress: tuple[int, int] | None = None) -> None:
    """Update the current run's sub-label without changing its phase."""
    try:
        with _lock:
            record = _state.get("current")
            if not record:
                return
            record["detail"] = text
            if progress:
                record["progress"] = {"done": progress[0], "total": progress[1]}
            _persist()
    except Exception:
        pass


# ── Queue depth & gating ─────────────────────────────────────────────────────────

def set_queue(**counts: int) -> None:
    """
    Record what is waiting: clips, pdfs, triggers, concepts, callouts.

    Merges rather than replaces, because the watchdog learns these counts at
    different points in a rescan (callouts are only scanned for after the
    preflight gate). Every count is refreshed each cycle, so nothing goes stale
    except a callout count taken before research was paused — which is still the
    truth about what's pending.
    """
    try:
        with _lock:
            _state["queue"].update({k: int(v) for k, v in counts.items()})
            _persist(force=True)
    except Exception:
        pass


def set_blocked(reason: str | None) -> None:
    """Record the preflight verdict — why research is paused, or None when it isn't."""
    try:
        with _lock:
            _state["blocked"] = reason
            _persist(force=True)
    except Exception:
        pass


def record_key_status(status: dict | None) -> None:
    """
    Store the live OpenRouter /key reading (cumulative spend + remaining credit).

    This is free: research_preflight already polls it before every research run,
    so the dashboard's headline spend number costs no extra API call.
    """
    try:
        if not isinstance(status, dict):
            return
        with _lock:
            _state["spend"]["key"] = {
                "usage": status.get("usage"),
                "limit": status.get("limit"),
                "limit_remaining": status.get("limit_remaining"),
                "checked": _iso(),
            }
            _persist()
    except Exception:
        pass


# ── Cost & usage ─────────────────────────────────────────────────────────────────

def push_usage(provider: str, model: str, cost: float | None = None,
               prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """
    Called by a provider once per completed API call.

    Buffered per-thread rather than recorded immediately because the provider
    doesn't know which *task* it is serving — only llm._run does. flush_usage()
    drains the buffer once the call chain returns.
    """
    try:
        if not hasattr(_local, "usage"):
            _local.usage = []
        _local.usage.append({
            "provider": provider, "model": model,
            "cost": float(cost) if cost else 0.0,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
        })
    except Exception:
        pass


def take_usage() -> list[dict]:
    """Drain and return this thread's buffered usage records."""
    buf = getattr(_local, "usage", None) or []
    _local.usage = []
    return buf


def flush_usage(task: str) -> None:
    """Attribute buffered provider usage to a task, the current run, and the ledger."""
    try:
        for u in take_usage():
            _record_call(task, u["provider"], u["model"], u["cost"],
                         u["prompt_tokens"], u["completion_tokens"])
    except Exception:
        pass


def _record_call(task: str, provider: str, model: str, cost: float,
                 prompt_tokens: int, completion_tokens: int) -> None:
    with _lock:
        record = _state.get("current")
        if record:
            record["cost_usd"] += cost
            record["calls"] += 1

        spend = _state["spend"]
        spend["total_usd"] = round(spend.get("total_usd", 0.0) + cost, 6)
        spend["by_task"][task] = round(spend["by_task"].get(task, 0.0) + cost, 6)
        spend["by_model"][model] = round(spend["by_model"].get(model, 0.0) + cost, 6)

        _bump_daily("usd", cost)
        _bump_daily(f"{provider}_calls", 1)
        _bump_daily("tokens_in", prompt_tokens)
        _bump_daily("tokens_out", completion_tokens)
        _persist()

    _event("llm", task=task, provider=provider, model=model,
           cost_usd=round(cost, 6), tokens_in=prompt_tokens,
           tokens_out=completion_tokens, run=(record or {}).get("title"))


def record_api(name: str, n: int = 1) -> None:
    """
    Count a non-LLM API call against its free-tier budget (Tavily searches).

    These cost no money but do have a monthly cap, so what matters on the
    dashboard is the count, not a dollar figure.
    """
    try:
        with _lock:
            _bump_daily(f"{name}_calls", n)
            _persist()
    except Exception:
        pass


def _bump_daily(field: str, amount) -> None:
    """Add to today's rollup bucket. Caller holds the lock."""
    if not amount:
        return
    daily = _state["spend"]["daily"]
    today = _today()
    if not daily or daily[-1].get("date") != today:
        daily.append({"date": today})
        del daily[:-DAILY_HISTORY]
    bucket = daily[-1]
    value = bucket.get(field, 0) + amount
    bucket[field] = round(value, 6) if isinstance(amount, float) else value


def today_totals() -> dict:
    """Today's rollup bucket ({} before the first call of the day)."""
    with _lock:
        daily = _state["spend"]["daily"]
        if daily and daily[-1].get("date") == _today():
            return dict(daily[-1])
        return {"date": _today()}


# Load whatever the previous run left behind, so spend survives a restart.
load()
