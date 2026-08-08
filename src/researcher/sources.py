"""
Phase ③ — turn a queued source into an indexed clipping.

A discovery-queued source (arXiv/OpenAlex paper or web page) is fetched, written
to Clippings/ as a stub, and run through the clipper pipeline so it becomes an
ordinary indexed clipping (reusable by future research). The clipper's analyzer
is the gate that decides whether the retrieved text is really the document — a
bot-wall / CAPTCHA / raw-PDF byte-dump is discarded and we fall back to the
abstract. Shared by both research runs and concept runs.
"""

from pathlib import Path

from config import (
    INBOX_PATH, CLIP_CONTENT_LIMIT, PAPER_CONTENT_LIMIT, SYNTHESIS_RAW_EXCERPT,
)
from notes import read_note, write_note, safe_filename, today
from academic import extract_paper_text
from clipper import process_clipped_note, find_existing_source
import fulltext


def _load_existing(path: Path) -> dict | None:
    """Load an already-vaulted note's analysis for reuse in synthesis."""
    try:
        _, body = read_note(path)
    except Exception:
        return None
    analysis = body.split("## Original Content")[0].strip()
    return {"title": path.stem, "analysis": analysis}


def _clip_source(url: str, title: str, kind: str, body: str, full_text: bool,
                 identifiers: dict | None = None, route: str = "") -> dict | None:
    """
    Write a queued source's text as a clip stub and run it through the clipper
    pipeline, returning {title, analysis, raw} — or None if the clipper discarded it
    (e.g. its analyzer judged the content not to be the real document, or a dup race).

    Any identifiers we resolved are persisted into the clip's frontmatter. This is
    the fix for the pipeline's most expensive omission: a search result knows the
    DOI, the URL it hands us frequently does not, and dropping it on the floor is
    why 55% of the vault's abstract-only clips cannot be re-resolved later without
    an unreliable title search. Recording it makes every such clip cheaply
    recoverable by a future backfill.
    """
    clip_path = INBOX_PATH / f"{safe_filename(title)}.md"
    if clip_path.exists():
        # name collision with an unrelated note — disambiguate
        clip_path = INBOX_PATH / f"{safe_filename(title)} ({today()}).md"

    frontmatter = {
        "clipped": True,
        "source": url,
        "date": today(),
        "processed": False,
        "source_type": "research_found",
        "full_text": full_text,
    }
    for key in ("doi", "pmcid", "pmid", "arxiv"):
        if (identifiers or {}).get(key):
            frontmatter[key] = identifiers[key]
    if route:
        frontmatter["full_text_route"] = route

    write_note(clip_path, frontmatter=frontmatter, body=body)

    limit = PAPER_CONTENT_LIMIT if (kind == "pdf" and full_text) else CLIP_CONTENT_LIMIT
    final_path = process_clipped_note(clip_path, content_limit=limit)
    if not final_path or not final_path.exists():
        # Never leave the stub behind. The clipper unlinks on its own discard paths
        # (duplicate, usable:false), but a JSON-parse failure returns None with the
        # stub still on disk, and the full-text→abstract fallback abandons the base
        # file. Either way the watchdog's backlog scan would re-ingest it as a second
        # clip for the same source — the exact origin of the overnight duplicates.
        clip_path.unlink(missing_ok=True)
        return None

    _, out = read_note(final_path)
    analysis = out.split("## Original Content")[0].strip()
    raw = ""
    if "## Original Content" in out:
        raw = out.split("## Original Content", 1)[1].strip()[:SYNTHESIS_RAW_EXCERPT]
    return {"title": final_path.stem, "analysis": analysis, "raw": raw}


