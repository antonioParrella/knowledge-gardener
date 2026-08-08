"""
Tier 1 — the open-access full-text ladder (src/fulltext.py).

Two things are pinned here, and they are the two the design rests on:

  * identifier extraction, because the whole ladder is keyed off it — a DOI that
    keeps a trailing paren from a URL query string 404s every downstream lookup; and
  * the identity gate + trust rule, because that is what stops an inferred route
    from admitting a *plausible wrong document* into the vault. The clipper's
    `usable` gate asks "is this content?", not "is this THE content?", so a wrong
    paper that reads fine sails through it and ends up cited in a report. Precision
    is the binding constraint here, not recall, and these tests are where that is
    enforced without spending a network call.
"""

import pytest

import fulltext
from fulltext import (
    extract_identifiers, identity_score, passes_identity, Candidate,
)


class TestExtractIdentifiers:
    def test_pmcid_from_url(self):
        ids = extract_identifiers("https://pmc.ncbi.nlm.nih.gov/articles/PMC6295975")
        assert ids["pmcid"] == "PMC6295975"

    def test_pmcid_uppercased(self):
        assert extract_identifiers("https://x/pmc123456/")["pmcid"] == "PMC123456"

    def test_pmid_from_pubmed_url(self):
        ids = extract_identifiers("https://pubmed.ncbi.nlm.nih.gov/36944092")
        assert ids["pmid"] == "36944092"

    def test_doi_from_doi_org(self):
        ids = extract_identifiers("https://doi.org/10.1016/j.neubiorev.2018.07.010")
        assert ids["doi"] == "10.1016/j.neubiorev.2018.07.010"

    def test_doi_embedded_in_publisher_path(self):
        ids = extract_identifiers(
            "https://www.tandfonline.com/doi/pdf/10.1080/25785648.2023.2168939")
        assert ids["doi"] == "10.1080/25785648.2023.2168939"

    def test_doi_strips_query_string(self):
        # '?download=true' must not become part of the DOI.
        ids = extract_identifiers(
            "https://www.tandfonline.com/doi/pdf/10.1080/25785648.2023.2168939?download=true")
        assert ids["doi"] == "10.1080/25785648.2023.2168939"

    def test_doi_strips_trailing_punctuation(self):
        ids = extract_identifiers("see https://doi.org/10.1002/hbm.22850.")
        assert ids["doi"] == "10.1002/hbm.22850"

    def test_arxiv_abs_and_pdf_both_match(self):
        assert extract_identifiers("https://arxiv.org/abs/2209.08778")["arxiv"] == "2209.08778"
        assert extract_identifiers("https://arxiv.org/pdf/2209.08778v1")["arxiv"] == "2209.08778"

    def test_no_identifier_yields_empty(self):
        assert extract_identifiers("https://www.btselem.org/topic/apartheid") == {}

    def test_extra_supplies_what_url_lacks(self):
        # The real win: an opaque publisher PDF path plus the DOI the search knew.
        ids = extract_identifiers(
            "https://academic.oup.com/schizbull/article-pdf/33/6/1373/sbm032.pdf",
            {"doi": "10.1093/schbul/sbm032"},
        )
        assert ids["doi"] == "10.1093/schbul/sbm032"

    def test_extra_doi_accepts_full_url_form(self):
        ids = extract_identifiers("https://x/paper.pdf",
                                  {"doi": "https://doi.org/10.1234/abc.def"})
        assert ids["doi"] == "10.1234/abc.def"

    def test_extra_overrides_url_derived(self):
        ids = extract_identifiers("https://doi.org/10.1/wrong",
                                  {"doi": "10.2/right"})
        assert ids["doi"] == "10.2/right"

    def test_unknown_extra_keys_ignored(self):
        ids = extract_identifiers("https://x/y", {"isbn": "123", "doi": "10.1/a"})
        assert "isbn" not in ids and ids["doi"] == "10.1/a"


