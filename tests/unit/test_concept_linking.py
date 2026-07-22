"""Tier 1 — concept dedup key (_match_key) and inline concept linking
(_link_first_mention, _link_concepts_inline)."""

from researcher import _match_key, _link_first_mention, _link_concepts_inline


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
