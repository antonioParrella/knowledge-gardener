"""
backfill_fulltext.py — retry full-text retrieval for clips that settled for an abstract.

The open-access ladder (fulltext.py) only helps sources gathered *after* it existed.
This is the one-off pass over the ones already in the vault: 299 of 650
research-gathered clips are `full_text: false`, and the graded sample says roughly a
third of those are freely available through the ladder — led by PubMed Central,
which is open access and was never actually paywalled, just bot-walled.

    python src/backfill_fulltext.py                    # dry-run over everything
    python src/backfill_fulltext.py --limit 20         # dry-run a sample first
    python src/backfill_fulltext.py --only theanine    # one topic
    python src/backfill_fulltext.py --resolve-titles   # also try title -> identifier
    python src/backfill_fulltext.py --apply            # commit (costs money)

The dry run does the whole *retrieval* half for real — network reads only, no model
calls, no writes — because retrieval is the uncertain part. What it prints is what
--apply will act on.

── What --apply does to a clip ──
Recovered full text replaces the abstract stub, and the clip is re-analysed so its
summary and takeaways come from the paper rather than 200 words of abstract. That
re-analysis is the actual point: the notes exist, they are just thin.

Three properties make that safe to run over a live vault:

  * **The filename never changes.** `preserve_title` is set, so every `[[wikilink]]`
    in every report that already cites this clip keeps resolving. A backfill that
    renamed notes would silently break the citation graph it was trying to enrich.
  * **MOCs are not touched.** `reindex=False`; the note is already indexed, and
    re-running assign_to_moc on a richer analysis can pick a *different* MOC, leaving
    one note in two with both note_counts wrong. Only the existing entry's one-line
    gloss is refreshed, in place.
  * **A failure restores the original byte-for-byte.** The clip is read into memory
    first and written back on any error, including a discarded `usable: false`
    verdict. The worst case is the abstract-only clip you already had.
"""

import argparse
import re
import sys
import time
from pathlib import Path

from config import INBOX_PATH, INDEX_PATH, PAPER_CONTENT_LIMIT
from notes import read_note, write_note
import fulltext


# ── Selecting candidates ──────────────────────────────────────────────────────

def abstract_only_clips(only: str = "") -> list[Path]:
    """Every clip that settled for an abstract, oldest first for stable ordering."""
    out = []
    for path in sorted(INBOX_PATH.glob("*.md")):
        try:
            fm, _ = read_note(path)
        except Exception:
            continue
        if fm.get("full_text") is not False:
            continue
        if only and only.lower() not in path.stem.lower():
            continue
        out.append(path)
    return out


def stored_abstract(body: str) -> str:
    """
    The abstract an abstract-only clip kept under its warning callout. This is the
    identity gate's fingerprint, so it is worth pulling out precisely rather than
    handing the ladder the whole note (which contains the model's own analysis and
    would match almost anything on the topic).
    """
    m = re.search(r"##\s*Abstract\s*\n(.+?)(?=\n##|\Z)", body, re.S)
    if m:
        return m.group(1).strip()
    # Older clips put the abstract straight under the callout with no heading.
    m = re.search(r">\s*\[!warning\][^\n]*\n+(.+?)(?=\n##|\Z)", body, re.S)
    return m.group(1).strip() if m else ""


def known_identifiers(fm: dict, url: str) -> dict:
    """Identifiers already recorded on the clip, plus whatever the URL carries."""
    stored = {k: fm[k] for k in ("doi", "pmcid", "pmid", "arxiv") if fm.get(k)}
    return fulltext.extract_identifiers(url, stored)


# ── Title resolution (opt-in) ─────────────────────────────────────────────────

