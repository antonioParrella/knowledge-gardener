"""
Tier 1 — MOC topic sanitisation. A MOC topic becomes a filename
("MOC - <topic>.md"), so model-leaked markup ("Schizophrenia</S>") or any
Windows-illegal character must be stripped before it crashes the write (WinError
123) or spawns a junk near-duplicate MOC.
"""

import pytest

from indexer import sanitize_moc_topic


def test_strips_closing_tag_fragment():
    # The exact real-world failure: a "</S>" leaked into the name.
    assert sanitize_moc_topic("Schizophrenia</S>") == "Schizophrenia"


def test_strips_paired_tags():
    assert sanitize_moc_topic("<b>Behavioral Genetics</b>") == "Behavioral Genetics"


def test_strips_lone_illegal_char():
    # A lone '<' with no closing '>' is not a tag but is still filename-illegal.
    assert sanitize_moc_topic("Schizophrenia<") == "Schizophrenia"


def test_strips_trailing_brace():
    # The second real-world leak: a trailing '}' — legal in a filename, so a
    # blocklist of only illegal chars missed it and spawned a junk MOC.
    assert sanitize_moc_topic("Schizophrenia}") == "Schizophrenia"


@pytest.mark.parametrize("junk", ["{", "}", "[", "]", "`", "~", "#", "@", "!"])
def test_strips_non_topic_punctuation(junk):
    assert sanitize_moc_topic(f"Gen{junk}Models") == "GenModels"


def test_keeps_legit_topic_punctuation():
    # Ampersand, parentheses, comma, hyphen, apostrophe are legitimate in names.
    assert sanitize_moc_topic("Mandate (International Law)") == "Mandate (International Law)"
    assert sanitize_moc_topic("Diffusion & Flow-Matching") == "Diffusion & Flow-Matching"


@pytest.mark.parametrize("ch", list('<>:"/\\|?*'))
def test_removes_every_windows_illegal_char(ch):
    out = sanitize_moc_topic(f"Path{ch}ology")
    assert ch not in out
    assert out.startswith("Path") and out.endswith("ology")


def test_collapses_whitespace_left_behind():
    assert sanitize_moc_topic("Generative  <x>  Models") == "Generative Models"


def test_clean_name_passes_through_unchanged():
    assert sanitize_moc_topic("Pharmacoepidemiology") == "Pharmacoepidemiology"


def test_empty_after_cleaning_returns_empty_string():
    # Caller supplies the "General" fallback; helper just returns cleaned text.
    assert sanitize_moc_topic("<<>>") == ""
