"""
Tier 2 — clipper.process_clipped_note with the LLM mocked.

Exercises the two branches that matter without spending a token: the `usable`
gate discarding a junk stub, and a normal clip being rewritten + marked
processed. The LLM is stubbed to return canned JSON (the real parse_json_response
still runs); index_note is stubbed to a no-op so this stays a clipper test.
"""

import json

import clipper
import notes


def _make_clip(inbox, name="Clipped - Something", url="https://example.com/a", body="Article text."):
    path = inbox / f"{name}.md"
    notes.write_note(path, {"clipped": True, "source": url, "processed": False}, body)
    return path


def test_usable_false_discards_stub(tmp_vault, monkeypatch):
    path = _make_clip(tmp_vault.INBOX_PATH)
    monkeypatch.setattr(
        clipper, "llm_simple",
        lambda **kw: json.dumps({"usable": False, "title": "CAPTCHA wall"}),
    )

    result = clipper.process_clipped_note(path)

    assert result is None
    assert not path.exists()  # the junk stub is deleted, not indexed


def test_normal_clip_is_processed_and_renamed(tmp_vault, monkeypatch):
    path = _make_clip(tmp_vault.INBOX_PATH, name="Clipped - Raw")
    monkeypatch.setattr(clipper, "llm_simple", lambda **kw: json.dumps({
        "usable": True,
        "title": "Clean Article Title",
        "summary": "A concise summary.",
        "moc_summary": "One-liner for the MOC.",
        "tags": ["Machine Learning", "rl"],
    }))
    monkeypatch.setattr(clipper, "index_note", lambda **kw: None)  # don't touch MOCs here

    result = clipper.process_clipped_note(path)

    assert result is not None
    assert result.stem == "Clean Article Title"  # renamed to the clean title
    fm, body = notes.read_note(result)
    assert fm["processed"] is True
    assert fm["tags"] == ["machine-learning", "rl"]  # normalised
    assert "## Original Content" in body  # original body preserved


def test_already_processed_clip_is_skipped(tmp_vault, monkeypatch):
    path = tmp_vault.INBOX_PATH / "Done.md"
    notes.write_note(path, {"clipped": True, "processed": True, "source": "u"}, "b")
    # If the LLM were called this would raise; asserting it isn't proves the skip.
    monkeypatch.setattr(clipper, "llm_simple",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("LLM called")))

    assert clipper.process_clipped_note(path) == path