class TestIdentityScore:
    ABSTRACT = (
        "Morbid risk of schizophrenia was estimated in first-degree, second-degree "
        "and third-degree relatives of schizophrenia probands compared with relatives "
        "of healthy controls, confirming strong familial aggregation."
    )

    def test_matching_document_scores_high(self):
        body = ("Schizophrenia morbid risk among relatives. We compared first-degree, "
                "second-degree and third-degree relatives of probands against healthy "
                "controls and found familial aggregation. Methods, results, discussion.")
        assert identity_score(self.ABSTRACT, body) >= 0.75

    def test_different_document_scores_low(self):
        body = ("Anatomy of the splanchnic nerves. The greater splanchnic nerve "
                "originates from thoracic ganglia and carries preganglionic fibres.")
        assert identity_score(self.ABSTRACT, body) < 0.4

    def test_returns_none_when_reference_too_thin(self):
        # Not 0.0 — an unusable fingerprint must be distinguishable from a rejection.
        assert identity_score("ADHD", "any body text at all") is None

    def test_case_insensitive(self):
        assert identity_score(self.ABSTRACT, self.ABSTRACT.upper()) == 1.0


class TestPassesIdentity:
    ABSTRACT = TestIdentityScore.ABSTRACT

    def test_accepts_matching(self):
        ok, why = passes_identity(self.ABSTRACT, self.ABSTRACT + " methods results")
        assert ok and "identity" in why

    def test_rejects_mismatched(self):
        ok, why = passes_identity(self.ABSTRACT, "entirely unrelated prose about boats")
        assert not ok

    def test_rejects_when_no_fingerprint(self):
        # Refuse rather than admit: an ungated inferred route is the exact failure
        # mode the gate exists to prevent.
        ok, why = passes_identity("x", "some long body text here")
        assert not ok and "fingerprint" in why

    def test_threshold_is_honoured(self):
        body = self.ABSTRACT
        assert passes_identity(body, body, threshold=1.0)[0]
        assert not passes_identity(body, "relatives of probands", threshold=0.99)[0]


class TestLadderGating:
    """The trust rule: identifier-derived routes bypass the gate, inferred ones don't."""

    ABSTRACT = TestIdentityScore.ABSTRACT
    LONG_WRONG = "Splanchnic nerve anatomy. " * 500      # long, plausible, wrong
    LONG_RIGHT = ABSTRACT + " methods results discussion. " * 200

    @pytest.fixture(autouse=True)
    def _no_network(self, monkeypatch):
        monkeypatch.setattr(fulltext, "resolve_pmcid", lambda ids: None)

    def _run(self, monkeypatch, routes):
        monkeypatch.setattr(fulltext, "ROUTES", tuple(routes))
        return fulltext.retrieve("https://doi.org/10.1016/j.test.2024.001", reference=self.ABSTRACT)

    def test_trusted_route_bypasses_gate(self, monkeypatch):
        # A PMCID read off the URL cannot fetch a different document, so even text
        # that would fail the lexical gate is accepted.
        route = lambda ids: Candidate(self.LONG_WRONG, "europepmc:PMC1", trusted=True)
        text, why = self._run(monkeypatch, [("europepmc", route)])
        assert text == self.LONG_WRONG and why == "europepmc:PMC1"

    def test_inferred_route_is_gated_out(self, monkeypatch):
        route = lambda ids: Candidate(self.LONG_WRONG, "unpaywall:10.1016/j.test.2024.001", trusted=False)
        text, why = self._run(monkeypatch, [("unpaywall", route)])
        assert text == "" and "rejected" in why

    def test_inferred_route_accepted_when_it_matches(self, monkeypatch):
        route = lambda ids: Candidate(self.LONG_RIGHT, "unpaywall:10.1016/j.test.2024.001", trusted=False)
        text, why = self._run(monkeypatch, [("unpaywall", route)])
        assert text == self.LONG_RIGHT and why == "unpaywall:10.1016/j.test.2024.001"

    def test_falls_through_to_later_route(self, monkeypatch):
        bad = lambda ids: Candidate(self.LONG_WRONG, "unpaywall:10.1016/j.test.2024.001", trusted=False)
        good = lambda ids: Candidate(self.LONG_RIGHT, "openalex:10.1016/j.test.2024.001", trusted=False)
        text, why = self._run(monkeypatch, [("unpaywall", bad), ("openalex", good)])
        assert text == self.LONG_RIGHT and why == "openalex:10.1016/j.test.2024.001"

    def test_route_exception_never_escapes(self, monkeypatch):
        def boom(ids):
            raise RuntimeError("network exploded")
        good = lambda ids: Candidate(self.LONG_RIGHT, "openalex:10.1016/j.test.2024.001", trusted=False)
        text, _ = self._run(monkeypatch, [("unpaywall", boom), ("openalex", good)])
        assert text == self.LONG_RIGHT

    def test_all_routes_missing_reports_cleanly(self, monkeypatch):
        text, why = self._run(monkeypatch, [("unpaywall", lambda ids: None)])
        assert text == "" and "miss" in why

    def test_no_identifiers_short_circuits(self, monkeypatch):
        monkeypatch.setattr(fulltext, "ROUTES", ())
        text, why = fulltext.retrieve("https://example.com/page", reference=self.ABSTRACT)
        assert text == "" and why == "no identifiers"

    def test_disabled_flag_is_respected(self, monkeypatch):
        monkeypatch.setattr(fulltext, "FULLTEXT_ENABLED", False)
        text, why = fulltext.retrieve("https://doi.org/10.1016/j.test.2024.001", reference=self.ABSTRACT)
        assert text == "" and why == "disabled"


