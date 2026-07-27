"""
dashboard.py — the two presentations of telemetry state.

Neither of these computes anything: they render `telemetry.snapshot()`. Adding a
number to the dashboard means recording it in telemetry.py, not deriving it here.

  start_dashboard()   a read-only web UI on :8765, served from a daemon thread
                      inside the watchdog process. Reachable from a phone on the
                      same Wi-Fi, or anywhere over Tailscale.
  write_vault_note()  the same state rendered into Index/_dashboard.md, which
                      Obsidian Sync carries to the phone with no networking at
                      all — the fallback for when you're off the LAN.

Both are strictly optional and strictly non-fatal: the server runs in a daemon
thread that swallows its own errors, and a failed note write is logged and
dropped. Losing the dashboard must never cost a research run.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from config import (
    DASHBOARD_ENABLED, DASHBOARD_HOST, DASHBOARD_PORT, DASHBOARD_NOTE_PATH,
    VAULT_PATH, RESEARCH_MIN_KEY_CREDITS,
)
import telemetry

# Free-tier ceilings the meters are drawn against. Gemini's is per day and resets
# ~midnight Pacific; Tavily's is per month. Both are the *free* plan's limits —
# see AGENTS.md § Free Tier Limits.
GEMINI_DAILY_LIMIT = 1500
TAVILY_MONTHLY_LIMIT = 1000


# ── Payload ──────────────────────────────────────────────────────────────────────

def _payload() -> dict:
    """The snapshot plus the few rollups both presentations need."""
    state = telemetry.snapshot()
    daily = state.get("spend", {}).get("daily", [])
    today = telemetry.today_totals()
    month = time.strftime("%Y-%m")

    current = state.get("current")
    if current:
        current["elapsed"] = round(time.time() - current.get("started_ts", time.time()))

    state["derived"] = {
        "today": today,
        "spend_7d": round(sum(d.get("usd", 0.0) for d in daily[-7:]), 4),
        "spend_30d": round(sum(d.get("usd", 0.0) for d in daily[-30:]), 4),
        "gemini_today": today.get("gemini_calls", 0),
        "gemini_limit": GEMINI_DAILY_LIMIT,
        "tavily_month": sum(d.get("tavily_calls", 0) for d in daily
                            if str(d.get("date", "")).startswith(month)),
        "tavily_limit": TAVILY_MONTHLY_LIMIT,
        "min_credits": RESEARCH_MIN_KEY_CREDITS,
        "vault": VAULT_PATH.name,
    }
    state["phases"] = telemetry.PHASES
    state["kind_labels"] = telemetry.KIND_LABELS
    return state


# ── Web dashboard ────────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    server_version = "knowledge-gardener"

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler's naming)
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
        elif path == "/api/state":
            body = json.dumps(_payload(), ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def _send(self, status: int, ctype: str, body: bytes):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # phone closed the tab mid-response

    def log_message(self, fmt, *args):
        """Silence per-request logging — the page polls every 2s and stdout is
        redirected into watchdog.log, so the default handler would bury the
        pipeline's own trace under access lines."""


_server = None


def start_dashboard() -> str | None:
    """
    Start the dashboard on a daemon thread. Returns the URL, or None if disabled
    or unavailable (a taken port must not stop the watchdog from running).
    """
    global _server
    if not DASHBOARD_ENABLED:
        return None
    try:
        _server = ThreadingHTTPServer((DASHBOARD_HOST, DASHBOARD_PORT), _Handler)
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, daemon=True,
                         name="dashboard").start()
        return f"http://{_local_ip()}:{DASHBOARD_PORT}"
    except Exception as e:
        print(f"[dashboard] could not start on port {DASHBOARD_PORT}: {e}")
        return None


