"""
clean_junk_clips.py — one-time removal of junk clips.

Before the clipper learned to reject non-content (the `usable` flag), two kinds of
garbage got saved as real, indexed notes:
  * raw PDF / binary byte dumps — a PDF that downloaded fine but whose text couldn't
    be extracted, then got re-fetched and dumped in as "text" (%PDF-… endobj stream …)
  * bot-wall / CAPTCHA / paywall interstitials — an anti-scraping page that returned
    HTTP 200, so its "Making sure you're not a bot!" text became the clip body

This script finds those clips by inspecting their captured `## Original Content`
(and the analyzer's own write-up, which usually admits it couldn't read the page),
then removes each one surgically: it deletes the note and reuses reset_clips' MOC
cleanup so the `[[link]]` is stripped from its MOC, `note_count` is decremented, an
emptied MOC is deleted, and _index.md is tidied. Abstract-only clips are NOT junk
(they hold a real abstract) and are never flagged.

    python src/clean_junk_clips.py            # DRY RUN: list what would be removed
    python src/clean_junk_clips.py --apply    # delete the junk clips + fix MOCs/index
"""

import sys
from pathlib import Path

from config import INBOX_PATH, RESEARCH_PATH, CONCEPTS_PATH
from notes import read_note
from reset_clips import clean_mocs, clean_master_index

HEADER = "## Original Content"

# A short captured page containing one of these is an anti-scraping / paywall / login
# interstitial, not an article. Gated on length (< INTERSTITIAL_MAX_CHARS) because a
# genuine, long article *about* bot detection would contain the same words — the whole
# reason we don't keyword-gate the live pipeline. Here it's only a candidate filter;
# every hit is printed for review before anything is deleted.
INTERSTITIAL_MARKERS = (
    "making sure you're not a bot", "enable javascript", "verify you are human",
    "are you a robot", "recaptcha", "proof-of-work", "anti-scraping", "access denied",
    "just a moment", "checking your browser", "attention required", "anubis",
    "cloudflare", "requests from your network", "unusual traffic",
)
INTERSTITIAL_MAX_CHARS = 3000

# The analyzer, fed junk, tends to say so in its write-up. These are phrases it used on
# the known-bad clips — distinctive admissions of "this wasn't real content".
ANALYSIS_FAILURE_MARKERS = (
    "raw pdf binary", "not extractable text", "no meaningful analysis",
    "cannot be analyzed", "could not be analyzed", "unable to analyze",
    "unable to analyse", "anti-scraping challenge", "no substantive article",
    "content is not accessible", "content unavailable", "content unreadable",
    "unreadable pdf", "no article content", "verification requirement",
    "cannot be decoded", "is garbled", "not a bot",
)


def _snippet(text: str, n: int = 110) -> str:
    """One-line preview of text for the review report."""
    return " ".join(text.split())[:n]


def classify(md_file: Path) -> tuple[str, str] | None:
    """
    Return (reason, evidence-snippet) if the clip is junk, else None.

    Inspects the captured `## Original Content` primarily; the analyzer's write-up is
    a secondary signal. Abstract-only fallbacks (a real abstract under a warning
    callout) match none of these and are left alone.
    """
    try:
        fm, body = read_note(md_file)
    except Exception:
        return None

    parts = body.split(HEADER, 1)
    analysis = parts[0]
    original = parts[1].strip() if len(parts) > 1 else ""
    a_low, o_low = analysis.lower(), original.lower()

    # 1. Raw PDF structure tokens in the captured content.
    if "%pdf-" in o_low or ("endobj" in o_low and "stream" in o_low):
        return ("raw-pdf", _snippet(original))

    # 2. Mojibake: content that is largely replacement chars is decoded binary.
    if original:
        repl = original.count("�")
        if repl >= 20 and repl / len(original) > 0.02:
            return ("binary-garbage", f"{repl} replacement chars — {_snippet(original)}")

    # 3. Short interstitial page saved as content.
    if 0 < len(original) < INTERSTITIAL_MAX_CHARS and any(m in o_low for m in INTERSTITIAL_MARKERS):
        return ("interstitial", _snippet(original))

    # 4. The analyzer's own write-up admits the content wasn't real.
    if any(m in a_low for m in ANALYSIS_FAILURE_MARKERS):
        return ("analysis-flagged", _snippet(analysis))

    return None


def find_junk() -> list[tuple[Path, str, str]]:
    """Return [(path, reason, snippet)] for every junk clip in Clippings/."""
    junk: list[tuple[Path, str, str]] = []
    for md_file in sorted(INBOX_PATH.glob("*.md")):
        verdict = classify(md_file)
        if verdict:
            junk.append((md_file, verdict[0], verdict[1]))
    return junk


def find_citations(stems: set[str]) -> dict[str, list[str]]:
    """
    Which Research/ and Concepts/ notes cite any of these stems as [[wikilinks]].
    These become dead links once the clips are deleted — reported, not auto-edited
    (report prose is the user's to fix), so nothing is silently rewritten.
    """
    hits: dict[str, list[str]] = {}
    for folder in (RESEARCH_PATH, CONCEPTS_PATH):
        if not folder.exists():
            continue
        for md_file in folder.glob("*.md"):
            try:
                _, body = read_note(md_file)
            except Exception:
                continue
            cited = [s for s in stems if f"[[{s}]]" in body]
            if cited:
                hits[md_file.name] = sorted(cited)
    return hits


def main():
    args = set(sys.argv[1:])
    apply = "--apply" in args

    print("=" * 60)
    print("Clean Junk Clips" + ("" if apply else "  (DRY RUN — nothing will be changed)"))
    print(f"Clippings: {INBOX_PATH}")
    print("=" * 60)

    junk = find_junk()
    total = len(list(INBOX_PATH.glob("*.md")))
    if not junk:
        print(f"\nScanned {total} clips — no junk found.")
        return

    by_reason: dict[str, int] = {}
    print(f"\nFound {len(junk)} junk clip(s) of {total} scanned:\n")
    for path, reason, snippet in junk:
        by_reason[reason] = by_reason.get(reason, 0) + 1
        print(f"  [{reason}] {path.name}")
        print(f"      └ {snippet}")
    print("\nBy reason: " + ", ".join(f"{k}={v}" for k, v in sorted(by_reason.items())))

    stems = {p.stem for p, _, _ in junk}
    citations = find_citations(stems)
    if citations:
        print(f"\n⚠  {len(citations)} report/concept note(s) cite a clip being removed "
              "(links will go dead — review these, they are NOT auto-edited):")
        for note, cited in sorted(citations.items()):
            print(f"  - {note}: {', '.join(cited)}")

    if not apply:
        print(f"\nDRY RUN. Re-run with --apply to delete these {len(junk)} clip(s) "
              "and clean their MOC/index entries.")
        return

    print(f"\nDeleting {len(junk)} junk clip(s)…")
    for path, _, _ in junk:
        path.unlink(missing_ok=True)
        print(f"  [deleted] {path.name}")

    deleted_topics = clean_mocs(stems, dry_run=False)
    clean_master_index(deleted_topics, dry_run=False)
    print(f"\nDone. Removed {len(junk)} clips; cleaned MOCs and _index.md.")


if __name__ == "__main__":
    main()
