---
name: run-tests
description: Run Knowledge Garden tests at the right tier, including paid real-LLM checks after a prompt or model-route change. Use when changing Python code, prompts, routing or gates in this repository, or when preparing a merge. Covers the free default suite, the opt-in LLM suite, and the test to select for each prompt family.
---

# Running tests

The test suite is split by cost and isolation. `pytest.ini` excludes the LLM
tests by default, so a bare `pytest` is free. Install development dependencies
once with `pip install -r requirements-dev.txt`.

```bash
pytest                # Tiers 1 + 2; Tier 3 is auto-deselected
pytest tests/unit     # Tier 1 only
pytest -m llm         # Tier 3 only; real LLM calls, costs money and needs keys
```

Tier 1 covers pure `str → str` logic. Tier 2 exercises filesystem behaviour in
a throwaway vault, with the LLM and network mocked. Tier 3 makes real LLM calls.
`tests/conftest.py` redirects the run ledger and mutes ntfy for every test, so
pipeline tests do not write state or notifications into the live vault.

After changing a prompt or model route, run the matching Tier 3 test rather
than treating the default suite as enough:

- `clip_analysis.md` or `clip_system.md` → `test_usable_gate.py`
- `research_correct.md` or the edit-tool schema → `test_callout_corrections.py`
- `research_repair_links.md` or synthesis routing → `test_citation_integrity.py`
- the MOC-assignment prompt → `test_moc_granularity.py`