def resolve_by_title(title: str) -> dict:
    """
    Last resort for a clip whose URL carries no identifier — 55% of the backlog,
    because the pipeline used to discard the DOI the search result handed it.

    Deliberately opt-in (--resolve-titles). Measured at 25% precision on its own:
    it will happily return a *different* paper on the same topic. What makes it
    usable at all here is that everything it produces still has to clear the
    ladder's identity and structure gates, and a title match is only accepted when
    the candidate title genuinely overlaps the one we hold.
    """
    ids: dict[str, str] = {}
    try:
        r = fulltext._get("https://api.crossref.org/works",
                          params={"query.bibliographic": title, "rows": 3,
                                  "select": "DOI,title",
                                  "mailto": fulltext.FULLTEXT_CONTACT_EMAIL})
        for item in (r.json().get("message", {}).get("items", []) if r else []):
            if _titles_overlap(title, (item.get("title") or [""])[0]):
                ids["doi"] = item.get("DOI", "")
                break
    except Exception:
        pass

    try:
        r = fulltext._get(f"{fulltext._EPMC_BASE}/search",
                          params={"query": f'TITLE:"{title}"', "format": "json",
                                  "resultType": "core", "pageSize": 3})
        for res in (r.json().get("resultList", {}).get("result", []) if r else []):
            if _titles_overlap(title, res.get("title") or ""):
                if res.get("pmcid"):
                    ids["pmcid"] = res["pmcid"].upper()
                ids.setdefault("doi", res.get("doi") or "")
                break
    except Exception:
        pass

    return {k: v for k, v in ids.items() if v}


def _titles_overlap(a: str, b: str, threshold: float = 0.6) -> bool:
    """
    Token overlap between two titles. The vault stores an LLM-rewritten title, not
    the publisher's, so an exact comparison would reject nearly everything — but a
    bare search hit must not be trusted either. This is the middle ground.
    """
    ta = {w for w in re.findall(r"[a-z]{4,}", a.lower())}
    tb = {w for w in re.findall(r"[a-z]{4,}", b.lower())}
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= threshold


# ── MOC gloss refresh ─────────────────────────────────────────────────────────

def refresh_moc_summary(stem: str, summary: str, dry_run: bool) -> str:
    """
    Update the one-line gloss on this note's EXISTING MOC entry, wherever it is.

    Entries are only rewritten, never added or moved — no assignment, no note_count
    change, no new MOC. That keeps a content upgrade from turning into an index
    reshuffle. Returns the MOC name touched, or "".
    """
    if not summary or not INDEX_PATH.exists():
        return ""
    link = f"[[{stem}]]"
    for moc_path in sorted(INDEX_PATH.glob("MOC - *.md")):
        try:
            fm, body = read_note(moc_path)
        except Exception:
            continue
        # re.escape already handles the [[ ]]; the trailing gloss is optional and may
        # use either dash, so an entry written by hand is matched too.
        pattern = re.compile(rf"^(- {re.escape(link)})\s*(?:[—-].*)?$", re.M)
        if not pattern.search(body):
            continue
        if not dry_run:
            one = summary.replace("\n", " ").strip()
            write_note(moc_path, fm, pattern.sub(lambda m: f"{m.group(1)}— {one}", body))
        return moc_path.stem
    return ""


# ── The upgrade ───────────────────────────────────────────────────────────────

def upgrade(path: Path, text: str, route: str, ids: dict) -> tuple[bool, str]:
    """
    Replace an abstract-only clip's body with recovered full text and re-analyse it.
    Restores the file untouched on any failure. Returns (ok, detail).
    """
    import clipper
    import telemetry

    original_bytes = path.read_bytes()          # the whole safety net, in one line
    fm, _ = read_note(path)

    new_fm = dict(fm)
    new_fm.update({
        "full_text": True,
        "full_text_route": route,
        "preserve_title": True,   # existing [[wikilinks]] must keep resolving
        "backfilled": True,
    })
    for key in ("doi", "pmcid", "pmid", "arxiv"):
        if ids.get(key):
            new_fm[key] = ids[key]

    try:
        # `_analyse_clip` rather than `process_clipped_note`, so `processed` never
        # goes False on disk. The watchdog is normally running while this backfill
        # is: its 60s rescan picks up any clip with `processed: false`, and a clip
        # sitting in that state for the length of an analysis call would be grabbed
        # and processed a second time concurrently. Going straight to the analyser
        # keeps the note continuously marked processed, so the rescan never sees it.
        write_note(path, new_fm, text)
        with telemetry.run("clip", path.stem):
            result = clipper._analyse_clip(path, new_fm, text, str(fm.get("source", "")),
                                           PAPER_CONTENT_LIMIT, reindex=False)
    except Exception as e:
        path.write_bytes(original_bytes)
        return False, f"re-analysis crashed ({type(e).__name__}: {e}) — clip restored"

    if not result or not result.exists():
        # `usable: false` (the analyser judged the recovered text not to be the
        # document) unlinks the file. Put the original back exactly as it was.
        path.write_bytes(original_bytes)
        return False, "analyser rejected the recovered text — clip restored"

    return True, route


