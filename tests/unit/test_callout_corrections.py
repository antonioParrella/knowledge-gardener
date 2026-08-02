"""
Tier 1 — the in-place correction mechanism in researcher/corrections.py.

An LLM editing the user's notes unattended is the one genuinely destructive thing
in this pipeline, so the parts that decide *whether an edit lands* are pinned here:
the exact/unique match contract, the protected-span refusal, and each gate in
verify_edits. All pure str → str; the loop that drives them is Tier 3 territory.
"""

import pytest

from researcher import (
    ANSWER_OPEN, ANSWER_CLOSE, edit_note, execute_tool, get_edits, reset_edits,
    verify_edits, render_answer_block, clean_question,
)
from researcher import corrections


NOTE = """# A Report

## Findings

The combination improved attention (SMD = 0.39) in [[Some Meta-Analysis]].

Caffeine alone was not tested as a comparator.

## Conclusion

The stack works.
"""


class TestEditNoteMatching:
    def setup_method(self):
        reset_edits(NOTE)

    def test_unique_match_applies(self):
        out = edit_note("The stack works.", "The stack is unproven.", "overstated")
        assert "Replaced" in out
        working, edits = get_edits()
        assert "The stack is unproven." in working
        assert "The stack works." not in working
        assert len(edits) == 1
        assert edits[0]["why"] == "overstated"

    def test_missing_text_is_a_loud_error_not_a_silent_noop(self):
        out = edit_note("text that is not there", "replacement", "why")
        assert "Not found" in out
        assert get_edits()[0] == NOTE  # working copy untouched

    def test_ambiguous_match_refused_with_count(self):
        reset_edits("alpha beta\nalpha gamma\n")
        out = edit_note("alpha", "ALPHA", "why")
        assert "appears 2 times" in out
        assert get_edits()[0] == "alpha beta\nalpha gamma\n"

    def test_identical_strings_refused(self):
        assert "identical" in edit_note("The stack works.", "The stack works.", "w")

    def test_empty_old_string_refused(self):
        assert "empty" in edit_note("", "something", "w")

    def test_edit_cap_enforced(self, monkeypatch):
        monkeypatch.setattr(corrections, "MAX_CALLOUT_EDITS", 1)
        reset_edits("one\ntwo\n")
        assert "Replaced" in edit_note("one", "1", "w")
        out = edit_note("two", "2", "w")
        assert "limit reached" in out.lower()
        assert len(get_edits()[1]) == 1

    def test_math_normalised_in_the_replacement_only(self):
        reset_edits("The effect was small.\n")
        edit_note("The effect was small.", r"The effect was \(d=0.2\).", "w")
        assert "$d=0.2$" in get_edits()[0]


class TestProtectedSpans:
    WITH_ANSWER = (
        "Before the block.\n\n"
        f"{ANSWER_OPEN}\n"
        "> [!done] **A prior question?**\n\n"
        "#### A prior question?\n\n"
        "A prior claim that was made.\n"
        f"{ANSWER_CLOSE}\n\n"
        "After the block.\n"
    )

    def test_edit_inside_a_prior_answer_is_refused(self):
        reset_edits(self.WITH_ANSWER)
        out = edit_note("A prior claim that was made.", "Something else.", "w")
        assert "previous research answer" in out
        assert get_edits()[0] == self.WITH_ANSWER

    def test_edit_outside_a_prior_answer_is_allowed(self):
        reset_edits(self.WITH_ANSWER)
        assert "Replaced" in edit_note("After the block.", "After, corrected.", "w")

    def test_in_progress_marker_is_protected(self):
        body = "Prose.\n\n> [!info] Researching: something…\n\nMore prose.\n"
        reset_edits(body)
        out = edit_note("> [!info] Researching: something…", "> gone", "w")
        assert "previous research answer" in out


class TestExecuteTool:
    def test_dispatches_edit_note(self):
        reset_edits("hello world\n")
        assert "Replaced" in execute_tool(
            "edit_note", {"old_string": "hello", "new_string": "goodbye", "why": "w"}
        )

    def test_unknown_tool_reports_rather_than_raises(self):
        assert "Unknown tool" in execute_tool("rm_rf", {})