def _local_ip() -> str:
    """Best-effort LAN address, so the log prints a URL the phone can actually use."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
            return s.getsockname()[0]
    except Exception:
        return "localhost"


# ── Vault note ───────────────────────────────────────────────────────────────────

_BAR_FULL, _BAR_EMPTY = "█", "░"


def _meter(used: float, limit: float, width: int = 16) -> str:
    """A text meter that renders identically on desktop and phone Obsidian."""
    if not limit:
        return ""
    frac = max(0.0, min(1.0, used / limit))
    filled = round(frac * width)
    return f"`{_BAR_FULL * filled}{_BAR_EMPTY * (width - filled)}` {frac * 100:.0f}%"


def _duration(secs) -> str:
    secs = int(secs or 0)
    return f"{secs}s" if secs < 60 else f"{secs // 60}m{secs % 60:02d}s"


def _render_note(state: dict) -> str:
    """Render the state as the markdown body of Index/_dashboard.md."""
    d = state["derived"]
    current = state.get("current")
    blocked = state.get("blocked")
    lines: list[str] = ["# Pipeline Dashboard", ""]

    # Status banner — the one thing you open this note to see.
    if blocked:
        lines += [f"> [!warning] Research paused\n> {blocked}", ""]
    elif current:
        kind = state["kind_labels"].get(current["kind"], current["kind"])
        lines += [f"> [!info] Running — {kind}: {current['title']}", ""]
    else:
        lines += ["> [!done] Idle — nothing processing", ""]

    if current:
        phases = state["phases"].get(current["kind"], [])
        phase = current.get("phase") or ""
        # A checklist of the run's phases, ticked up to the current one, so the
        # note answers "what stage is it in" at a glance on a phone.
        try:
            reached = phases.index(phase)
        except ValueError:
            reached = -1
        lines.append("## Current run")
        for i, name in enumerate(phases):
            mark = "x" if i < reached else " "
            here = " ← now" if i == reached else ""
            lines.append(f"- [{mark}] {name}{here}")
        detail = current.get("detail") or ""
        progress = current.get("progress") or {}
        if progress.get("total"):
            detail = f"{detail} ({progress.get('done', 0)}/{progress['total']})"
        if detail.strip():
            lines.append(f"\n*{detail.strip()}*")
        lines += [
            "",
            f"Elapsed **{_duration(current.get('elapsed'))}** · "
            f"${current.get('cost_usd', 0):.3f} so far · "
            f"{current.get('calls', 0)} model calls",
            "",
        ]

    queue = state.get("queue") or {}
    pending = {k: v for k, v in queue.items() if v}
    lines.append("## Queue")
    lines.append(", ".join(f"**{v}** {k}" for k, v in pending.items())
                 if pending else "Nothing waiting.")
    lines.append("")

    key = (state.get("spend") or {}).get("key") or {}
    lines += [
        "## Spend",
        "",
        "| | |",
        "|---|---|",
        f"| Today | ${d['today'].get('usd', 0):.3f} |",
        f"| Last 7 days | ${d['spend_7d']:.2f} |",
        f"| Last 30 days | ${d['spend_30d']:.2f} |",
    ]
    if key.get("usage") is not None:
        lines.append(f"| OpenRouter key, lifetime | ${key['usage']:.2f} |")
    if key.get("limit_remaining") is not None:
        lines.append(f"| OpenRouter credit left | ${key['limit_remaining']:.2f} |")
    lines += [
        "",
        f"Gemini today {d['gemini_today']}/{d['gemini_limit']} {_meter(d['gemini_today'], d['gemini_limit'])}",
        "",
        f"Tavily this month {d['tavily_month']}/{d['tavily_limit']} {_meter(d['tavily_month'], d['tavily_limit'])}",
        "",
    ]

    recent = state.get("recent") or []
    if recent:
        icons = {"done": "✅", "failed": "❌", "interrupted": "⚠️"}
        lines += ["## Recent runs", "",
                  "| | Type | What | Took | Cost |", "|---|---|---|---|---|"]
        for r in recent[:12]:
            title = str(r.get("title", "")).replace("|", "\\|")[:60]
            lines.append(
                f"| {icons.get(r.get('status'), '·')} "
                f"| {state['kind_labels'].get(r.get('kind'), r.get('kind'))} "
                f"| {title} | {_duration(r.get('secs'))} | ${r.get('cost_usd', 0):.3f} |"
            )
        lines.append("")

    lines += ["---", "",
              "*Written by the watchdog. Live version: "
              f"http://{_local_ip()}:{DASHBOARD_PORT}*"]
    return "\n".join(lines)


def write_vault_note(path: Path | None = None) -> None:
    """
    Mirror the dashboard into the vault, skipping the write when nothing changed.

    The skip matters: this runs every 60s rescan, and rewriting an identical note
    would have Obsidian Sync pushing a file to the phone every minute forever.
    """
    target = path or DASHBOARD_NOTE_PATH
    if target is None:
        return
    try:
        body = _render_note(_payload())
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            # Compare bodies only — the frontmatter timestamp always differs.
            if existing.split("---", 2)[-1].strip() == body.strip():
                return
        from notes import write_note
        write_note(target, {"dashboard": True, "updated": telemetry._iso()}, body)
    except Exception as e:
        print(f"[dashboard] could not write {target.name}: {e}")


# ── The page ─────────────────────────────────────────────────────────────────────
# Self-contained: no CDN, no fonts, no build step. It polls /api/state every 2s and
# re-renders. Colors come from the validated data-viz palette (categorical slots in
# fixed order, sequential blue for the single-series bars, reserved status hues for
# run outcomes), declared once as custom properties for each mode.

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>Knowledge Gardener</title>
<style>
:root {
  color-scheme: light;
  --surface-1:#fcfcfb; --plane:#f9f9f7;
  --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
  --track:#cde2fb;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface-1:#1a1a19; --plane:#0d0d0d;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
    --track:#104281;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1:#1a1a19; --plane:#0d0d0d;
  --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
  --track:#104281;
}

* { box-sizing: border-box; }
body {
  margin:0; padding:20px 16px 48px;
  background:var(--plane); color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap { max-width:940px; margin:0 auto; }
h1 { font-size:20px; font-weight:600; margin:0; letter-spacing:-.01em; }
h2 { font-size:13px; font-weight:600; margin:0 0 14px; color:var(--ink-2);
     text-transform:uppercase; letter-spacing:.06em; }
.sub { color:var(--muted); font-size:13px; margin-top:2px; }

header { display:flex; align-items:flex-start; justify-content:space-between;
         gap:12px; margin-bottom:20px; }
.theme-btn { background:none; border:1px solid var(--border); color:var(--ink-2);
             border-radius:8px; padding:6px 10px; font:inherit; font-size:13px;
             cursor:pointer; }

.card { background:var(--surface-1); border:1px solid var(--border);
        border-radius:14px; padding:18px; margin-bottom:14px; }

/* ── Now playing ─────────────────────────────────────────────── */
.now-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.chip { font-size:12px; font-weight:600; letter-spacing:.03em; padding:3px 9px;
        border-radius:999px; border:1px solid var(--border); color:var(--ink-2); }
.dot { width:9px; height:9px; border-radius:50%; flex:none; }
.dot.live { background:var(--s1); animation:pulse 1.6s ease-in-out infinite; }
.dot.idle { background:var(--muted); }
.dot.blocked { background:var(--warning); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
@media (prefers-reduced-motion: reduce) { .dot.live { animation:none } }
.now-title { font-size:19px; font-weight:600; margin:10px 0 2px; letter-spacing:-.01em; }
.now-meta { color:var(--ink-2); font-size:13px; }

.stepper { display:flex; gap:2px; margin:18px 0 10px; }
.step { flex:1 1 0; min-width:0; }
.step .bar { height:6px; border-radius:3px; background:var(--track); }
.step.done .bar { background:var(--s1); }
.step.here .bar { background:var(--s1); }
.step .lbl { font-size:11px; color:var(--muted); margin-top:6px;
             white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.step.here .lbl { color:var(--ink); font-weight:600; }
.detail { font-size:13px; color:var(--ink-2); margin-top:8px; }
.detail b { font-variant-numeric:tabular-nums; }

/* ── KPI row ─────────────────────────────────────────────────── */
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }
.kpi { background:var(--surface-1); border:1px solid var(--border);
       border-radius:14px; padding:16px; }
.kpi .label { font-size:12px; color:var(--ink-2); }
.kpi .value { font-size:30px; font-weight:600; letter-spacing:-.02em; margin:6px 0 2px; }
.kpi .hero { font-size:44px; }
.kpi .foot { font-size:12px; color:var(--muted); }
.meter { height:6px; border-radius:3px; background:var(--track);
         margin:10px 0 6px; overflow:hidden; }
.meter > i { display:block; height:100%; border-radius:3px; background:var(--s1); }
.meter > i.warning { background:var(--warning); }
.meter > i.critical { background:var(--critical); }

/* ── Charts ──────────────────────────────────────────────────── */
.chart { width:100%; overflow-x:auto; }
svg { display:block; max-width:100%; }
svg text { font:11px system-ui,-apple-system,"Segoe UI",sans-serif; }
.tick { fill:var(--muted); font-variant-numeric:tabular-nums; }
.vlabel { fill:var(--ink-2); font-weight:600; font-variant-numeric:tabular-nums; }
.gridline { stroke:var(--grid); stroke-width:1; }
.baseline { stroke:var(--axis); stroke-width:1; }
.hit { fill:transparent; cursor:default; }

.legend { display:flex; flex-wrap:wrap; gap:14px; margin-top:12px; font-size:12px;
          color:var(--ink-2); }
.legend span { display:inline-flex; align-items:center; gap:6px; }
.swatch { width:10px; height:10px; border-radius:3px; flex:none; }

.tip { position:fixed; z-index:9; pointer-events:none; opacity:0;
       transition:opacity .1s; background:var(--surface-1); color:var(--ink);
       border:1px solid var(--border); border-radius:10px; padding:8px 10px;
       font-size:12px; box-shadow:0 6px 24px rgba(0,0,0,.16); max-width:240px; }
.tip b { font-variant-numeric:tabular-nums; }

details { margin-top:12px; }
summary { font-size:12px; color:var(--muted); cursor:pointer; }
table { border-collapse:collapse; width:100%; margin-top:10px; font-size:13px; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--border);
         white-space:nowrap; }
th { font-size:11px; text-transform:uppercase; letter-spacing:.05em;
     color:var(--muted); font-weight:600; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
td.name { white-space:normal; }
.status { display:inline-flex; align-items:center; gap:6px; }
.status i { width:8px; height:8px; border-radius:50%; flex:none; }
.i-done { background:var(--good); } .i-failed { background:var(--critical); }
.i-interrupted { background:var(--warning); } .i-running { background:var(--s1); }

.banner { border-left:3px solid var(--warning); background:var(--surface-1);
          border-radius:0 10px 10px 0; padding:12px 14px; margin-bottom:14px;
          font-size:13px; color:var(--ink-2); }
.empty { color:var(--muted); font-size:13px; }
.tblwrap { overflow-x:auto; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Knowledge Gardener</h1>
      <div class="sub" id="sub">connecting…</div>
    </div>
    <button class="theme-btn" id="theme" type="button">Theme</button>
  </header>

  <div id="banner"></div>

  <section class="card" id="now"></section>

  <h2>Budget</h2>
  <section class="kpis" id="kpis"></section>

  <section class="card">
    <h2>Spend, last 14 days</h2>
    <div class="chart" id="spend-chart"></div>
    <details>
      <summary>Data table</summary>
      <div class="tblwrap"><table id="spend-table"></table></div>
    </details>
  </section>

  <section class="card" id="task-card">
    <h2>Where the money goes</h2>
    <div class="chart" id="task-chart"></div>
    <div class="legend" id="task-legend"></div>
    <details>
      <summary>Data table</summary>
      <div class="tblwrap"><table id="task-table"></table></div>
    </details>
  </section>

  <section class="card">
    <h2>Recent runs</h2>
    <div class="tblwrap"><table id="runs"></table></div>
  </section>
</div>
<div class="tip" id="tip"></div>

<script>
"use strict";
const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const el = (n, a = {}, kids = []) => {
  const e = document.createElementNS(SVG, n);
  for (const k in a) e.setAttribute(k, a[k]);
  for (const c of [].concat(kids)) e.appendChild(c);
  return e;
};
const txt = (s) => document.createTextNode(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const money = (v) => "$" + (Number(v) || 0).toFixed(Math.abs(v) >= 10 ? 2 : 3);
const dur = (s) => { s = Math.round(s || 0);
  return s < 60 ? s + "s" : Math.floor(s / 60) + "m" + String(s % 60).padStart(2, "0") + "s"; };
const dayLabel = (d) => (d || "").slice(5).replace("-", "/");

/* Theme toggle stamps data-theme, which wins over the OS setting both ways. */
const savedTheme = localStorage.getItem("kg-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
$("theme").onclick = () => {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const now = document.documentElement.dataset.theme || (dark ? "dark" : "light");
  const next = now === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("kg-theme", next);
  if (LAST) render(LAST);
};

/* Shared tooltip. Every chart mark gets one; values are also in the table views,
   so the tooltip enhances and never gates. */
const tip = $("tip");
function bindTip(node, html) {
  const show = (ev) => {
    tip.innerHTML = html;
    tip.style.opacity = "1";
    const p = ev.touches ? ev.touches[0] : ev;
    const r = tip.getBoundingClientRect();
    tip.style.left = Math.min(p.clientX + 12, innerWidth - r.width - 8) + "px";
    tip.style.top = Math.max(8, p.clientY - r.height - 12) + "px";
  };
  node.addEventListener("mousemove", show);
  node.addEventListener("mouseenter", show);
  node.addEventListener("touchstart", show, { passive: true });
  node.addEventListener("mouseleave", () => (tip.style.opacity = "0"));
  node.addEventListener("touchend", () => (tip.style.opacity = "0"));
}

let LAST = null, TICKER = null;

/* ── Now playing ─────────────────────────────────────────────── */
function renderNow(s) {
  const cur = s.current, box = $("now");
  if (!cur) {
    const q = Object.entries(s.queue || {}).filter(([, v]) => v);
    box.innerHTML =
      `<div class="now-head"><span class="dot idle"></span><span class="chip">Idle</span></div>
       <div class="now-title">Nothing processing</div>
       <div class="now-meta">${q.length
         ? esc(q.map(([k, v]) => `${v} ${k}`).join(" · ")) + " waiting for the next scan"
         : "Queue empty."}</div>`;
    return;
  }
  const phases = (s.phases || {})[cur.kind] || [];
  const at = phases.indexOf(cur.phase);
  const steps = phases.map((p, i) =>
    `<div class="step ${i < at ? "done" : i === at ? "here" : ""}">
       <div class="bar"></div><div class="lbl">${esc(p)}</div></div>`).join("");

  const prog = cur.progress && cur.progress.total
    ? ` <b>${cur.progress.done}/${cur.progress.total}</b>` : "";
  const meta = [
    (s.kind_labels || {})[cur.kind] || cur.kind,
    cur.meta && cur.meta.depth ? cur.meta.depth : null,
  ].filter(Boolean).join(" · ");

  box.innerHTML =
    `<div class="now-head"><span class="dot live"></span>
       <span class="chip">${esc(meta)}</span>
       <span class="now-meta">started ${esc(cur.started.replace("T", " "))}</span></div>
     <div class="now-title">${esc(cur.title)}</div>
     <div class="stepper">${steps}</div>
     <div class="detail">${esc(cur.detail || cur.phase || "")}${prog}</div>
     <div class="now-meta" style="margin-top:10px">
       <b id="elapsed">${dur(cur.elapsed)}</b> elapsed ·
       ${money(cur.cost_usd)} so far · ${cur.calls} model calls</div>`;

  // Tick the elapsed time locally so the page feels live between 2s polls.
  clearInterval(TICKER);
  let secs = cur.elapsed;
  TICKER = setInterval(() => {
    const n = $("elapsed");
    if (!n) return clearInterval(TICKER);
    n.textContent = dur(++secs);
  }, 1000);
}

/* ── KPI tiles ───────────────────────────────────────────────── */
function meter(used, limit) {
  const frac = limit ? Math.min(1, used / limit) : 0;
  const cls = frac >= 0.9 ? "critical" : frac >= 0.75 ? "warning" : "";
  return `<div class="meter"><i class="${cls}" style="width:${(frac * 100).toFixed(1)}%"></i></div>`;
}

function renderKpis(s) {
  const d = s.derived, key = (s.spend || {}).key || {};
  const tiles = [];

  tiles.push(`<div class="kpi"><div class="label">Spent today</div>
    <div class="value hero">${money(d.today.usd || 0)}</div>
    <div class="foot">${money(d.spend_7d)} last 7 days · ${money(d.spend_30d)} last 30</div></div>`);

  if (key.usage != null) {
    const rem = key.limit_remaining, lim = key.limit;
    tiles.push(`<div class="kpi"><div class="label">OpenRouter key</div>
      <div class="value">${money(key.usage)}</div>
      ${lim != null ? meter(key.usage, lim) : ""}
      <div class="foot">${rem != null
        ? money(rem) + " credit left" + (rem < d.min_credits ? " — below the research floor" : "")
        : "lifetime spend · no cap set"}</div></div>`);
  }

  tiles.push(`<div class="kpi"><div class="label">Gemini calls today</div>
    <div class="value">${d.gemini_today.toLocaleString()}</div>
    ${meter(d.gemini_today, d.gemini_limit)}
    <div class="foot">of ${d.gemini_limit.toLocaleString()} free per day</div></div>`);

  tiles.push(`<div class="kpi"><div class="label">Tavily searches</div>
    <div class="value">${d.tavily_month.toLocaleString()}</div>
    ${meter(d.tavily_month, d.tavily_limit)}
    <div class="foot">of ${d.tavily_limit.toLocaleString()} free this month</div></div>`);

  $("kpis").innerHTML = tiles.join("");
}

/* ── Daily spend: one series, so sequential blue and no legend box ── */
function renderSpend(s) {
  const host = $("spend-chart");
  host.textContent = "";
  const days = (s.spend.daily || []).slice(-14);
  if (!days.length) {
    host.innerHTML = '<p class="empty">No spend recorded yet.</p>';
    $("spend-table").innerHTML = "";
    return;
  }

  const W = Math.max(320, Math.min(880, host.clientWidth || 880));
  const H = 210, padL = 46, padR = 12, padT = 16, padB = 30;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = Math.max(...days.map((d) => d.usd || 0), 0.01);
  const niceMax = Math.ceil(max * 20) / 20;                   // round up to 5¢
  const band = plotW / days.length;
  const bw = Math.min(24, band - 2);                          // ≤24px, 2px gap
  const y = (v) => padT + plotH - (v / niceMax) * plotH;
  const peak = days.reduce((a, b) => ((b.usd || 0) > (a.usd || 0) ? b : a), days[0]);

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H,
                          role: "img", "aria-label": "Daily API spend, last 14 days" });

  for (let i = 0; i <= 2; i++) {
    const v = (niceMax / 2) * i;
    svg.appendChild(el("line", { class: "gridline", x1: padL, x2: W - padR,
                                 y1: y(v), y2: y(v) }));
    svg.appendChild(el("text", { class: "tick", x: padL - 8, y: y(v) + 4,
                                 "text-anchor": "end" }, [txt("$" + v.toFixed(2))]));
  }

  days.forEach((d, i) => {
    const v = d.usd || 0;
    const x = padL + i * band + (band - bw) / 2;
    const h = Math.max(v > 0 ? 2 : 0, plotH - (y(v) - padT));
    if (h) {
      // 4px rounded data-end, square at the baseline. Clamped to half the bar's
      // own height and width so a 2px sliver can't produce a malformed path.
      const r = Math.min(4, h / 2, bw / 2);
      svg.appendChild(el("path", {
        fill: "var(--s1)",
        d: `M${x},${padT + plotH} V${padT + plotH - h + r} q0,${-r} ${r},${-r}
            h${bw - 2 * r} q${r},0 ${r},${r} V${padT + plotH} Z`,
      }));
    }
    // Label only today and the peak — never a number on every bar.
    const isPeak = d.date === peak.date && v > 0;
    const isLast = i === days.length - 1 && v > 0;
    if (isPeak || isLast) {
      svg.appendChild(el("text", { class: "vlabel", x: x + bw / 2,
                                   y: padT + plotH - h - 6, "text-anchor": "middle" },
                         [txt("$" + v.toFixed(2))]));
    }
    if (i % Math.ceil(days.length / 7) === 0 || i === days.length - 1) {
      svg.appendChild(el("text", { class: "tick", x: x + bw / 2, y: H - 10,
                                   "text-anchor": "middle" }, [txt(dayLabel(d.date))]));
    }
    // Hit target spans the whole band, so a 2px bar is still easy to hover.
    const hit = el("rect", { class: "hit", x: padL + i * band, y: padT,
                             width: band, height: plotH });
    bindTip(hit, `<b>${esc(d.date)}</b><br>${money(v)} · ${d.runs || 0} runs<br>
      ${(d.openrouter_calls || 0)} OpenRouter · ${(d.gemini_calls || 0)} Gemini`);
    svg.appendChild(hit);
  });

  svg.appendChild(el("line", { class: "baseline", x1: padL, x2: W - padR,
                               y1: padT + plotH, y2: padT + plotH }));
  host.appendChild(svg);

  $("spend-table").innerHTML =
    "<thead><tr><th>Date</th><th>Spend</th><th>Runs</th><th>Gemini</th><th>Tavily</th></tr></thead><tbody>" +
    days.slice().reverse().map((d) =>
      `<tr><td>${esc(d.date)}</td><td class="num">${money(d.usd || 0)}</td>
       <td class="num">${d.runs || 0}</td><td class="num">${d.gemini_calls || 0}</td>
       <td class="num">${d.tavily_calls || 0}</td></tr>`).join("") + "</tbody>";
}

/* ── Cost by task: part-to-whole, so one stacked bar with a legend ── */
const TASK_SLOTS = ["--s1", "--s2", "--s3", "--s4"];

function renderTasks(s) {
  const host = $("task-chart"), legend = $("task-legend");
  const all = Object.entries(s.spend.by_task || {})
    .filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  // Four categorical slots is the ceiling for this form; a fifth task folds into
  // "other" rather than inventing a ninth hue nobody can tell apart.
  const entries = all.length > 4
    ? [...all.slice(0, 3), ["other", all.slice(3).reduce((a, [, v]) => a + v, 0)]]
    : all;
  const total = entries.reduce((a, [, v]) => a + v, 0);
  host.textContent = "";
  if (!total) {
    $("task-card").style.display = "none";
    return;
  }
  $("task-card").style.display = "";

  const W = Math.max(320, Math.min(880, host.clientWidth || 880));
  const H = 58, bh = 34;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H,
                          role: "img", "aria-label": "Share of spend by task" });
  let x = 0;
  entries.forEach(([task, v], i) => {
    const w = Math.max(2, (v / total) * W - 2);              // 2px surface gap
    const r = Math.min(4, w / 2);
    svg.appendChild(el("path", {
      fill: `var(${TASK_SLOTS[i]})`,
      d: `M${x + r},0 h${w - 2 * r} q${r},0 ${r},${r} v${bh - 2 * r}
          q0,${r} ${-r},${r} h${-(w - 2 * r)} q${-r},0 ${-r},${-r}
          v${-(bh - 2 * r)} q0,${-r} ${r},${-r} Z`,
    }));
    // Only label inside the segment when the text actually fits with padding —
    // a clipped label is worse than none, and the legend + table still carry it.
    const label = `${task} ${money(v)}`;
    if (w > label.length * 7 + 16) {
      svg.appendChild(el("text", {
        x: x + w / 2, y: bh / 2 + 4, "text-anchor": "middle",
        fill: i === 3 ? "#0b0b0b" : "#ffffff", "font-weight": "600",
      }, [txt(label)]));
    }
    const hit = el("rect", { class: "hit", x, y: 0, width: w, height: bh });
    bindTip(hit, `<b>${esc(task)}</b><br>${money(v)} · ${((v / total) * 100).toFixed(0)}% of spend`);
    svg.appendChild(hit);
    x += w + 2;
  });
  svg.appendChild(el("text", { class: "tick", x: 0, y: H - 4 },
                    [txt(`${money(total)} total since tracking began`)]));
  host.appendChild(svg);

  legend.innerHTML = entries.map(([task], i) =>
    `<span><i class="swatch" style="background:var(${TASK_SLOTS[i]})"></i>${esc(task)}</span>`
  ).join("");

  $("task-table").innerHTML =
    "<thead><tr><th>Task</th><th>Spend</th><th>Share</th></tr></thead><tbody>" +
    entries.map(([t, v]) =>
      `<tr><td>${esc(t)}</td><td class="num">${money(v)}</td>
       <td class="num">${((v / total) * 100).toFixed(0)}%</td></tr>`).join("") + "</tbody>";
}

/* ── Recent runs ─────────────────────────────────────────────── */
function renderRuns(s) {
  const rows = (s.recent || []).slice(0, 20);
  if (!rows.length) {
    $("runs").innerHTML = '<tbody><tr><td class="empty">No runs recorded yet.</td></tr></tbody>';
    return;
  }
  $("runs").innerHTML =
    "<thead><tr><th>Status</th><th>Type</th><th>What</th><th>Took</th><th>Cost</th><th>Calls</th></tr></thead><tbody>" +
    rows.map((r) => `<tr>
      <td><span class="status"><i class="i-${esc(r.status)}"></i>${esc(r.status)}</span></td>
      <td>${esc((s.kind_labels || {})[r.kind] || r.kind)}</td>
      <td class="name" title="${esc(r.error || "")}">${esc(r.title)}</td>
      <td class="num">${dur(r.secs)}</td>
      <td class="num">${money(r.cost_usd)}</td>
      <td class="num">${r.calls || 0}</td></tr>`).join("") + "</tbody>";
}

/* ── Poll & render ───────────────────────────────────────────── */
function render(s) {
  LAST = s;
  $("sub").textContent =
    `${s.derived.vault} · watching since ${s.started.replace("T", " ")}`;
  $("banner").innerHTML = s.blocked
    ? `<div class="banner"><b>Research paused.</b> ${esc(s.blocked)}</div>` : "";
  renderNow(s);
  renderKpis(s);
  renderSpend(s);
  renderTasks(s);
  renderRuns(s);
}

async function poll() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    render(await r.json());
    document.body.style.opacity = "1";
  } catch (e) {
    // Hold the last render at reduced opacity rather than flashing a skeleton.
    document.body.style.opacity = "0.5";
    if (!LAST) $("sub").textContent = "watchdog not reachable";
  }
}
poll();
setInterval(poll, 2000);
addEventListener("resize", () => { if (LAST) { renderSpend(LAST); renderTasks(LAST); } });
</script>
</body>
</html>
"""