def _process_source(entry: dict) -> dict | None:
    """
    Fetch a queued source's full text, write it as a clip, and run the clipper
    analysis pipeline so it becomes an indexed clipping.
    Returns {title, analysis, raw} or None on failure.
    """
    url = entry["url"]
    kind = entry.get("kind", "web")
    title = entry.get("title") or url
    abstract = (entry.get("abstract") or "").strip()

    # Safety-net dedup (queue_source already checked, but iCloud sync can race).
    existing = find_existing_source(url)
    if existing:
        print(f"[research] Source already in vault: {existing.stem}")
        return _load_existing(existing)

    ids = fulltext.extract_identifiers(url, entry.get("identifiers"))
    # The identity gate matches against the abstract; the title is a weak stand-in
    # used only when discovery didn't supply one.
    reference = abstract or title

    # ── Attempt 1: the URL we were given, fetched directly ────────────────────
    # Kept first because it is one request and already succeeds about half the time.
    # `landing_url` is deliberately NOT the PDF URL: passing the same value made
    # academic.py's landing-page fallback unreachable (its guard is
    # `landing_url != pdf_url`), so a kind="pdf" source whose download failed had no
    # fallback whatsoever. The OA ladder below is now that fallback, and a better one.
    if kind == "pdf":
        text = extract_paper_text(url)
    else:
        from web_tools import fetch_url
        text = fetch_url(url)

    if text and not text.startswith("Failed to fetch"):
        # The clipper's analyzer judges whether the retrieved text is the real
        # document; if it isn't (raw PDF bytes, a bot-wall / CAPTCHA / paywall
        # interstitial), process_clipped_note discards the stub and _clip_source
        # returns None — so we fall through to the ladder.
        result = _clip_source(url, title, kind, text, full_text=True, identifiers=ids)
        if result:
            return result
        print(f"[research] Direct fetch unusable — trying open-access routes: {title}")

    # ── Attempt 2: the open-access ladder ─────────────────────────────────────
    # Derives identifiers and walks the free key-less APIs in order of measured
    # precision, gating every inferred candidate on document identity.
    #
    # fulltext.retrieve already swallows per-route failures, so this except is for
    # the layer below that contract — an import error, a DNS stack blowing up in a
    # way requests doesn't wrap. Full-text recovery is a bonus path: it must be able
    # to fail in any manner at all without costing us the abstract-only clip we
    # would otherwise have written, let alone the whole research run.
    try:
        oa_text, route = fulltext.retrieve(url, reference=reference, identifiers=ids)
    except Exception as e:
        print(f"[research] Open-access ladder crashed ({type(e).__name__}: {e}): {title}")
        oa_text, route = "", "ladder crashed"
    if oa_text:
        # Re-derive: the ladder may have upgraded a PMID/DOI to a PMCID, which is
        # worth persisting — it is the key to the two highest-precision routes.
        ids = fulltext.extract_identifiers(url, {**ids, **_route_ids(route)})
        result = _clip_source(url, title, kind, oa_text, full_text=True,
                              identifiers=ids, route=route)
        if result:
            return result
        print(f"[research] Open-access full text unusable ({route}): {title}")
    else:
        print(f"[research] No open-access full text ({route}): {title}")

    # ── Attempt 3: abstract-only ──────────────────────────────────────────────
    # Full text couldn't be retrieved anywhere (genuinely paywalled), or every
    # candidate was judged not to be the real document. Still kept and citable,
    # just thinner — and now carrying its identifiers for a later retry.
    if abstract:
        body = (
            "> [!warning] Abstract only — full text could not be retrieved.\n\n"
            f"## Abstract\n{abstract}"
        )
        return _clip_source(url, title, kind, body, full_text=False, identifiers=ids)

    print(f"[research] No usable source and no abstract available: {url}")
    return None


def _route_ids(route: str) -> dict:
    """
    Pull the identifier back out of a ladder route label ("europepmc:PMC12345").
    The ladder resolves PMIDs and DOIs up to PMCIDs internally; this is how that
    resolution survives into the clip's frontmatter.
    """
    if ":" not in (route or ""):
        return {}
    kind, _, value = route.partition(":")
    return {"pmcid": value} if kind in ("europepmc", "bioc") else {}
