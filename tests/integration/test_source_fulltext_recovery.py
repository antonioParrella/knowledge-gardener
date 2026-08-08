"""
Tier 2 — researcher.sources._process_source with the network and LLM mocked.

The three-attempt cascade is the point: direct fetch, then the open-access ladder,
then abstract-only. Before the ladder existed, attempt 1 failing meant attempt 3,
which is how 46% of gathered sources became abstract-only clips — including ones
sitting in PubMed Central, entirely free, behind nothing but a bot wall.

Also pins the identifier write-back. A clip that records its DOI/PMCID can be
re-resolved cheaply forever; one that doesn't can only be found again by an
unreliable title search, which is the state 55% of the vault's abstract-only clips
are currently in.
"""

import notes
import fulltext
from researcher import sources


ABSTRACT = (
    "Morbid risk of schizophrenia was estimated in first-degree, second-degree and "
    "third-degree relatives of schizophrenia probands compared with relatives of "
    "healthy controls, confirming strong familial aggregation."
)
FULL_TEXT = ABSTRACT + " Methods. Results. Discussion. References. " * 200


def _entry(**kw):
    base = {
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC6295975",
        "title": "Familial Risk of Schizophrenia",
        "kind": "pdf",
        "reason": "core estimate",
        "abstract": ABSTRACT,
    }
    base.update(kw)
    return base


def _mock_clipper(monkeypatch, usable=True):
    """Stand in for the clipper: keep the stub and mark it processed, or discard it."""
    def fake(path, content_limit=None):
        if not usable:
            path.unlink(missing_ok=True)
            return None
        fm, body = notes.read_note(path)
        fm["processed"] = True
        notes.write_note(path, fm, f"Analysis of the source.\n\n## Original Content\n\n{body}")
        return path
    monkeypatch.setattr(sources, "process_clipped_note", fake)
    monkeypatch.setattr(sources, "find_existing_source", lambda url, **kw: None)


def _no_direct_fetch(monkeypatch):
    """Attempt 1 fails — the case the ladder exists for."""
    monkeypatch.setattr(sources, "extract_paper_text", lambda url, **kw: "")


def test_ladder_recovers_when_direct_fetch_fails(tmp_vault, monkeypatch):
    _mock_clipper(monkeypatch)
    _no_direct_fetch(monkeypatch)
    monkeypatch.setattr(fulltext, "retrieve",
                        lambda url, reference="", identifiers=None: (FULL_TEXT, "europepmc:PMC6295975"))

    result = sources._process_source(_entry())

    assert result is not None
    clip = tmp_vault.INBOX_PATH / "Familial Risk of Schizophrenia.md"
    fm, body = notes.read_note(clip)
    assert fm["full_text"] is True
    assert fm["full_text_route"] == "europepmc:PMC6295975"
    assert "Methods. Results. Discussion." in body


def test_pmcid_from_ladder_is_persisted(tmp_vault, monkeypatch):
    # The URL carries a PMCID; it must land in frontmatter so a later backfill can
    # re-resolve this source without a title search.
    _mock_clipper(monkeypatch)
    _no_direct_fetch(monkeypatch)
    monkeypatch.setattr(fulltext, "retrieve",
                        lambda url, reference="", identifiers=None: (FULL_TEXT, "bioc:PMC6295975"))

    sources._process_source(_entry())

    fm, _ = notes.read_note(tmp_vault.INBOX_PATH / "Familial Risk of Schizophrenia.md")
    assert fm["pmcid"] == "PMC6295975"


def test_queued_doi_survives_into_frontmatter(tmp_vault, monkeypatch):
    # The headline fix: an opaque publisher PDF path plus the DOI discovery knew.
    _mock_clipper(monkeypatch)
    _no_direct_fetch(monkeypatch)
    monkeypatch.setattr(fulltext, "retrieve",
                        lambda url, reference="", identifiers=None: ("", "no open-access full text"))

    sources._process_source(_entry(
        url="https://academic.oup.com/schizbull/article-pdf/33/6/1373/sbm032.pdf",
        identifiers={"doi": "10.1093/schbul/sbm032"},
    ))

    fm, body = notes.read_note(tmp_vault.INBOX_PATH / "Familial Risk of Schizophrenia.md")
    assert fm["doi"] == "10.1093/schbul/sbm032"
    assert fm["full_text"] is False          # still abstract-only …
    assert "Abstract only" in body           # … but now recoverable later


