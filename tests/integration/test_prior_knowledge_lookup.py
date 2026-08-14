"""
Tier 2 — the MOC-graph prior-knowledge lookup, against a real Index/ folder.

Phase ① used to flatten every MOC into one candidate list and hand the model the
whole thing (~34k tokens at 900 notes). It now walks the graph: shortlist MOCs by
name, then read only those. What matters is that the second call sees a SMALL,
CORRECTLY SCOPED candidate list, and that a bad shortlist can't quietly cost the
run its prior knowledge. The LLM is mocked — this is about scoping, not judgement.
"""

import indexer
import notes


def _moc(index_path, topic, entries, note_count=None):
    body = (f"# {topic} — Knowledge Index\n\n## Notes\n"
            + "".join(f"- [[{e}]] — summary of {e}\n" for e in entries))
    notes.write_note(
        index_path / f"MOC - {topic}.md",
        {"moc": True, "topic": topic, "note_count": note_count or len(entries)},
        body,
    )


def _vault_with_mocs(tmp_vault):
    _moc(tmp_vault.INDEX_PATH, "Metabolic Health", ["Fasting RCT", "Insulin Review"])
    _moc(tmp_vault.INDEX_PATH, "Sports Nutrition", ["Caffeine Trial"])
    _moc(tmp_vault.INDEX_PATH, "Massive MIMO", ["Beamforming Survey"])
    return tmp_vault


class TestMocTopics:
    def test_reads_topic_and_count_from_frontmatter(self, tmp_vault):
        _vault_with_mocs(tmp_vault)
        assert dict(indexer._moc_topics()) == {
            "Metabolic Health": 2, "Sports Nutrition": 1, "Massive MIMO": 1,
        }

    def test_falls_back_to_filename_when_topic_missing(self, tmp_vault):
        notes.write_note(tmp_vault.INDEX_PATH / "MOC - Orphan Topic.md",
                         {"moc": True}, "# Orphan Topic\n\n## Notes\n")
        assert ("Orphan Topic", 0) in indexer._moc_topics()


class TestCatalogScoping:
    def test_only_reads_the_shortlisted_mocs(self, tmp_vault):
        _vault_with_mocs(tmp_vault)
        titles = {t for t, _ in indexer._read_moc_catalog(only={"Metabolic Health"})}
        assert titles == {"Fasting RCT", "Insulin Review"}

    def test_none_reads_every_moc(self, tmp_vault):
        _vault_with_mocs(tmp_vault)
        titles = {t for t, _ in indexer._read_moc_catalog()}
        assert titles == {"Fasting RCT", "Insulin Review", "Caffeine Trial",
                          "Beamforming Survey"}


class TestShortlist:
    def test_keeps_only_real_moc_names(self, tmp_vault, monkeypatch):
        _vault_with_mocs(tmp_vault)
        monkeypatch.setattr(indexer, "llm_simple",
                            lambda **k: '["Metabolic Health", "Invented Topic"]')
        assert indexer._shortlist_mocs("time-restricted eating") == {"Metabolic Health"}

    def test_lexical_match_survives_a_useless_model_answer(self, tmp_vault, monkeypatch):
        """An empty response is exactly the failure that cost a real run its context."""
        _vault_with_mocs(tmp_vault)
        monkeypatch.setattr(indexer, "llm_simple", lambda **k: "")
        assert indexer._shortlist_mocs("metabolic syndrome in adults") == {"Metabolic Health"}

    def test_lexical_match_survives_a_raising_model(self, tmp_vault, monkeypatch):
        _vault_with_mocs(tmp_vault)

        def boom(**k):
            raise RuntimeError("all providers failed")

        monkeypatch.setattr(indexer, "llm_simple", boom)
        assert indexer._shortlist_mocs("sports nutrition timing") == {"Sports Nutrition"}

    def test_lexical_pass_does_not_dilute_a_good_model_answer(self, tmp_vault, monkeypatch):
        """
        Fallback, not union. Unioned, "Time-Restricted Eating" drags in
        `MOC - Time Series` on the word "time" and spends candidate slots on notes
        that cannot be relevant.
        """
        _vault_with_mocs(tmp_vault)
        _moc(tmp_vault.INDEX_PATH, "Time Series", ["ARIMA Primer"])
        monkeypatch.setattr(indexer, "llm_simple", lambda **k: '["Metabolic Health"]')
        assert indexer._shortlist_mocs("time-restricted eating") == {"Metabolic Health"}

    def test_short_words_do_not_match_lexically(self, tmp_vault, monkeypatch):
        """'in', 'and', 'the' must not drag in unrelated MOCs."""
        _moc(tmp_vault.INDEX_PATH, "The And Of", ["Junk"])
        monkeypatch.setattr(indexer, "llm_simple", lambda **k: "[]")
        assert indexer._shortlist_mocs("the cost of and in") == set()

    def test_respects_the_shortlist_cap(self, tmp_vault, monkeypatch):
        for i in range(20):
            _moc(tmp_vault.INDEX_PATH, f"Topic{i}", [f"Note{i}"])
        picked = [f"Topic{i}" for i in range(20)]
        monkeypatch.setattr(indexer, "llm_simple", lambda **k: __import__("json").dumps(picked))
        assert len(indexer._shortlist_mocs("everything")) == indexer.MOC_SHORTLIST_MAX

    def test_empty_vault_yields_empty(self, tmp_vault, monkeypatch):
        monkeypatch.setattr(indexer, "llm_simple", lambda **k: '["Anything"]')
        assert indexer._shortlist_mocs("a topic") == set()


