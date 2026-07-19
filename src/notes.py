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


def safe_rename(src: Path, dst: Path, retries: int = 5, delay: float = 0.4) -> Path:
    """
    Rename src -> dst, tolerant of transient Windows/iCloud file locks.

    The vault lives on iCloud Drive: its sync daemon (and Windows Defender)
    opens a freshly-written file to upload/scan it, and on Windows a rename of a
    file another process has open fails with PermissionError (WinError 32). That
    is transient — the handle is released within a second or two — so we retry
    with a short backoff. If it still won't budge, we keep the existing name
    rather than crash: the note is fully valid, only its filename is suboptimal,
    and a run-ending exception over a cosmetic rename is the worse outcome.

    Returns the path the note now lives at (dst on success, src on give-up).
    """
    for attempt in range(retries):
        try:
            src.rename(dst)
            return dst
        except PermissionError as e:
            if attempt == retries - 1:
                print(f"[notes] Rename locked, keeping name '{src.name}': {e}")
                return src
            time.sleep(delay * (attempt + 1))
    return src


# Any run of non-alphanumeric characters collapses to a single hyphen, so
# "Machine Learning", "machine_learning" and "machine/learning" all canonicalise
# the same way.
_TAG_SEP_RE = re.compile(r"[^a-z0-9]+")


def normalize_tag(tag: str) -> str:
    """
    Canonicalise a single tag to lowercase-hyphenated form.

    Deterministic hygiene only — casing, a leading '#', and separator style
    (spaces / underscores / slashes -> hyphen). It deliberately does NOT merge
    synonyms ("ml" vs "machine-learning") or split concatenations
    ("machinelearning"); that judgement lives in the tag vocabulary
    (Index/_tags.md) and the one-time consolidate_tags.py pass.

        "  Tax Evasion " -> "tax-evasion"   "#ML" -> "ml"   "wealth_tax" -> "wealth-tax"
    """
    tag = _TAG_SEP_RE.sub("-", str(tag).strip().lower().lstrip("#"))
    return tag.strip("-")


def normalize_tags(tags) -> list[str]:
    """Normalise a list of tags: canonicalise each, drop empties, dedup (order-preserving)."""
    if isinstance(tags, str):
        tags = [tags]
    out: list[str] = []
    for t in tags or []:
        n = normalize_tag(t)
        if n and n not in out:
            out.append(n)
    return out


def today() -> str:
    return time.strftime("%Y-%m-%d")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