# ── Demo mode ────────────────────────────────────────────────────────────────────

def _demo():
    """
    `python src/dashboard.py --demo` — seed plausible state and serve the page, so
    the dashboard can be previewed (and restyled) without waiting for a real
    research run or restarting the watchdog. Writes to a scratch state file, never
    the live ledger.
    """
    import random
    import tempfile

    telemetry.configure(Path(tempfile.gettempdir()) / "kg-dashboard-demo")
    telemetry._state = telemetry._blank_state()   # fresh each run, never additive

    spend = telemetry._state["spend"]
    for i in range(13, -1, -1):                   # 14 days, ending today
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        spend["daily"].append({
            "date": day,
            "usd": round(random.uniform(0, 1.4) ** 2, 4),
            "runs": random.randint(0, 6),
            "gemini_calls": random.randint(20, 900),
            "openrouter_calls": random.randint(5, 60),
            "tavily_calls": random.randint(0, 18),
        })
    spend["total_usd"] = round(sum(d["usd"] for d in spend["daily"]), 4)
    spend["by_task"] = {"synthesis": 8.42, "research": 2.61, "clip": 0.44, "moc": 0.12}
    spend["key"] = {"usage": 11.59, "limit": 15.0, "limit_remaining": 3.41,
                    "checked": telemetry._iso()}

    for kind, title, status, secs, cost in [
        ("research", "Logical qubits and error correction, 2024-2025", "done", 512, 0.83),
        ("concept", "Concept - Surface Code", "done", 141, 0.19),
        ("clip", "Attention Is All You Need", "done", 12, 0.0),
        ("callout", "Which of our two options scales better?", "failed", 88, 0.31),
    ]:
        telemetry._state["recent"].append({
            "id": title, "kind": kind, "title": title, "meta": {},
            "started": telemetry._iso(), "started_ts": time.time(), "finished": telemetry._iso(),
            "secs": secs, "status": status, "phase": "", "detail": "", "progress": None,
            "cost_usd": cost, "calls": random.randint(4, 40),
            "error": "IncompleteReportError: report appears truncated" if status == "failed" else None,
        })

    telemetry.set_queue(clips=2, pdfs=0, triggers=1, concepts=3, callouts=0)
    telemetry._state["current"] = {
        "id": "demo", "kind": "research", "title": "How do GLP-1 agonists affect muscle mass?",
        "meta": {"depth": "comprehensive"},
        "started": telemetry._iso(), "started_ts": time.time() - 437, "finished": None,
        "secs": 0.0, "status": "running", "phase": "sources",
        "detail": "Semaglutide and lean mass preservation in older adults",
        "progress": {"done": 4, "total": 11}, "cost_usd": 0.42, "calls": 23, "error": None,
    }

    url = f"http://localhost:{DASHBOARD_PORT}"
    server = ThreadingHTTPServer(("127.0.0.1", DASHBOARD_PORT), _Handler)
    print(f"Demo dashboard on {url} — Ctrl+C to stop")
    print(f"Vault note preview:\n\n{_render_note(_payload())}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        _demo()
    else:
        print("Usage: python src/dashboard.py --demo")
