"""
notes.py — Helpers for reading and writing Obsidian markdown notes.
All notes use YAML frontmatter followed by a markdown body.
"""

import re
import time
import yaml
from pathlib import Path


def read_note(path: Path) -> tuple[dict, str]:
    """
    Parse a markdown note into (frontmatter dict, body string).
    Returns ({}, raw_text) if no frontmatter found.
    """
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        try:
            _, fm_raw, body = text.split("---", 2)
            fm = yaml.safe_load(fm_raw) or {}
            return fm, body.strip()
        except Exception:
            pass
    return {}, text.strip()


def write_note(path: Path, frontmatter: dict, body: str):
    """Write a note with YAML frontmatter + markdown body."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_str = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{fm_str}\n---\n\n{body}\n", encoding="utf-8")


def safe_filename(title: str) -> str:
    """Strip characters that are invalid in Windows filenames."""
    return re.sub(r'[\\/*?:"<>|]', "", title).strip()


def today() -> str:
    return time.strftime("%Y-%m-%d")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
