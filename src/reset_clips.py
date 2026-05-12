"""
reset_clips.py — Standalone script to reset processed clips back to their
original content and clean up MOC/index references.

Usage:
    python src/reset_clips.py                # reset everything
    python src/reset_clips.py --dry-run      # preview without changes
"""

import re
import sys
from pathlib import Path
from config import INBOX_PATH, INDEX_PATH
from notes import read_note, write_note

HEADER = "## Original Content"


def reset_clips(dry_run: bool = False) -> set[str]:
    """Reset all processed clips to original content. Returns set of reset stems."""
    reset_stems: set[str] = set()

    if not INBOX_PATH.exists():
        print("Clippings/ folder not found.")
        return reset_stems

    for md_file in sorted(INBOX_PATH.glob("*.md")):
        try:
            fm, body = read_note(md_file)
        except Exception as e:
            print(f"[skip] Could not read {md_file.name}: {e}")
            continue

        if fm.get("processed") is not True:
            continue

        parts = body.split(f"\n{HEADER}\n", 1)
        if len(parts) < 2:
            print(f"[warn] No '{HEADER}' found in {md_file.name}, skipping")
            continue

        original = parts[1].strip()

        if dry_run:
            print(f"[dry-run] Would reset: {md_file.name}")
            reset_stems.add(md_file.stem)
            continue

        fm["processed"] = False
        fm.pop("processed_date", None)
        fm.pop("tags", None)
        write_note(md_file, fm, original)
        print(f"[reset] {md_file.name}")
        reset_stems.add(md_file.stem)

    return reset_stems


def clean_mocs(clip_stems: set[str], dry_run: bool = False):
    """Remove clip entries from MOCs. Delete MOCs that end up empty."""
    if not INDEX_PATH.exists():
        print("Index/ folder not found.")
        return set()

    entry_re = re.compile(r"^- \[\[([^\]]+)\]\]")
    deleted_topics: set[str] = set()

    for moc_path in sorted(INDEX_PATH.glob("MOC - *.md")):
        try:
            fm, body = read_note(moc_path)
        except Exception as e:
            print(f"[skip] Could not read {moc_path.name}: {e}")
            continue

        lines = body.splitlines(keepends=True)
        kept: list[str] = []
        removed = 0

        for line in lines:
            m = entry_re.match(line)
            if m and m.group(1) in clip_stems:
                removed += 1
                continue
            kept.append(line)

        new_note_count = fm.get("note_count", 0) - removed

        if new_note_count <= 0:
            if dry_run:
                print(f"[dry-run] Would delete empty MOC: {moc_path.name}")
            else:
                moc_path.unlink()
                print(f"[moc] Deleted empty MOC: {moc_path.name}")
            topic = fm.get("topic", "")
            if topic:
                deleted_topics.add(topic)
        elif removed > 0:
            new_body = "".join(kept)
            fm["note_count"] = new_note_count
            if dry_run:
                print(f"[dry-run] Would update MOC {moc_path.name}: -{removed} entries, {new_note_count} remaining")
            else:
                write_note(moc_path, fm, new_body)
                print(f"[moc] {moc_path.name}: -{removed} entries, {new_note_count} remaining")

    return deleted_topics


def clean_master_index(deleted_topics: set[str], dry_run: bool = False):
    """Remove links to deleted MOCs from _index.md."""
    if not deleted_topics:
        return

    master_path = INDEX_PATH / "_index.md"
    if not master_path.exists():
        return

    text = master_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    kept: list[str] = []

    for line in lines:
        skip = False
        for topic in deleted_topics:
            if f"[[MOC - {topic}]]" in line:
                skip = True
                break
        if skip:
            if dry_run:
                print(f"[dry-run] Would remove from _index.md: {line.strip()}")
            continue
        kept.append(line)

    if len(kept) < len(lines):
        new_text = "".join(kept)
        if not dry_run:
            master_path.write_text(new_text, encoding="utf-8")
            print(f"[index] Updated _index.md")


def main():
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args or "-n" in args

    print("=" * 50)
    print("Reset Clips" + (" (dry-run)" if dry_run else ""))
    print(f"Inbox:  {INBOX_PATH}")
    print(f"Index:  {INDEX_PATH}")
    print("=" * 50)

    stems = reset_clips(dry_run=dry_run)
    print(f"\nClips reset: {len(stems)}")

    deleted = clean_mocs(stems, dry_run=dry_run)
    clean_master_index(deleted, dry_run=dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
