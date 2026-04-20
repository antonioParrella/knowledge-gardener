"""
clipper.py — Processes notes saved by the Obsidian Web Clipper.

When the watchdog detects a new note in Inbox/ with clipped: true and
processed: false, this module:
  1. Reads the clipped content
  2. Asks Gemini to summarise, extract takeaways and tags
  3. Rewrites the note with the enriched content
  4. Renames the note to a clean title
  5. Indexes the note into the knowledge base MOCs
"""

import re
from pathlib import Path

from config import CLIP_CONTENT_LIMIT, load_prompt
from notes import read_note, write_note, safe_filename, today
from gemini_client import gemini_simple, parse_json_response
from indexer import index_note


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

    # Guard: only process unhandled clips
    if fm.get("clipped") == "false":
        return
    if fm.get("processed") == "true":
        return

    print(f"[clip] Processing: {path.name}")

    # Truncate to avoid token overload
    content = body[:CLIP_CONTENT_LIMIT]
    source_url = fm.get("source", "unknown")

    # Ask Gemini to analyse the content
    system_prompt = load_prompt("clip_system")
    user_prompt = load_prompt("clip_analysis", source_url=source_url, content=content)
    
    result_text = gemini_simple(
        prompt=user_prompt,
        system=system_prompt,
    )

    data = parse_json_response(result_text)
    if not data:
        print(f"[clip] JSON parse failed for {path.name}, using fallback.")
        data = {
            "title": path.stem,
            "summary": "Could not summarise — see original content below.",
            "takeaways": [],
            "tags": [],
        }

    # Build enriched note body
    takeaways_md = "\n".join(f"- {t}" for t in data.get("takeaways", []))
    new_body = (
        f"## Summary\n{data.get('summary', '')}\n\n"
        f"## Key Takeaways\n{takeaways_md}\n\n"
        f"## Original Content\n{body}\n"
    )

    # Update frontmatter
    fm["processed"] = True
    fm["tags"] = data.get("tags", [])
    fm["title"] = data.get("title", path.stem)
    fm["processed_date"] = today()

    write_note(path, fm, new_body)

    # Rename to clean title
    clean_title = safe_filename(data.get("title", path.stem))
    if clean_title and clean_title != path.stem:
        new_path = path.parent / f"{clean_title}.md"
        if not new_path.exists():
            path.rename(new_path)
            path = new_path
            print(f"[clip] Renamed to: {clean_title}.md")

    # Index into knowledge base
    index_note(
        note_title=path.stem,
        note_path=path,
        summary=data.get("summary", ""),
        tags=data.get("tags", []),
    )

    print(f"[clip] Done: {path.stem}")