class TestVerifyEdits:
    TITLES = {"Some Meta-Analysis", "A New Source"}

    def test_clean_edit_passes(self):
        edited = NOTE.replace("The stack works.", "The stack is unproven.")
        assert verify_edits(NOTE, edited, self.TITLES) is None

    def test_heading_change_is_fatal(self):
        edited = NOTE.replace("## Conclusion", "## Conclusions")
        rej = verify_edits(NOTE, edited, self.TITLES)
        assert rej is not None and rej.fatal
        assert "heading" in rej.reason

    def test_heading_removal_is_fatal(self):
        edited = NOTE.replace("## Conclusion\n\n", "")
        rej = verify_edits(NOTE, edited, self.TITLES)
        assert rej is not None and rej.fatal

    def test_length_collapse_is_fatal(self):
        edited = "# A Report\n\n## Findings\n\n## Conclusion\n"
        rej = verify_edits(NOTE, edited, self.TITLES)
        assert rej is not None and rej.fatal

    def test_invented_link_is_retryable_not_fatal(self):
        edited = NOTE.replace("The stack works.", "See [[A Paper I Made Up]].")
        rej = verify_edits(NOTE, edited, self.TITLES)
        assert rej is not None and not rej.fatal
        assert "A Paper I Made Up" in rej.reason

    def test_link_from_the_source_index_is_allowed(self):
        edited = NOTE.replace("The stack works.", "See [[A New Source]].")
        assert verify_edits(NOTE, edited, self.TITLES) is None

    def test_dropping_an_existing_link_is_allowed(self):
        # Removing a refuted claim legitimately removes its citation.
        edited = NOTE.replace(
            "The combination improved attention (SMD = 0.39) in [[Some Meta-Analysis]].",
            "The combination was never compared against caffeine alone.",
        )
        assert verify_edits(NOTE, edited, self.TITLES) is None

    def test_preexisting_links_are_not_validated(self):
        # The note is full of citations unrelated to this run's sources; none of
        # them may be flagged just because they aren't in `allowed_titles`.
        assert verify_edits(NOTE, NOTE, set()) is None


class TestRenderAnswerBlock:
    def test_sentinels_wrap_the_block(self):
        out = render_answer_block("raw q", "Clean question?", "Answer prose.", date="2026-01-01")
        assert out.startswith(ANSWER_OPEN)
        assert out.rstrip().endswith(ANSWER_CLOSE)

    def test_clean_heading_and_original_question_both_present(self):
        out = render_answer_block("wot about slep", "What about sleep?", "Prose.", date="2026-01-01")
        assert "> [!done] **What about sleep?**" in out
        assert "> *Asked:* wot about slep" in out
        assert "#### What about sleep?" in out

    def test_answer_headings_never_reach_h3(self):
        out = render_answer_block("q?", "Q?", "Prose.", date="2026-01-01")
        assert not any(
            line.startswith("# ") or line.startswith("## ") or line.startswith("### ")
            for line in out.splitlines()
        )

    def test_changelog_lists_originals(self):
        edits = [{"old": "The stack works.", "new": "x", "why": "unsupported"}]
        out = render_answer_block("q?", "Q?", "Prose.", edits, date="2026-01-01")
        assert "1 passage corrected above" in out
        assert "The stack works." in out
        assert "unsupported" in out

    def test_plural_agreement(self):
        edits = [{"old": "a", "new": "b", "why": ""}, {"old": "c", "new": "d", "why": ""}]
        out = render_answer_block("q?", "Q?", "Prose.", edits, date="2026-01-01")
        assert "2 passages corrected above" in out

    def test_rejection_is_surfaced_not_hidden(self):
        out = render_answer_block("q?", "Q?", "Prose.", [], "changed the headings",
                                  date="2026-01-01")
        assert "correction rejected" in out
        assert "changed the headings" in out
        assert "unchanged" in out

    def test_no_edits_no_changelog(self):
        out = render_answer_block("q?", "Q?", "Prose.", [], None, date="2026-01-01")
        assert "corrected above" not in out
        assert "[!quote]" not in out


