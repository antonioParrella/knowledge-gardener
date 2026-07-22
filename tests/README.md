# Tests

Lightweight safety net for vibe-coded development — focused on the logic that
actually breaks, not coverage for its own sake. Three tiers:

| Tier | Folder | What | Cost | In default run? |
|------|--------|------|------|-----------------|
| 1 | `unit/` | Pure `str → str` logic: math-delimiter normalisation, tag hygiene, wikilink repair, report-completeness gate, note naming, depth parsing, concept linking, MOC helpers | free, ~0.5s | ✅ |
| 2 | `integration/` | Filesystem behaviour against a throwaway vault (`tmp_vault` fixture): note round-trips, source dedup, MOC surgery, and the clip pipeline with the **LLM mocked** | free, fast | ✅ |
| 3 | `llm/` | **Real LLM calls** — the judgments that can't be mocked: the `usable` gate, citation repair, MOC granularity | a few cents, slow | ❌ opt-in |

## Running

```bash
pip install -r requirements-dev.txt

pytest                       # Tiers 1 + 2 (Tier 3 auto-deselected)
pytest tests/unit            # just Tier 1
pytest -m llm                # Tier 3 only — real LLM, costs money, needs API keys
pytest -m llm tests/llm/test_usable_gate.py   # one real-LLM check
```

Tier 3 is gated by the `llm` marker (`addopts = -m "not llm"` in `pytest.ini`), so
a normal run never spends money. Keys are read from `.env` (auto-loaded by
`config.py`); without them, Tier 3 tests **skip** cleanly rather than error.

## When to reach for Tier 3

Run it deliberately after touching a **prompt** or **model route** — those are the
changes unit tests can't catch:

- edited `prompts/clip_analysis.md` or `clip_system.md` → `test_usable_gate.py`
- edited `research_repair_links.md` or synthesis routing → `test_citation_integrity.py`
- edited `moc_assign*.md` → `test_moc_granularity.py`

## Conventions

- `tests/conftest.py` puts `src/` on the path and provides `tmp_vault` (a real
  on-disk vault with config paths monkeypatched per-module).
- Tier 3 fixtures (bot-wall / article text) live in `tests/llm/fixtures/`.
