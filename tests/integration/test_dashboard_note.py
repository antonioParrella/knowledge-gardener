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