class TestCleanQuestion:
    def test_wellformed_question_skips_the_call(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("should not have called the model")
        monkeypatch.setattr("researcher.callouts.llm_simple", boom)
        assert clean_question("Does caffeine explain the effect?") == \
            "Does caffeine explain the effect?"

    def test_rambling_question_is_restated(self, monkeypatch):
        monkeypatch.setattr("researcher.callouts.llm_simple",
                            lambda *a, **k: "## Does caffeine explain the effect?\n")
        assert clean_question("you need to reevaluate this and consider that caffeine "
                              "might be the only thing doing anything here") == \
            "Does caffeine explain the effect?"

    def test_model_failure_falls_back_to_the_original(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("offline")
        monkeypatch.setattr("researcher.callouts.llm_simple", boom)
        raw = "some long rambling thing that goes on and on and never asks a question at all"
        assert clean_question(raw) == raw

    def test_overlong_restatement_rejected(self, monkeypatch):
        monkeypatch.setattr("researcher.callouts.llm_simple", lambda *a, **k: "x" * 200)
        raw = "some long rambling thing that goes on and on and never asks anything at all"
        assert clean_question(raw) == raw


class TestApplyCorrectionsGuards:
    def test_disabled_returns_body_untouched(self, monkeypatch):
        monkeypatch.setattr(corrections, "CALLOUT_EDITS_ENABLED", False)
        assert corrections.apply_corrections("q", "a", NOTE, set()) == (NOTE, [], None)

    def test_oversized_note_is_skipped(self, monkeypatch):
        monkeypatch.setattr(corrections, "CORRECTION_DOC_LIMIT", 10)
        assert corrections.apply_corrections("q", "a", NOTE, set()) == (NOTE, [], None)

    def test_loop_failure_leaves_the_note_unedited(self, monkeypatch):
        def boom(**kwargs):
            raise RuntimeError("openrouter down")
        monkeypatch.setattr(corrections, "llm_tool_loop", boom)
        assert corrections.apply_corrections("q", "a", NOTE, set()) == (NOTE, [], None)

    def test_no_edits_wanted_is_a_clean_outcome(self, monkeypatch):
        monkeypatch.setattr(corrections, "llm_tool_loop", lambda **k: "nothing to correct")
        assert corrections.apply_corrections("q", "a", NOTE, set()) == (NOTE, [], None)

    def test_fatal_rejection_does_not_retry(self, monkeypatch):
        calls = []

        def fake_loop(**kwargs):
            calls.append(1)
            # Rewrite a heading — a fatal, non-retryable violation.
            edit_note("## Conclusion", "## Conclusions", "restructured")
            return "done"

        monkeypatch.setattr(corrections, "llm_tool_loop", fake_loop)
        body, edits, rejected = corrections.apply_corrections("q", "a", NOTE, set())
        assert body == NOTE and edits == []
        assert "heading" in rejected
        assert len(calls) == 1  # no second attempt

    def test_retryable_rejection_retries_then_gives_up(self, monkeypatch):
        calls = []

        def fake_loop(**kwargs):
            calls.append(kwargs["prompt"])
            edit_note("The stack works.", "See [[Invented Paper]].", "w")
            return "done"

        monkeypatch.setattr(corrections, "llm_tool_loop", fake_loop)
        monkeypatch.setattr(corrections, "MAX_CORRECTION_ATTEMPTS", 2)
        body, edits, rejected = corrections.apply_corrections("q", "a", NOTE, set())
        assert body == NOTE and edits == []
        assert len(calls) == 2
        # The second attempt is told why the first was refused.
        assert "Invented Paper" in calls[1]
        assert "rejected" in calls[1]

    def test_retry_starts_from_a_pristine_copy(self, monkeypatch):
        seen = []

        def fake_loop(**kwargs):
            seen.append(get_edits()[0])
            edit_note("The stack works.", "See [[Invented Paper]].", "w")
            return "done"

        monkeypatch.setattr(corrections, "llm_tool_loop", fake_loop)
        monkeypatch.setattr(corrections, "MAX_CORRECTION_ATTEMPTS", 2)
        corrections.apply_corrections("q", "a", NOTE, set())
        assert seen == [NOTE, NOTE]  # damage never compounds across attempts

    def test_accepted_edits_come_back(self, monkeypatch):
        def fake_loop(**kwargs):
            edit_note("The stack works.", "The stack is unproven.", "overstated")
            return "done"

        monkeypatch.setattr(corrections, "llm_tool_loop", fake_loop)
        body, edits, rejected = corrections.apply_corrections("q", "a", NOTE, set())
        assert rejected is None
        assert len(edits) == 1
        assert "The stack is unproven." in body
