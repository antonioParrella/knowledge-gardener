"""
Tier 2 — the vault dashboard note on disk.

The note is rewritten every 60s rescan, which Obsidian Sync would push to the
phone every 60s forever. The write must therefore be a no-op when nothing
changed — that skip is the behaviour under test here, alongside the plain
round-trip.
"""

import dashboard
import telemetry
from notes import read_note


def test_note_is_written_with_frontmatter(tmp_path):
    target = tmp_path / "Index" / "_dashboard.md"
    dashboard.write_vault_note(target)

    assert target.exists()
    fm, body = read_note(target)
    assert fm["dashboard"] is True
    assert "updated" in fm
    assert "# Pipeline Dashboard" in body


def test_unchanged_state_does_not_rewrite_the_note(tmp_path):
    target = tmp_path / "_dashboard.md"
    dashboard.write_vault_note(target)
    first = target.read_text(encoding="utf-8")

    dashboard.write_vault_note(target)
    assert target.read_text(encoding="utf-8") == first     # byte-identical, no touch


def test_a_finished_run_changes_the_note(tmp_path):
    target = tmp_path / "_dashboard.md"
    dashboard.write_vault_note(target)
    before = target.read_text(encoding="utf-8")

    with telemetry.run("research", "Sleep and memory consolidation"):
        telemetry.push_usage("openrouter", "m", cost=0.34)
        telemetry.flush_usage("synthesis")

    dashboard.write_vault_note(target)
    after = target.read_text(encoding="utf-8")

    assert after != before
    assert "Sleep and memory consolidation" in after
    assert "$0.340" in after


def test_write_failure_is_swallowed(tmp_path):
    """A dashboard that can't be written must not raise into the watchdog loop."""
    dashboard.write_vault_note(tmp_path)                   # a directory, not a file


def test_note_writer_thread_refreshes_without_the_main_loop(tmp_path, monkeypatch):
    """
    Regression: the note was only rendered from the main loop's rescan, so it
    froze for the whole of any long run. Worst case was a restart with a pending
    trigger — the backlog drained before the loop began, leaving the note showing
    the *previous* process's last write while a 24-source run went by.

    The writer thread must therefore keep rendering while the main thread is
    blocked doing pipeline work.
    """
    import threading
    import time

    target = tmp_path / "_dashboard.md"
    monkeypatch.setattr(dashboard, "DASHBOARD_NOTE_PATH", target)
    dashboard.stop_note_writer()

    written = threading.Event()
    real_write = dashboard.write_vault_note

    def _tracking(path=None):
        real_write(path)
        if target.exists():
            written.set()

    monkeypatch.setattr(dashboard, "write_vault_note", _tracking)

    dashboard.start_note_writer(interval=0.05)
    try:
        # Stand in for the main thread being busy inside drain_backlog().
        time.sleep(0.3)
        assert written.is_set(), "note thread never rendered while main thread was busy"
        assert "# Pipeline Dashboard" in target.read_text(encoding="utf-8")
    finally:
        dashboard.stop_note_writer()


def test_note_writer_is_not_started_twice(monkeypatch, tmp_path):
    """A second call must not spawn a competing writer for the same file."""
    monkeypatch.setattr(dashboard, "DASHBOARD_NOTE_PATH", tmp_path / "_dashboard.md")
    dashboard.stop_note_writer()
    try:
        dashboard.start_note_writer(interval=30)
        first = dashboard._note_writer
        dashboard.start_note_writer(interval=30)
        assert dashboard._note_writer is first
    finally:
        dashboard.stop_note_writer()


def test_note_writer_is_disabled_when_the_note_path_is_none(monkeypatch):
    """DASHBOARD_NOTE_PATH = None turns the mirror off; no thread should start."""
    monkeypatch.setattr(dashboard, "DASHBOARD_NOTE_PATH", None)
    dashboard.stop_note_writer()
    dashboard.start_note_writer(interval=30)
    assert dashboard._note_writer is None


def test_demo_binds_the_configured_host_not_loopback(monkeypatch):
    """
    Regression: the demo server bound 127.0.0.1, so the phone could never reach
    it — which is the main reason to run the demo at all. It must bind the same
    interface the real dashboard does.
    """
    import config
    bound = {}

    class _StubServer:
        def __init__(self, address, handler):
            bound["address"] = address

        def serve_forever(self):
            raise KeyboardInterrupt          # return immediately

        def shutdown(self):
            pass

    monkeypatch.setattr(dashboard, "ThreadingHTTPServer", _StubServer)
    dashboard._demo()

    assert bound["address"] == (config.DASHBOARD_HOST, config.DASHBOARD_PORT)
    assert bound["address"][0] != "127.0.0.1"


def test_reachable_urls_always_offers_a_local_address():
    urls = dashboard.reachable_urls()
    assert urls, "start-up must always print at least one URL"
    assert any("localhost" in url for _, url in urls)
    assert all(url.endswith(str(dashboard.DASHBOARD_PORT)) for _, url in urls)


def test_api_state_is_served(tmp_path):
    """The page's only data source, exercised end to end over HTTP."""
    import json
    import urllib.request
    from http.server import ThreadingHTTPServer
    import threading

    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard._Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/api/state", timeout=5) as resp:
            payload = json.loads(resp.read())
        assert "spend" in payload and "phases" in payload

        with urllib.request.urlopen(base, timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "<title>Knowledge Gardener</title>" in page
        # Self-contained: no CDN scripts, stylesheets, fonts, or images, so the
        # page works on a phone with no internet — only the SVG namespace URI
        # (an identifier, never fetched) may look like an external reference.
        for pattern in ("src=", "<link", "@import", "//cdn", "fonts.g"):
            assert pattern not in page
    finally:
        server.shutdown()
