"""Tier 1 — research note naming (_title_from_report, _research_note_name) and
depth-checkbox parsing (_depth_from_body)."""

import researcher
from researcher import _title_from_report, _research_note_name, _depth_from_body


class TestTitleFromReport:
    def test_uses_h1(self):
        assert _title_from_report("# World Models and Their Origins\n\nbody") == \
            "World Models and Their Origins"

    def test_trims_subtitle_after_colon(self):
        # Head before ':' is >= 15 chars, so the subtitle is dropped.
        report = "# Generative Diffusion Models: A Critical Examination of Recent Work\n\nbody"
        assert _title_from_report(report) == "Generative Diffusion Models"

    def test_keeps_full_title_when_head_too_short(self):
        # Head before ':' is under 15 chars, so the whole title is kept.
        report = "# RL: Reinforcement Learning Foundations\n\nbody"
        assert _title_from_report(report) == "RL Reinforcement Learning Foundations"

    def test_strips_emphasis_markers(self):
        assert _title_from_report("# *Bold Title Here*\n\nbody") == "Bold Title Here"

    def test_returns_none_without_h1(self):
        assert _title_from_report("no heading here, just prose") is None

    def test_strips_illegal_filename_chars(self):
        title = _title_from_report('# Quantum "Computing" Breakthroughs')
        assert '"' not in title


class TestResearchNoteName:
    def test_explicit_output_wins(self):
        fm = {"output": "Research - Custom.md"}
        assert _research_note_name(fm, "topic", "# Some H1\n\nbody") == "Research - Custom.md"

    def test_explicit_output_gets_md_suffix(self):
        assert _research_note_name({"output": "Research - Custom"}, "t", "# H1 Title Here\n\nb") \
            == "Research - Custom.md"

    def test_falls_back_to_h1(self):
        fm = {}
        report = "# Diffusion Models and Their Origins\n\nbody"
        assert _research_note_name(fm, "topic", report) == \
            "Research - Diffusion Models and Their Origins.md"

    def test_falls_back_to_topic_without_h1(self):
        assert _research_note_name({}, "My Topic", "no heading") == "Research - My Topic.md"


class TestDepthFromBody:
    def test_reads_ticked_box(self):
        body = "## Depth\n- [ ] standard\n- [x] deep\n- [ ] comprehensive"
        assert _depth_from_body(body) == "deep"

    def test_none_when_nothing_ticked(self):
        body = "## Depth\n- [ ] standard\n- [ ] deep"
        assert _depth_from_body(body) is None

    def test_scoped_to_depth_section(self):
        # A ticked Acceptance-Criteria box must not be read as a depth choice.
        body = ("## Depth\n- [ ] standard\n- [ ] deep\n\n"
                "## Acceptance Criteria\n- [x] comprehensive coverage of the field")
        assert _depth_from_body(body) is None

    def test_first_ticked_wins(self):
        body = "## Depth\n- [x] standard\n- [x] deep"
        assert _depth_from_body(body) == "standard"

    def test_case_insensitive_x(self):
        body = "## Depth\n- [X] comprehensive"
        assert _depth_from_body(body) == "comprehensive"
