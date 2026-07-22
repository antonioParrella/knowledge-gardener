"""
Tier 2 — clipper.find_existing_source duplicate detection against a real vault.

This is the guard that stopped the overnight-duplicate bug: the `exclude`
argument must let a note skip matching itself, or a real duplicate slips through.
"""

import clipper
import notes


def _clip(folder, name, url):
    path = folder / f"{name}.md"
    notes.write_note(path, {"clipped": True, "source": url}, "body")
    return path


def test_finds_duplicate_by_source_url(tmp_vault):
    _clip(tmp_vault.INBOX_PATH, "First", "https://example.com/a")
    assert clipper.find_existing_source("https://example.com/a") is not None


def test_returns_none_when_absent(tmp_vault):
    _clip(tmp_vault.INBOX_PATH, "First", "https://example.com/a")
    assert clipper.find_existing_source("https://example.com/other") is None


def test_exclude_prevents_self_match(tmp_vault):
    # The critical case: the note being processed must not match itself.
    path = _clip(tmp_vault.INBOX_PATH, "Self", "https://example.com/a")
    assert clipper.find_existing_source("https://example.com/a", exclude=path) is None


def test_exclude_still_finds_a_real_twin(tmp_vault):
    # Two notes share a URL; excluding one must still surface the other.
    p1 = _clip(tmp_vault.INBOX_PATH, "AAA", "https://example.com/dup")
    _clip(tmp_vault.INBOX_PATH, "BBB", "https://example.com/dup")
    found = clipper.find_existing_source("https://example.com/dup", exclude=p1)
    assert found is not None and found != p1


def test_searches_sources_folder_too(tmp_vault):
    _clip(tmp_vault.SOURCES_PATH, "SourceNote", "https://example.com/s")
    assert clipper.find_existing_source("https://example.com/s") is not None


def test_unknown_url_is_never_a_duplicate(tmp_vault):
    _clip(tmp_vault.INBOX_PATH, "X", "unknown")
    assert clipper.find_existing_source("unknown") is None
    assert clipper.find_existing_source("") is None
