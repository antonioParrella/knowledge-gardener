"""
Tier 2 — clipper.process_clipped_note with the LLM mocked.

Exercises the two branches that matter without spending a token: the `usable`
gate discarding a junk stub, and a normal clip being rewritten + marked
processed. The LLM is stubbed to return canned JSON (the real parse_json_response
still runs); index_note is stubbed to a no-op so this stays a clipper test.
"""

import json

import pytest

import clipper
import notes
import pdf_processor


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


@pytest.mark.parametrize("processor", [clipper, pdf_processor], ids=["web-clip", "pdf"])
def test_saved_analysis_renders_math_and_preserves_source(processor, tmp_vault, tmp_path, monkeypatch):
    source = r"Source text with its own \(y\) and escaped \[brackets\]."
    analysis = r"The loss \(L\) is:" + "\n\n\\[\nL = x^2\n\\]"
    monkeypatch.setattr(processor, "llm_simple", lambda **kw: json.dumps({
        "usable": True, "title": "Equations", "content": analysis,
    }))
    monkeypatch.setattr(processor, "index_note", lambda **kw: None)

    if processor is clipper:
        path = _make_clip(tmp_vault.INBOX_PATH, body=source)
        result = clipper.process_clipped_note(path)
    else:
        monkeypatch.setattr(pdf_processor, "INBOX_PATH", tmp_vault.INBOX_PATH)
        monkeypatch.setattr(pdf_processor, "PDF_ARCHIVE_PATH", tmp_path / "archive")
        monkeypatch.setattr(pdf_processor, "extract_pdf_text", lambda path: source)
        path = tmp_path / "input.pdf"
        path.write_bytes(b"stand-in for the original PDF")
        pdf_processor.process_pdf(path)
        result = tmp_vault.INBOX_PATH / "Equations.md"

    _, body = notes.read_note(result)
    assert body == "The loss $L$ is:\n\n$$\nL = x^2\n$$\n\n---\n\n## Original Content\n\n" + source


def test_already_processed_clip_is_skipped(tmp_vault, monkeypatch):
    path = tmp_vault.INBOX_PATH / "Done.md"
    notes.write_note(path, {"clipped": True, "processed": True, "source": "u"}, "b")
    # If the LLM were called this would raise; asserting it isn't proves the skip.
    monkeypatch.setattr(clipper, "llm_simple",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("LLM called")))

    assert clipper.process_clipped_note(path) == path
