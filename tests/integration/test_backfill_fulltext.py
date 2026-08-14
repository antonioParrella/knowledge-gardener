"""
Tier 2 — the full-text backfill over a throwaway vault, network and LLM mocked.

The three safety properties are what's pinned here, because this script rewrites
notes that finished research reports already cite:

  * the filename never changes, so existing [[wikilinks]] keep resolving;
  * MOCs are never re-assigned — only the existing gloss is refreshed in place;
  * any failure restores the original clip byte-for-byte.
"""

import backfill_fulltext as bf
import fulltext
import notes


ABSTRACT = (
    "Morbid risk of schizophrenia was estimated in first-degree, second-degree and "
    "third-degree relatives of schizophrenia probands compared with relatives of "
    "healthy controls, confirming strong familial aggregation."
)
FULL_TEXT = "Introduction. Methods. Results. Discussion. References. " * 300


def _abstract_clip(vault, stem="Familial Risk of Schizophrenia", **extra):
    fm = {
        "clipped": True,
        "source": "https://pmc.ncbi.nlm.nih.gov/articles/PMC6295975",
        "processed": True,
        "source_type": "research_found",
        "full_text": False,
    }
    fm.update(extra)
    path = vault.INBOX_PATH / f"{stem}.md"
    notes.write_note(path, fm, "## Summary\n\nThin.\n\n---\n\n## Original Content\n\n"
                                "> [!warning] Abstract only — full text could not be "
                                f"retrieved.\n\n## Abstract\n{ABSTRACT}")
    return path


def _moc_with(vault, stem, gloss="Old thin gloss"):
    path = vault.INDEX_PATH / "MOC - Schizophrenia.md"
    notes.write_note(path, {"moc": True, "topic": "Schizophrenia", "note_count": 1},
                     f"# Schizophrenia — Knowledge Index\n\n## Notes\n- [[{stem}]] — {gloss}\n")
    return path


class TestSelection:
    def test_finds_only_abstract_only_clips(self, tmp_vault, monkeypatch):
        monkeypatch.setattr(bf, "INBOX_PATH", tmp_vault.INBOX_PATH)
        _abstract_clip(tmp_vault, "Thin One")
        notes.write_note(tmp_vault.INBOX_PATH / "Rich.md",
                         {"clipped": True, "full_text": True}, "body")
        notes.write_note(tmp_vault.INBOX_PATH / "Plain.md", {"clipped": True}, "body")

        found = [p.stem for p in bf.abstract_only_clips()]
        assert found == ["Thin One"]

    def test_only_filter(self, tmp_vault, monkeypatch):
        monkeypatch.setattr(bf, "INBOX_PATH", tmp_vault.INBOX_PATH)
        _abstract_clip(tmp_vault, "L-Theanine Study")
        _abstract_clip(tmp_vault, "Schizophrenia Study")
        assert [p.stem for p in bf.abstract_only_clips("theanine")] == ["L-Theanine Study"]


class TestStoredAbstract:
    def test_extracts_under_heading(self):
        body = f"> [!warning] Abstract only\n\n## Abstract\n{ABSTRACT}"
        assert bf.stored_abstract(body) == ABSTRACT

    def test_extracts_without_heading(self):
        body = f"> [!warning] Abstract only — full text could not be retrieved.\n\n{ABSTRACT}"
        assert bf.stored_abstract(body) == ABSTRACT

    def test_returns_empty_when_absent(self):
        assert bf.stored_abstract("## Summary\n\nNothing here.") == ""

    def test_stops_at_next_heading(self):
        body = f"## Abstract\n{ABSTRACT}\n\n## Notes\nunrelated trailing section"
        assert "unrelated" not in bf.stored_abstract(body)


class TestKnownIdentifiers:
    def test_prefers_frontmatter_over_url(self):
        ids = bf.known_identifiers({"doi": "10.1093/schbul/sbm032"},
                                   "https://example.com/opaque.pdf")
        assert ids["doi"] == "10.1093/schbul/sbm032"

    def test_falls_back_to_url(self):
        ids = bf.known_identifiers({}, "https://pmc.ncbi.nlm.nih.gov/articles/PMC6295975")
        assert ids["pmcid"] == "PMC6295975"