class TestFindRelevantClippings:
    def test_second_call_sees_only_shortlisted_notes(self, tmp_vault, monkeypatch):
        """The whole point: the note-picking prompt must not contain the vault."""
        _vault_with_mocs(tmp_vault)
        notes.write_note(tmp_vault.INBOX_PATH / "Fasting RCT.md", {}, "Analysis body.")
        seen = {}

        def fake_llm(**kwargs):
            prompt = kwargs["prompt"]
            if "Topic indexes" in prompt:          # call 1 — the shortlist
                return '["Metabolic Health"]'
            seen["prompt"] = prompt                 # call 2 — the note pick
            return '["Fasting RCT"]'

        monkeypatch.setattr(indexer, "llm_simple", fake_llm)
        results = indexer.find_relevant_clippings("time-restricted eating")

        assert [r["title"] for r in results] == ["Fasting RCT"]
        assert "Insulin Review" in seen["prompt"]        # same MOC — a candidate
        assert "Beamforming Survey" not in seen["prompt"]  # unrelated MOC — not sent
        assert "Caffeine Trial" not in seen["prompt"]

    def test_no_shortlist_skips_the_second_call_entirely(self, tmp_vault, monkeypatch):
        _vault_with_mocs(tmp_vault)
        calls = {"n": 0}

        def fake_llm(**kwargs):
            calls["n"] += 1
            return "[]"

        monkeypatch.setattr(indexer, "llm_simple", fake_llm)
        assert indexer.find_relevant_clippings("quantum chromodynamics") == []
        assert calls["n"] == 1  # shortlist only; no catalog call was made

    def test_prior_reports_and_concepts_are_never_candidates(self, tmp_vault, monkeypatch):
        _moc(tmp_vault.INDEX_PATH, "Metabolic Health",
             ["Fasting RCT", "Research - Fasting", "Concept - Autophagy"])
        notes.write_note(tmp_vault.RESEARCH_PATH / "Research - Fasting.md", {}, "report")
        notes.write_note(tmp_vault.CONCEPTS_PATH / "Concept - Autophagy.md", {}, "concept")
        notes.write_note(tmp_vault.INBOX_PATH / "Fasting RCT.md", {}, "Analysis body.")
        seen = {}

        def fake_llm(**kwargs):
            if "Topic indexes" in kwargs["prompt"]:
                return '["Metabolic Health"]'
            seen["prompt"] = kwargs["prompt"]
            return '["Fasting RCT"]'

        monkeypatch.setattr(indexer, "llm_simple", fake_llm)
        indexer.find_relevant_clippings("fasting")
        assert "Research - Fasting" not in seen["prompt"]
        assert "Concept - Autophagy" not in seen["prompt"]

    def test_unparseable_pick_returns_empty_without_raising(self, tmp_vault, monkeypatch):
        _vault_with_mocs(tmp_vault)
        monkeypatch.setattr(
            indexer, "llm_simple",
            lambda **k: '["Metabolic Health"]' if "Topic indexes" in k["prompt"] else "",
        )
        assert indexer.find_relevant_clippings("time-restricted eating") == []