class TestRouteIdWriteback:
    """A PMCID the ladder resolved must survive into the clip's frontmatter."""

    def test_pmc_routes_yield_pmcid(self):
        from researcher.sources import _route_ids
        assert _route_ids("europepmc:PMC123") == {"pmcid": "PMC123"}
        assert _route_ids("bioc:PMC123") == {"pmcid": "PMC123"}

    def test_non_pmc_route_yields_nothing(self):
        from researcher.sources import _route_ids
        assert _route_ids("unpaywall:10.1016/j.test.2024.001") == {}

    def test_malformed_route_label_is_safe(self):
        from researcher.sources import _route_ids
        assert _route_ids("") == {} and _route_ids("disabled") == {}


class TestArticleStructure:
    """
    The second gate on inferred routes. A repository landing page quotes the
    abstract verbatim, so it scores ~100% on identity while being nothing but that
    abstract plus institutional chrome — accepting one would REPLACE a clean
    abstract-only clip with a worse version of itself.
    """

    LANDING = (
        "Effects of family history on the risk of schizophrenia - Aarhus University "
        "Skip to main navigation Skip to search Home Profiles Research units Projects "
        "Abstract Although a family history of schizophrenia is the best-established "
        "risk factor, environmental factors may also be important. Original language "
        "English Journal The New England Journal of Medicine We use cookies to help "
        "provide and enhance our service. Log in to Pure. Contact us."
    )
    ARTICLE = (
        "Introduction. Schizophrenia risk is familial. Methods. We linked registers. "
        "Participants were drawn from the cohort. Statistical analysis used Poisson "
        "regression. Results. Risk rose with relatedness. Discussion. These findings "
        "confirm aggregation. Conclusions. Limitations. References."
    )

    def test_landing_page_lacks_structure(self):
        assert not fulltext.has_article_structure(self.LANDING)

    def test_real_article_has_structure(self):
        assert fulltext.has_article_structure(self.ARTICLE)

    def test_counts_distinct_sections_not_repeats(self):
        # Ten mentions of one heading is not ten sections.
        assert fulltext.article_structure("Results. " * 10) == 1

    def test_empty_text_is_safe(self):
        assert fulltext.article_structure("") == 0
        assert not fulltext.has_article_structure(None)


class TestLandingPageRejection:
    """The two gates are AND-ed: passing identity is not enough on its own."""

    ABSTRACT = TestIdentityScore.ABSTRACT

    def test_identity_pass_but_no_structure_is_rejected(self, monkeypatch):
        monkeypatch.setattr(fulltext, "resolve_pmcid", lambda ids: None)
        # Identical to the reference => identity 100%, but it is only an abstract.
        landing = (self.ABSTRACT + " Skip to main navigation. We use cookies. ") * 30
        monkeypatch.setattr(fulltext, "ROUTES", (
            ("openalex", lambda ids: Candidate(landing, "openalex:10.1016/j.t.2024.1",
                                               trusted=False)),
        ))
        text, why = fulltext.retrieve("https://doi.org/10.1016/j.t.2024.1",
                                      reference=self.ABSTRACT)
        assert text == "" and "landing page" in why

    def test_trusted_route_is_not_structure_checked(self, monkeypatch):
        # PMC full-text XML is full text by construction; a structure check there
        # would only add false rejections.
        monkeypatch.setattr(fulltext, "resolve_pmcid", lambda ids: None)
        thin = "no section headings here at all. " * 300
        monkeypatch.setattr(fulltext, "ROUTES", (
            ("europepmc", lambda ids: Candidate(thin, "europepmc:PMC1", trusted=True)),
        ))
        text, _ = fulltext.retrieve("https://pmc.ncbi.nlm.nih.gov/articles/PMC1",
                                    reference=self.ABSTRACT)
        assert text == thin
