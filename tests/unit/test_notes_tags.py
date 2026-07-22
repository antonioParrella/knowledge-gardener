"""Tier 1 — notes.normalize_tag / normalize_tags (deterministic tag hygiene)."""

from notes import normalize_tag, normalize_tags


class TestNormalizeTag:
    def test_lowercases(self):
        assert normalize_tag("Machine") == "machine"

    def test_spaces_to_hyphen(self):
        assert normalize_tag("Tax Evasion") == "tax-evasion"

    def test_underscores_to_hyphen(self):
        assert normalize_tag("wealth_tax") == "wealth-tax"

    def test_slashes_to_hyphen(self):
        assert normalize_tag("machine/learning") == "machine-learning"

    def test_strips_leading_hash(self):
        assert normalize_tag("#ML") == "ml"

    def test_trims_whitespace(self):
        assert normalize_tag("  Tax Evasion ") == "tax-evasion"

    def test_no_leading_or_trailing_hyphen(self):
        assert normalize_tag("  -weird- ") == "weird"

    def test_collapses_runs_of_separators(self):
        assert normalize_tag("a   b__c") == "a-b-c"

    def test_does_not_split_concatenation(self):
        # Deliberately NOT split — that's the vocabulary's job, not this function's.
        assert normalize_tag("machinelearning") == "machinelearning"


class TestNormalizeTags:
    def test_dedups_preserving_order(self):
        assert normalize_tags(["ML", "ml", "AI"]) == ["ml", "ai"]

    def test_drops_empties(self):
        assert normalize_tags(["", "#", "  ", "ok"]) == ["ok"]

    def test_accepts_single_string(self):
        assert normalize_tags("Tax Evasion") == ["tax-evasion"]

    def test_none_yields_empty(self):
        assert normalize_tags(None) == []

    def test_canonicalises_each(self):
        assert normalize_tags(["Tax Evasion", "wealth_tax"]) == ["tax-evasion", "wealth-tax"]