class TestTitleOverlap:
    def test_paraphrased_title_still_matches(self):
        assert bf._titles_overlap(
            "Familial Risk of Schizophrenia in Relatives",
            "Risk of schizophrenia in relatives of individuals affected by schizophrenia")

    def test_different_paper_rejected(self):
        assert not bf._titles_overlap(
            "L-Theanine for anxiety and stress",
            "Anatomy, Back, Splanchnic Nerve")

    def test_empty_is_not_a_match(self):
        assert not bf._titles_overlap("", "anything at all here")


class TestMocGlossRefresh:
    def test_gloss_is_rewritten_in_place(self, tmp_vault, monkeypatch):
        monkeypatch.setattr(bf, "INDEX_PATH", tmp_vault.INDEX_PATH)
        moc = _moc_with(tmp_vault, "Familial Risk of Schizophrenia")

        touched = bf.refresh_moc_summary("Familial Risk of Schizophrenia",
                                         "Registry study of morbid risk by relatedness",
                                         dry_run=False)

        fm, body = notes.read_note(moc)
        assert touched == "MOC - Schizophrenia"
        assert "Registry study of morbid risk by relatedness" in body
        assert "Old thin gloss" not in body
        assert fm["note_count"] == 1          # unchanged — no entry added or removed
        assert body.count("[[Familial Risk of Schizophrenia]]") == 1

    def test_dry_run_writes_nothing(self, tmp_vault, monkeypatch):
        monkeypatch.setattr(bf, "INDEX_PATH", tmp_vault.INDEX_PATH)
        moc = _moc_with(tmp_vault, "Familial Risk of Schizophrenia")
        before = moc.read_bytes()

        bf.refresh_moc_summary("Familial Risk of Schizophrenia", "new gloss", dry_run=True)

        assert moc.read_bytes() == before

    def test_note_absent_from_every_moc_is_a_noop(self, tmp_vault, monkeypatch):
        monkeypatch.setattr(bf, "INDEX_PATH", tmp_vault.INDEX_PATH)
        _moc_with(tmp_vault, "Some Other Note")
        assert bf.refresh_moc_summary("Not Listed Anywhere", "gloss", dry_run=False) == ""


class TestUpgrade:
    def _mock_clipper(self, monkeypatch, outcome="ok"):
        """Stand in for the analyser: succeed, reject, or crash."""
        import clipper

        def fake(path, fm, body, source_url, content_limit=None, reindex=True):
            assert reindex is False, "backfill must not re-index MOCs"
            if outcome == "reject":
                path.unlink(missing_ok=True)
                return None
            if outcome == "crash":
                raise RuntimeError("model exploded")
            disk_fm, disk_body = notes.read_note(path)
            assert disk_fm.get("preserve_title") is True, "filename must be pinned"
            assert disk_fm.get("processed") is True, (
                "the clip must never sit at processed:false — the live watchdog's "
                "60s rescan would grab it mid-upgrade and analyse it a second time")
            notes.write_note(path, disk_fm, f"Rich analysis from full text.\n\n"
                                            f"---\n\n## Original Content\n\n{disk_body}")
            return path
        monkeypatch.setattr(clipper, "_analyse_clip", fake)

    def test_successful_upgrade_keeps_filename_and_marks_full_text(self, tmp_vault, monkeypatch):
        path = _abstract_clip(tmp_vault)
        self._mock_clipper(monkeypatch)

        ok, detail = bf.upgrade(path, FULL_TEXT, "europepmc:PMC6295975",
                                {"pmcid": "PMC6295975"})

        assert ok and detail == "europepmc:PMC6295975"
        assert path.exists(), "the clip must keep its original filename"
        fm, body = notes.read_note(path)
        assert fm["full_text"] is True
        assert fm["full_text_route"] == "europepmc:PMC6295975"
        assert fm["pmcid"] == "PMC6295975"
        assert fm["backfilled"] is True
        assert "Rich analysis from full text." in body

    def test_rejected_text_restores_original_exactly(self, tmp_vault, monkeypatch):
        path = _abstract_clip(tmp_vault)
        before = path.read_bytes()
        self._mock_clipper(monkeypatch, outcome="reject")

        ok, detail = bf.upgrade(path, FULL_TEXT, "openalex:10.1/x", {})

        assert not ok and "restored" in detail
        assert path.read_bytes() == before, "a rejected upgrade must lose nothing"

    def test_crash_restores_original_exactly(self, tmp_vault, monkeypatch):
        path = _abstract_clip(tmp_vault)
        before = path.read_bytes()
        self._mock_clipper(monkeypatch, outcome="crash")

        ok, detail = bf.upgrade(path, FULL_TEXT, "europepmc:PMC1", {})

        assert not ok and "restored" in detail
        assert path.read_bytes() == before


