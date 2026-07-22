"""Tier 1 — indexer.one_line (MOC entry flattening) and _titlecase_topic."""

from indexer import one_line, _titlecase_topic


class TestOneLine:
    def test_collapses_multiline_to_one(self):
        assert one_line("first line\nsecond line") == "first line second line"

    def test_strips_leading_heading_marker(self):
        assert one_line("# A Heading") == "A Heading"

    def test_strips_bullet_and_quote_markers(self):
        assert one_line("> quoted") == "quoted"
        assert one_line("- bullet") == "bullet"

    def test_collapses_internal_whitespace(self):
        assert one_line("a    b\t\tc") == "a b c"

    def test_truncates_with_ellipsis(self):
        out = one_line("word " * 100, limit=40)
        assert out.endswith("…")
        assert len(out) <= 41

    def test_no_dangling_punctuation_before_ellipsis(self):
        out = one_line("some text, and more, " + "x" * 200, limit=12)
        assert out.endswith("…")
        assert not out.rstrip("…").endswith((",", ";", ":", "-"))

    def test_empty_yields_empty(self):
        assert one_line("") == ""


class TestTitlecaseTopic:
    def test_titlecases_words(self):
        assert _titlecase_topic("sports nutrition") == "Sports Nutrition"

    def test_preserves_acronyms(self):
        assert _titlecase_topic("llm training") == "LLM Training"
        assert _titlecase_topic("ai safety") == "AI Safety"

    def test_mixed(self):
        assert _titlecase_topic("rl for llm agents") == "RL For LLM Agents"
