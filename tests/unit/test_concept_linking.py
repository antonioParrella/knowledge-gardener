"""Tier 1 — concept dedup key (_match_key), the extractor's view of a report
(_report_prose), and inline concept linking (_link_first_mention,
_link_concepts_inline)."""

from config import CONCEPT_REPORT_LIMIT
from researcher import (
    _match_key, _report_prose, _link_first_mention, _link_concepts_inline,
    _append_concepts_section,
)


class TestMatchKey:
    def test_casing_insensitive(self):
        assert _match_key("Dopamine") == _match_key("dopamine")

    def test_hyphen_and_endash_collapse(self):
        # The real drift case: hyphen vs en-dash must map to one key.
        assert _match_key("Chamley-Judd Theorem") == _match_key("Chamley–Judd Theorem")

    def test_extra_spacing_collapses(self):
        assert _match_key("chamley  judd  theorem") == _match_key("Chamley-Judd Theorem")

    def test_distinct_concepts_differ(self):
        assert _match_key("Dopamine") != _match_key("Serotonin")


class TestReportProse:
    def test_strips_trailing_sources(self):
        report = "# R\n\nBody about dopamine.\n\n## Sources\n- [[A Paper]]\n"
        out = _report_prose(report)
        assert "Body about dopamine." in out
        assert "[[A Paper]]" not in out

    def test_strips_related_research_and_concepts(self):
        for heading in ("## Related research", "## Concepts"):
            report = f"# R\n\nBody.\n\n{heading}\n- [[Something]]\n"
            assert "[[Something]]" not in _report_prose(report)

    def test_keeps_a_report_with_no_apparatus_whole(self):
        report = "# R\n\nJust prose, no trailing sections.\n"
        assert _report_prose(report) == report

    def test_late_sections_survive_a_realistic_comprehensive_report(self):
        # The regression: a 30k+ char report used to be cut at 15k, so its whole
        # second half — in the real case, all the pharmacology — was invisible to
        # the extractor and could never be picked or linked.
        filler = "Familial risk and liability-threshold modelling. " * 700  # ~34k chars
        report = (
            f"# Stimulants\n\n## 1. Familial Risk\n{filler}\n\n"
            "## 5. Non-Stimulant Options\n"
            "Guanfacine is an alpha-2A adrenergic agonist.\n\n"
            "## Sources\n- [[A Paper]]\n"
        )
        assert len(report) > 30000
        out = _report_prose(report)
        assert "alpha-2A adrenergic agonist" in out
        assert "[[A Paper]]" not in out

    def test_caps_a_runaway_report(self):
        report = "x" * (CONCEPT_REPORT_LIMIT + 5000)
        assert len(_report_prose(report)) == CONCEPT_REPORT_LIMIT


class TestLinkFirstMention:
    def test_wraps_first_occurrence(self):
        out = _link_first_mention("about dopamine here", "dopamine", "Concept - Dopamine")
        assert out == "about [[Concept - Dopamine|dopamine]] here"

    def test_preserves_report_casing_as_alias(self):
        out = _link_first_mention("about Dopamine here", "dopamine", "Concept - Dopamine")
        assert out == "about [[Concept - Dopamine|Dopamine]] here"

    def test_returns_none_when_absent(self):
        assert _link_first_mention("no match", "dopamine", "Concept - Dopamine") is None

    def test_skips_inside_existing_wikilink(self):
        # mention only occurs inside an existing link -> no clean spot -> None
        line = "see [[Concept - Dopamine]] already"
        assert _link_first_mention(line, "dopamine", "Concept - Dopamine") is None

    def test_skips_inside_code_span(self):
        line = "the var `dopamine` in code"
        assert _link_first_mention(line, "dopamine", "Concept - Dopamine") is None

    def test_only_first_of_several(self):
        out = _link_first_mention("dopamine and dopamine", "dopamine", "Concept - Dopamine")
        assert out == "[[Concept - Dopamine|dopamine]] and dopamine"


class TestAppendConceptsSection:
    def test_appends_when_absent(self):
        out = _append_concepts_section("Body.\n", [{"term": "Dopamine"}])
        assert out.endswith("## Concepts\n- [[Concept - Dopamine]]\n")

    def test_rerun_merges_instead_of_duplicating_the_heading(self):
        # The re-run case: a report conceptualized before the window widened gets a
        # second pass that finds concepts in its previously-unread half.
        first = _append_concepts_section("Body.\n", [{"term": "Heritability"}])
        second = _append_concepts_section(first, [{"term": "Dopamine"}])
        assert second.count("## Concepts") == 1
        assert "- [[Concept - Heritability]]" in second
        assert "- [[Concept - Dopamine]]" in second

    def test_rerun_does_not_duplicate_an_existing_entry(self):
        first = _append_concepts_section("Body.\n", [{"term": "Dopamine"}])
        second = _append_concepts_section(first, [{"term": "dopamine"}])
        assert second.count("[[Concept - ") == 1

    def test_leaves_sources_section_intact(self):
        report = "Body.\n\n## Sources\n- [[A Paper]]\n\n## Concepts\n- [[Concept - X]]\n"
        out = _append_concepts_section(report, [{"term": "Y"}])
        assert "## Sources\n- [[A Paper]]" in out
        assert out.count("## Concepts") == 1
        assert "- [[Concept - X]]" in out and "- [[Concept - Y]]" in out


class TestLinkConceptsInline:
    def test_links_each_concept_once(self):
        report = "Reward prediction relies on dopamine and elasticity of demand.\n"
        concepts = [
            {"term": "Dopamine", "mention": "dopamine"},
            {"term": "Elasticity", "mention": "elasticity of demand"},
        ]
        out = _link_concepts_inline(report, concepts)
        assert "[[Concept - Dopamine|dopamine]]" in out
        assert "[[Concept - Elasticity|elasticity of demand]]" in out

    def test_does_not_link_in_headings(self):
        report = "# All about dopamine\n\nbody with no mention word.\n"
        out = _link_concepts_inline(report, [{"term": "Dopamine", "mention": "dopamine"}])
        # The only occurrence is in the heading -> left unlinked.
        assert "[[Concept - Dopamine" not in out

    def test_leaves_trailing_sources_section_alone(self):
        report = ("Body mentions dopamine once.\n\n"
                  "## Sources\n- [[Some Paper about dopamine]]\n")
        out = _link_concepts_inline(report, [{"term": "Dopamine", "mention": "dopamine"}])
        # Linked in the body...
        assert "[[Concept - Dopamine|dopamine]]" in out
        # ...but the Sources apparatus is untouched.
        assert "- [[Some Paper about dopamine]]" in out
