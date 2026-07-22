"""Tier 2 — notes.read_note / write_note round-trip on real files."""

import notes


def test_write_then_read_roundtrips(tmp_path):
    path = tmp_path / "note.md"
    fm = {"processed": True, "tags": ["ml", "rl"], "note_count": 3}
    body = "# Title\n\nSome body text."
    notes.write_note(path, fm, body)

    got_fm, got_body = notes.read_note(path)
    assert got_fm == fm
    assert got_body == body


def test_read_note_without_frontmatter(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text("just body, no frontmatter", encoding="utf-8")
    fm, body = notes.read_note(path)
    assert fm == {}
    assert body == "just body, no frontmatter"


def test_write_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "deep" / "note.md"
    notes.write_note(path, {"a": 1}, "body")
    assert path.exists()


def test_safe_filename_strips_illegal_chars():
    assert notes.safe_filename('a/b:c*d?"e<f>g|h') == "abcdefgh"
