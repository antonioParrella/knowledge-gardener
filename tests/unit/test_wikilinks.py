"""
Tier 1 — citation-integrity helpers in researcher/synthesis.py.

These are the deterministic half of the "reports cite [[21]] instead of the real
title" defense AGENTS.md describes. The LLM repair pass is Tier 3; the fused-link
split and bad-link detection are pure and pinned here.
"""

import researcher
from researcher import _normalize_fused_wikilinks as fuse, _find_bad_wikilinks as bad


class TestNormalizeFusedWikilinks:
    def test_splits_fused_pair(self):
        assert fuse("see [[A], [B]]") == "see [[A]], [[B]]"

    def test_splits_three(self):
        assert fuse("[[A], [B], [C]]") == "[[A]], [[B]], [[C]]"

    def test_leaves_wellformed_untouched(self):
        assert fuse("see [[A]] and [[B]]") == "see [[A]] and [[B]]"

    def test_single_link_untouched(self):
        assert fuse("just [[One Title]] here") == "just [[One Title]] here"

    def test_preserves_multiword_titles(self):
        assert fuse("[[World Models], [Diffusion Models]]") == "[[World Models]], [[Diffusion Models]]"


class TestFindBadWikilinks:
    VALID = {"Real Note", "Another Real Note"}

    def test_flags_numbered_citation(self):
        assert bad("cite [[21]] and [[27]]", self.VALID) == ["21", "27"]

    def test_valid_links_not_flagged(self):
        assert bad("[[Real Note]] and [[Another Real Note]]", self.VALID) == []

    def test_alias_is_stripped_before_check(self):
        # `[[Real Note|shown text]]` targets a valid note.
        assert bad("[[Real Note|the paper]]", self.VALID) == []

    def test_heading_suffix_stripped_before_check(self):
        assert bad("[[Real Note#Section]]", self.VALID) == []

    def test_malformed_interior_bracket_always_flagged(self):
        # A stray bracket inside means fused/malformed — must be flagged even if
        # the fragment happens to name something.
        result = bad("[[A], [B]]", self.VALID)
        assert result  # non-empty: the malformed link is caught

    def test_deduped(self):
        assert bad("[[21]] then [[21]] again", self.VALID) == ["21"]


class TestReportComplete:
    GOOD = "x" * 500 + " and so the analysis concludes cleanly."

    def test_accepts_complete_report(self):
        researcher._assert_report_complete(self.GOOD, "topic")  # no raise

    def test_rejects_too_short(self):
        import pytest
        with pytest.raises(researcher.IncompleteReportError):
            researcher._assert_report_complete("too short", "topic")

    def test_rejects_midword_truncation(self):
        import pytest
        text = "x" * 500 + " this was difficult for Israeli"
        # ends on an alphanumeric mid-sentence -> truncated
        with pytest.raises(researcher.IncompleteReportError):
            researcher._assert_report_complete(text, "topic")

    def test_rejects_dangling_connector(self):
        import pytest
        text = "x" * 500 + " the causes were many, including—"
        with pytest.raises(researcher.IncompleteReportError):
            researcher._assert_report_complete(text, "topic")

    def test_accepts_ending_on_wikilink_bracket(self):
        text = "x" * 500 + " as shown in [[Some Real Note]]"
        researcher._assert_report_complete(text, "topic")  # ']' is a valid ending