class TestDriver:
    def _run(self, monkeypatch, tmp_vault, argv, retrieve):
        monkeypatch.setattr(bf, "INBOX_PATH", tmp_vault.INBOX_PATH)
        monkeypatch.setattr(bf, "INDEX_PATH", tmp_vault.INDEX_PATH)
        monkeypatch.setattr(fulltext, "retrieve", retrieve)
        monkeypatch.setattr("sys.argv", ["backfill_fulltext.py"] + argv)
        return bf.main()

    def test_dry_run_changes_nothing(self, tmp_vault, monkeypatch, capsys):
        path = _abstract_clip(tmp_vault)
        before = path.read_bytes()

        self._run(monkeypatch, tmp_vault, [],
                  lambda url, reference="", identifiers=None: (FULL_TEXT, "europepmc:PMC6295975"))

        assert path.read_bytes() == before
        out = capsys.readouterr().out
        assert "WOULD" in out and "Would recover: 1" in out

    def test_apply_upgrades_the_clip(self, tmp_vault, monkeypatch, capsys):
        import clipper
        path = _abstract_clip(tmp_vault)
        _moc_with(tmp_vault, path.stem)

        def fake_clip(p, fm, body, source_url, content_limit=None, reindex=True):
            disk_fm, disk_body = notes.read_note(p)
            notes.write_note(p, disk_fm,
                             f"Rich gloss line.\n\n---\n\n## Original Content\n\n{disk_body}")
            return p
        monkeypatch.setattr(clipper, "_analyse_clip", fake_clip)

        self._run(monkeypatch, tmp_vault, ["--apply"],
                  lambda url, reference="", identifiers=None: (FULL_TEXT, "bioc:PMC6295975"))

        fm, _ = notes.read_note(path)
        assert fm["full_text"] is True
        assert "Recovered: 1" in capsys.readouterr().out

    def test_clip_without_identifier_is_skipped_not_failed(self, tmp_vault, monkeypatch, capsys):
        _abstract_clip(tmp_vault, "Opaque", source="https://example.com/page")

        def explode(url, reference="", identifiers=None):
            raise AssertionError("ladder must not run without an identifier")

        self._run(monkeypatch, tmp_vault, [], explode)
        out = capsys.readouterr().out
        assert "no identifier" in out and "skipped: 1" in out

    def test_abstract_is_passed_as_the_identity_reference(self, tmp_vault, monkeypatch):
        _abstract_clip(tmp_vault)
        seen = {}

        def spy(url, reference="", identifiers=None):
            seen["reference"] = reference
            return "", "miss"

        self._run(monkeypatch, tmp_vault, [], spy)
        assert seen["reference"] == ABSTRACT

    def test_limit_is_respected(self, tmp_vault, monkeypatch, capsys):
        for n in range(4):
            _abstract_clip(tmp_vault, f"Clip {n}")
        self._run(monkeypatch, tmp_vault, ["--limit", "2"],
                  lambda url, reference="", identifiers=None: ("", "miss"))
        assert "Candidates: 2" in capsys.readouterr().out


