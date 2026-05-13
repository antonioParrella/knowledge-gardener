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
from notes import read_note, write_note, safe_filename, today
from gemini_client import gemini_simple, parse_json_response
from indexer import index_note


def _find_existing_source(source_url: str) -> Path | None:
    """
    Check if any existing note in Clippings/ or Sources/ already has this source URL.
    Returns the existing note's path if found, None otherwise.
    """
    if not source_url or source_url == "unknown":
        return None
    for folder in (INBOX_PATH, SOURCES_PATH):
        if not folder.exists():
            continue
        for md_file in folder.glob("*.md"):
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


def process_clipped_note(path: Path):
    """
    Full processing pipeline for a Web Clipper note.
    Skips silently if not a valid unprocessed clip.
    """
    try:
        fm, body = read_note(path)
    except Exception as e:
        print(f"[clip] Could not read {path.name}: {e}")
        return

    if fm.get("processed") is True:
        return

    source_url = fm.get("source", "unknown")
    existing = _find_existing_source(source_url)
    if existing and existing != path:
        print(f"[clip] DELETE: duplicate source URL — already in {existing.stem}")
        path.unlink()
        return

    print(f"[clip] Processing: {path.name}")

    content = body[:CLIP_CONTENT_LIMIT]

    system_prompt = load_prompt("clip_system")
    user_prompt = load_prompt("clip_analysis", source_url=source_url, content=content)

    result_text = gemini_simple(
        prompt=user_prompt,
        system=system_prompt,
    )

    data = parse_json_response(result_text)
    if not data:
        print(f"[clip] JSON parse failed for {path.name}, leaving original untouched.")
        return

    summary_title = data.get("title", path.stem)
    clean_title = safe_filename(summary_title)

    tags = data.get("tags", [])
    analysis = data.get("content") or data.get("summary") or ""
    moc_summary = data.get("moc_summary") or ""

    if clean_title != path.stem:
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
    )

    fm["processed"] = True
    fm["processed_date"] = today()
    if tags:
        fm["tags"] = tags

    write_note(path, fm, summary_body)

    print(f"[clip] Done: {path.stem}")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from config import INBOX_PATH

    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args or "-n" in args

    count = reset_clips(INBOX_PATH, dry_run=dry_run)
    action = "Would reset" if dry_run else "Reset"
    print(f"{action} {count} clip(s)")
