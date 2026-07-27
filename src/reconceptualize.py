"""
reconceptualize.py — One-off backfill: re-run the concept extractor over reports
that were conceptualized while it could only see the first 15,000 chars.

`_conceptualize` used to pass `report[:SYNTHESIS_RAW_EXCERPT]` to the extractor — a
per-source budget borrowed for a whole report, which cut a comprehensive report in
half. Every report written before that fix has an unmined second half: an audit of
seven conceptualized reports (30k-55k chars of prose) found not one concept drawn
from past ~15k. See DESIGN_NOTES § Concept dedup.

    python src/reconceptualize.py            # DRY RUN: one free moc-tier call per
                                             # report, shows what it would now pick
    python src/reconceptualize.py --apply    # link them in and queue the triggers
    python src/reconceptualize.py --apply --only "Stimulant"   # one report first

Cost: the dry run is extraction only — the `moc` task, i.e. free Gemini. `--apply`
is what spends: it links concepts into the reports and queues a `concept: true`
trigger per genuinely new one, and the watchdog then generates each explainer on the
top model. The dry run prints the count so that bill is a decision, not a surprise.
Concepts already built or already queued are backlinked, never regenerated.

Re-running is safe by construction: the ## Concepts section is rewritten as a union
of old and new picks, an already-linked mention is a protected span so inline links
aren't nested, and _write_concept_trigger / process_concept_trigger both no-op on a
concept that already exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import RESEARCH_PATH
from notes import read_note
from researcher.concepts import (
    _extract_concepts, _conceptualize, _concept_path, _concept_key,
    _pending_concept_terms, _report_prose,
)


def _reports(only: str | None) -> list[Path]:
    """Agent-generated research reports, optionally filtered by a filename substring."""
    if not RESEARCH_PATH.is_dir():
        return []
    out = []
    for path in sorted(RESEARCH_PATH.glob("*.md")):
        if only and only.lower() not in path.name.lower():
            continue
        try:
            fm, _ = read_note(path)
        except Exception:
            continue
        if fm.get("generated") is True:
            out.append(path)
    return out


def _classify(term: str, pending_keys: set[str]) -> str:
    """Whether a pick would cost a generation run, or just a backlink."""
    if _concept_path(term).exists():
        return "exists"
    if _concept_key(term) in pending_keys:
        return "queued"
    return "NEW"


def main(apply: bool, only: str | None):
    reports = _reports(only)
    if not reports:
        print("No generated reports found" + (f" matching {only!r}." if only else "."))
        return

    print(f"{'Re-conceptualizing' if apply else 'DRY RUN over'} {len(reports)} report(s).\n")
    new_total = 0

    for path in reports:
        try:
            fm, body = read_note(path)
        except Exception as e:
            print(f"  {path.name}: unreadable ({e}); skipping")
            continue

        prose = len(_report_prose(body))
        was_cut = prose > 15000
        print(f"\n{path.name}")
        print(f"  {prose:,} chars of prose"
              f"{'  ← more than the old 15,000-char window' if was_cut else ''}")

        if apply:
            _conceptualize(path, body, path.stem)
            continue

        pending_keys = {_concept_key(t) for t in _pending_concept_terms()}
        try:
            picks = _extract_concepts(body)
        except Exception as e:
            print(f"  extraction failed ({e}); skipping")
            continue
        if not picks:
            print("  would pick: nothing")
            continue
        for c in picks:
            status = _classify(c["term"], pending_keys)
            new_total += status == "NEW"
            # Where in the report the mention sits — anything past 15,000 is
            # something the old window structurally could not have picked.
            pos = body.lower().find(c["mention"].lower()) if c["mention"] else -1
            where = f"@{pos:,}" + (" (was unreachable)" if pos > 15000 else "")
            print(f"    [{status:^6}] {c['term']}  {where if pos >= 0 else ''}")

    if not apply:
        print(f"\nDRY RUN — nothing written. {new_total} new concept note(s) would be "
              f"queued, each a paid discovery+synthesis run on the top model.")
        print("Re-run with --apply to link them in and queue the triggers.")
    else:
        print("\nApplied. Queued concept triggers are picked up on the watchdog's "
              "next rescan (within 60s if it's running).")


if __name__ == "__main__":
    argv = sys.argv[1:]
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        only = argv[i + 1] if i + 1 < len(argv) else None
    main(apply="--apply" in argv, only=only)