class TestGlossExtraction:
    """
    The first version of this script took the analysis's first paragraph as the MOC
    gloss. A clip analysis OPENS with a markdown heading, so 55 live MOC entries came
    out reading `— ### Executive Summary`. These pin the shape of the fix.
    """

    ANALYSIS = (
        "### Executive Summary\n\n"
        "This study reports morbid risk of schizophrenia across three degrees of "
        "relatedness.\n\n"
        "### Key Ideas\n\n- Morbid risk\n\n"
        "---\n\n## Original Content\n\nraw source text here"
    )

    def test_skips_the_leading_heading(self):
        g = bf.gloss_from_analysis(self.ANALYSIS)
        assert g.startswith("This study reports morbid risk")
        assert "###" not in g and "Executive Summary" not in g

    def test_ignores_original_content(self):
        assert "raw source text" not in bf.gloss_from_analysis(self.ANALYSIS)

    def test_is_a_single_line(self):
        assert "\n" not in bf.gloss_from_analysis(self.ANALYSIS)

    def test_skips_quotes_and_tables(self):
        assert bf.gloss_from_analysis("> [!warning] x\n\n| a | b |\n\nReal prose here.") \
            == "Real prose here."

    def test_empty_analysis_yields_empty(self):
        assert bf.gloss_from_analysis("") == ""
        assert bf.gloss_from_analysis("### Only A Heading") == ""


class TestGlossFormatting:
    def test_entry_keeps_a_space_before_the_dash(self, tmp_vault, monkeypatch):
        # `]]—` glues the dash to the link in Obsidian's rendering.
        monkeypatch.setattr(bf, "INDEX_PATH", tmp_vault.INDEX_PATH)
        moc = _moc_with(tmp_vault, "Some Note")
        bf.refresh_moc_summary("Some Note", "A clean prose gloss.", dry_run=False)
        _, body = notes.read_note(moc)
        assert "- [[Some Note]] — A clean prose gloss." in body
        assert "]]—" not in body

    def test_heading_gloss_is_flattened_not_written_raw(self, tmp_vault, monkeypatch):
        monkeypatch.setattr(bf, "INDEX_PATH", tmp_vault.INDEX_PATH)
        moc = _moc_with(tmp_vault, "Some Note")
        bf.refresh_moc_summary("Some Note", "### Executive Summary", dry_run=False)
        _, body = notes.read_note(moc)
        assert "###" not in body

    def test_multiline_summary_never_breaks_the_entry(self, tmp_vault, monkeypatch):
        # A MOC entry is one list item; a newline in it would split the list.
        monkeypatch.setattr(bf, "INDEX_PATH", tmp_vault.INDEX_PATH)
        moc = _moc_with(tmp_vault, "Some Note")
        bf.refresh_moc_summary("Some Note", "line one\nline two", dry_run=False)
        _, body = notes.read_note(moc)
        entry = [l for l in body.splitlines() if l.startswith("- [[Some Note]]")]
        assert len(entry) == 1 and "line one line two" in entry[0]


class TestRefreshGlossesMode:
    def test_repairs_a_damaged_entry_without_touching_note_count(self, tmp_vault, monkeypatch):
        monkeypatch.setattr(bf, "INBOX_PATH", tmp_vault.INBOX_PATH)
        monkeypatch.setattr(bf, "INDEX_PATH", tmp_vault.INDEX_PATH)
        path = _abstract_clip(tmp_vault, "Backfilled Note", backfilled=True, full_text=True)
        notes.write_note(path, notes.read_note(path)[0],
                         "### Executive Summary\n\nGenuine prose summary.\n")
        moc = tmp_vault.INDEX_PATH / "MOC - Schizophrenia.md"
        notes.write_note(moc, {"moc": True, "topic": "Schizophrenia", "note_count": 1},
                         "# X\n\n## Notes\n- [[Backfilled Note]]— ### Executive Summary\n")

        bf.refresh_glosses(apply=True)

        fm, body = notes.read_note(moc)
        assert "- [[Backfilled Note]] — Genuine prose summary." in body
        assert fm["note_count"] == 1

    def test_dry_run_writes_nothing(self, tmp_vault, monkeypatch):
        monkeypatch.setattr(bf, "INBOX_PATH", tmp_vault.INBOX_PATH)
        monkeypatch.setattr(bf, "INDEX_PATH", tmp_vault.INDEX_PATH)
        path = _abstract_clip(tmp_vault, "Backfilled Note", backfilled=True)
        notes.write_note(path, notes.read_note(path)[0], "### H\n\nProse.\n")
        moc = _moc_with(tmp_vault, "Backfilled Note")
        before = moc.read_bytes()

        bf.refresh_glosses(apply=False)

        assert moc.read_bytes() == before