def test_falls_back_to_abstract_when_ladder_finds_nothing(tmp_vault, monkeypatch):
    _mock_clipper(monkeypatch)
    _no_direct_fetch(monkeypatch)
    monkeypatch.setattr(fulltext, "retrieve",
                        lambda url, reference="", identifiers=None: ("", "unpaywall: miss"))

    result = sources._process_source(_entry())

    assert result is not None  # a thin citable clip, exactly as before
    fm, body = notes.read_note(tmp_vault.INBOX_PATH / "Familial Risk of Schizophrenia.md")
    assert fm["full_text"] is False
    assert "full_text_route" not in fm
    assert ABSTRACT in body


def test_ladder_runs_when_direct_fetch_returns_garbage(tmp_vault, monkeypatch):
    """
    The bot-wall case. Attempt 1 returns text, but the clipper judges it not to be
    the document and discards the stub — previously the end of the line. Now the
    ladder gets its turn.
    """
    monkeypatch.setattr(sources, "find_existing_source", lambda url, **kw: None)
    monkeypatch.setattr(sources, "extract_paper_text",
                        lambda url, **kw: "Just a moment... enable JavaScript to continue")
    monkeypatch.setattr(fulltext, "retrieve",
                        lambda url, reference="", identifiers=None: (FULL_TEXT, "europepmc:PMC6295975"))

    calls = {"n": 0}
    def fake_clip(path, content_limit=None):
        calls["n"] += 1
        fm, body = notes.read_note(path)
        if "Just a moment" in body:      # the usable gate rejecting a bot wall
            path.unlink(missing_ok=True)
            return None
        fm["processed"] = True
        notes.write_note(path, fm, f"Analysis.\n\n## Original Content\n\n{body}")
        return path
    monkeypatch.setattr(sources, "process_clipped_note", fake_clip)

    result = sources._process_source(_entry())

    assert calls["n"] == 2               # direct rejected, then the ladder's text
    assert result is not None
    fm, _ = notes.read_note(tmp_vault.INBOX_PATH / "Familial Risk of Schizophrenia.md")
    assert fm["full_text"] is True


def test_ladder_failure_never_breaks_the_run(tmp_vault, monkeypatch):
    """A crash anywhere in retrieval must degrade to abstract-only, not raise."""
    _mock_clipper(monkeypatch)
    _no_direct_fetch(monkeypatch)

    def boom(url, reference="", identifiers=None):
        raise RuntimeError("network exploded")
    monkeypatch.setattr(fulltext, "retrieve", boom)

    try:
        sources._process_source(_entry())
    except RuntimeError:
        # fulltext.retrieve is documented as never raising; if a future edit breaks
        # that promise, _process_source must still not take the run down with it.
        raise AssertionError("_process_source propagated a retrieval crash")


def test_reference_passed_to_gate_is_the_abstract(tmp_vault, monkeypatch):
    """The identity gate is only as good as the fingerprint it is handed."""
    _mock_clipper(monkeypatch)
    _no_direct_fetch(monkeypatch)
    seen = {}
    def spy(url, reference="", identifiers=None):
        seen["reference"] = reference
        return FULL_TEXT, "europepmc:PMC1"
    monkeypatch.setattr(fulltext, "retrieve", spy)

    sources._process_source(_entry())
    assert seen["reference"] == ABSTRACT


def test_title_used_as_reference_when_no_abstract(tmp_vault, monkeypatch):
    _mock_clipper(monkeypatch)
    _no_direct_fetch(monkeypatch)
    seen = {}
    def spy(url, reference="", identifiers=None):
        seen["reference"] = reference
        return "", "miss"
    monkeypatch.setattr(fulltext, "retrieve", spy)

    sources._process_source(_entry(abstract=""))
    assert seen["reference"] == "Familial Risk of Schizophrenia"
