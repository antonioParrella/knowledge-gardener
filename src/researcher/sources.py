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


def _load_existing(path: Path) -> dict | None:
    """Load an already-vaulted note's analysis for reuse in synthesis."""
    try:
        _, body = read_note(path)
    except Exception:
        return None
    analysis = body.split("## Original Content")[0].strip()
    return {"title": path.stem, "analysis": analysis}


def _clip_source(url: str, title: str, kind: str, body: str, full_text: bool) -> dict | None:
    """
    Write a queued source's text as a clip stub and run it through the clipper
    pipeline, returning {title, analysis, raw} — or None if the clipper discarded it
    (e.g. its analyzer judged the content not to be the real document, or a dup race).
    """
    clip_path = INBOX_PATH / f"{safe_filename(title)}.md"
    if clip_path.exists():
        # name collision with an unrelated note — disambiguate
        clip_path = INBOX_PATH / f"{safe_filename(title)} ({today()}).md"

    write_note(
        clip_path,
        frontmatter={
            "clipped": True,
            "source": url,
            "date": today(),
            "processed": False,
            "source_type": "research_found",
            "full_text": full_text,
        },
        body=body,
    )

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

    # Retrieve full text
    if kind == "pdf":
        text = extract_paper_text(url, landing_url=url)
    else:
        from web_tools import fetch_url
        text = fetch_url(url)
    full_text_ok = bool(text) and not text.startswith("Failed to fetch")

    # Try the full text first. The clipper's analyzer judges whether the retrieved
    # text is the real document; if it isn't (raw PDF bytes, a bot-wall / CAPTCHA /
    # paywall interstitial), process_clipped_note discards the stub and _clip_source
    # returns None — so we fall through to the abstract. A clean, thin, citable clip
    # beats a garbage one.
    if full_text_ok:
        result = _clip_source(url, title, kind, text, full_text=True)
        if result:
            return result
        print(f"[research] Full text unusable — falling back to abstract: {title}")

    # Abstract-only fallback: full text couldn't be retrieved (e.g. paywalled PDF) or
    # was judged not to be the real document above. Still kept and citable, just thinner.
    if abstract:
        body = (
            "> [!warning] Abstract only — full text could not be retrieved.\n\n"
            f"## Abstract\n{abstract}"
        )
        return _clip_source(url, title, kind, body, full_text=False)

    print(f"[research] No usable source and no abstract available: {url}")
    return None