# ── Driver ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite clips (default is a dry run)")
    ap.add_argument("--only", default="", help="only clips whose title contains this")
    ap.add_argument("--limit", type=int, default=0, help="stop after N clips")
    ap.add_argument("--resolve-titles", action="store_true",
                    help="for clips with no identifier, try to resolve one from the "
                         "title (lower precision; still gated)")
    args = ap.parse_args()

    clips = abstract_only_clips(args.only)
    if args.limit:
        clips = clips[:args.limit]

    mode = "APPLY" if args.apply else "DRY RUN"
    print("=" * 72)
    print(f"Full-text backfill — {mode}")
    print(f"Clippings: {INBOX_PATH}")
    print(f"Candidates: {len(clips)} abstract-only clip(s)"
          + (f" matching '{args.only}'" if args.only else ""))
    if args.resolve_titles:
        print("Title resolution: ON (lower precision; identity + structure gates still apply)")
    print("=" * 72)
    if not clips:
        print("Nothing to do.")
        return 0

    recovered = failed = skipped = 0
    started = time.time()

    for i, path in enumerate(clips, 1):
        try:
            fm, body = read_note(path)
        except Exception as e:
            print(f"[{i:>3}/{len(clips)}] SKIP  {path.stem[:52]} — unreadable ({e})")
            skipped += 1
            continue

        url = str(fm.get("source", "")).strip()
        reference = stored_abstract(body) or path.stem
        ids = known_identifiers(fm, url)

        if not ids and args.resolve_titles:
            ids = resolve_by_title(path.stem)

        if not ids:
            print(f"[{i:>3}/{len(clips)}] --    {path.stem[:52]} — no identifier"
                  + ("" if args.resolve_titles else " (try --resolve-titles)"))
            skipped += 1
            continue

        try:
            text, route = fulltext.retrieve(url, reference=reference, identifiers=ids)
        except Exception as e:
            print(f"[{i:>3}/{len(clips)}] --    {path.stem[:52]} — ladder error ({e})")
            failed += 1
            continue

        if not text:
            print(f"[{i:>3}/{len(clips)}] --    {path.stem[:52]} — {route[:40]}")
            failed += 1
            continue

        if not args.apply:
            print(f"[{i:>3}/{len(clips)}] WOULD {path.stem[:52]} — "
                  f"{len(text):,} chars via {route}")
            recovered += 1
            continue

        ok, detail = upgrade(path, text, route, ids)
        if ok:
            summary_fm, summary_body = read_note(path)
            gloss = summary_body.split("\n\n")[0][:200]
            moc = refresh_moc_summary(path.stem, gloss, dry_run=False)
            print(f"[{i:>3}/{len(clips)}] OK    {path.stem[:52]} — {len(text):,} chars "
                  f"via {detail}" + (f" (gloss refreshed in {moc})" if moc else ""))
            recovered += 1
        else:
            print(f"[{i:>3}/{len(clips)}] FAIL  {path.stem[:52]} — {detail}")
            failed += 1

    elapsed = time.time() - started
    print("=" * 72)
    verb = "Recovered" if args.apply else "Would recover"
    print(f"{verb}: {recovered}   no full text: {failed}   skipped: {skipped}"
          f"   ({elapsed/60:.1f} min)")
    if not args.apply and recovered:
        # One clip-task call each. Those run on the free Gemini tier until its daily
        # bucket is spent, then a few tenths of a cent apiece on DeepSeek V4 Flash.
        print(f"\nRe-run with --apply to rewrite {recovered} clip(s).")
        print(f"Cost: {recovered} clip-analysis call(s) — free on Gemini's daily "
              f"quota, ~$0.002 each beyond it (worst case ~${recovered * 0.002:.2f}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
