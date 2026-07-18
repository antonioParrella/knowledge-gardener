"""
clipper.py — Processes notes saved by the Obsidian Web Clipper.

When the watchdog detects a new note in Inbox/ with clipped: true and
processed: false, this module:
  1. Reads the clipped content
  2. Asks Gemini to summarise, extract takeaways and tags
  3. Saves the summary to a separate note alongside the original
  4. Indexes into the knowledge base MOCs
"""

import re
from pathlib import Path

from config import CLIP_CONTENT_LIMIT, VAULT_PATH, INBOX_PATH, SOURCES_PATH, load_prompt
from notes import read_note, write_note, safe_filename, today, normalize_tags
from llm import llm_simple, parse_json_response
from indexer import index_note, format_tag_vocabulary


def find_existing_source(source_url: str, exclude: Path | None = None) -> Path | None:
    """
    Check if any existing note in Clippings/ or Sources/ already has this source URL.
    Returns the existing note's path if found, None otherwise.

    `exclude` skips one path — pass the note being processed so a same-source check
    can't match the file against itself. Without it, find returns the first glob
    match, which may be the current note, and a caller guarding on `existing != path`
    then silently lets a real duplicate through (how the overnight duplicates slipped
    past the inline dedup: the stub sorted before its twin in glob order).
    """
    if not source_url or source_url == "unknown":
        return None
    for folder in (INBOX_PATH, SOURCES_PATH):
        if not folder.exists():
            continue
        for md_file in folder.glob("*.md"):
            if exclude is not None and md_file == exclude:
                continue
            try:
                fm, _ = read_note(md_file)
                if fm.get("source", "").strip() == source_url:
                    return md_file
            except Exception:
                continue
    return None


def find_unprocessed_clips(inbox_path: Path):
    """Find all unprocessed clips in the Inbox."""
    unprocessed = []
    if not inbox_path.exists():
        return unprocessed

    for md_file in inbox_path.glob("*.md"):
        try:
            fm, _ = read_note(md_file)
            if fm.get("processed") is not True:
                unprocessed.append(md_file)
        except Exception:
            continue

    return unprocessed


def reset_clips(inbox_path: Path, dry_run: bool = False):
    """
    Delete summary notes so clips can be re-summarised.
    Leaves the original clip untouched.
    Pass dry_run=True to preview without making changes.
    """
    count = 0
    if not inbox_path.exists():
        return count

    for md_file in inbox_path.glob("*.md"):
        try:
            fm, _ = read_note(md_file)
            if fm.get("processed") == True:
                if dry_run:
                    print(f"[clip] Would delete summary: {md_file.name}")
                else:
                    summary_path = md_file.parent / f"{md_file.stem} - Summary.md"
                    if summary_path.exists():
                        summary_path.unlink()
                    fm["processed"] = False
                    fm.pop("processed_date", None)
                    write_note(md_file, fm, None)
                    print(f"[clip] Reset: {md_file.name}")
                count += 1
        except Exception:
            continue

    return count


def process_clipped_note(path: Path, content_limit: int = CLIP_CONTENT_LIMIT):
    """
    Full processing pipeline for a Web Clipper note.
    Skips silently if not a valid unprocessed clip.

    content_limit caps how much of the body is sent to the analysis model;
    full-text papers pass a higher limit so the analysis sees more than the
    first couple of pages. The full body is always preserved as Original Content.

    Returns the final note path (which may have been renamed) on success, else None.
    """
    try:
        fm, body = read_note(path)
    except Exception as e:
        print(f"[clip] Could not read {path.name}: {e}")
        return None

    if fm.get("processed") is True:
        return path

    source_url = fm.get("source", "unknown")
    existing = find_existing_source(source_url, exclude=path)
    if existing:
        print(f"[clip] DELETE: duplicate source URL — already in {existing.stem}")
        path.unlink()
        return None

    print(f"[clip] Processing: {path.name}")

    content = body[:content_limit]

    system_prompt = load_prompt("clip_system")
    user_prompt = load_prompt("clip_analysis", source_url=source_url, content=content,
                              vocabulary=format_tag_vocabulary())

    result_text = llm_simple(
        prompt=user_prompt,
        system=system_prompt,
        task="clip",
    )

    data = parse_json_response(result_text)
    if not data:
        print(f"[clip] JSON parse failed for {path.name}, leaving original untouched.")
        return None

    # The analyzer flags content that isn't the real document — raw PDF/binary bytes,
    # a bot-wall / CAPTCHA / paywall interstitial, an error page — as usable: false.
    # Don't turn those into clips: discard the stub (like the duplicate path above) so
    # the research pipeline falls back to an abstract-only clip and the vault isn't
    # polluted with garbage notes. Absent/true (the normal case) processes as usual.
    if data.get("usable") is False:
        reason = (data.get("title") or "not real content").strip()
        print(f"[clip] Discarding — content not usable ({reason}): {path.name}")
        path.unlink(missing_ok=True)
        return None

    summary_title = data.get("title", path.stem)
    clean_title = safe_filename(summary_title)

    tags = normalize_tags(data.get("tags", []))
    analysis = data.get("content") or data.get("summary") or ""
    moc_summary = data.get("moc_summary") or ""

    # On re-index (clip was reset with preserve_title), keep the existing filename
    # so wikilinks stay stable. Absent on new clips => False, so they rename normally.
    # Popped so the flag is consumed and never persists past one reprocess.
    preserve_title = fm.pop("preserve_title", False)

    if clean_title != path.stem and not preserve_title:
        new_path = path.parent / f"{clean_title}.md"
        if not new_path.exists():
            path.rename(new_path)
            path = new_path

    summary_body = f"{analysis}\n\n---\n\n## Original Content\n\n{body}"

    index_note(
        note_title=path.stem,
        note_path=path,
        summary=moc_summary,
        tags=tags,
        analysis=analysis,
    )

    fm["processed"] = True
    fm["processed_date"] = today()
    if tags:
        fm["tags"] = tags

    write_note(path, fm, summary_body)

    print(f"[clip] Done: {path.stem}")
    return path


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from config import INBOX_PATH

    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args or "-n" in args

    count = reset_clips(INBOX_PATH, dry_run=dry_run)
    action = "Would reset" if dry_run else "Reset"
    print(f"{action} {count} clip(s)")
