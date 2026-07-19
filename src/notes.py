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


_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _convert_inline_segment(seg: str, escape_currency: bool) -> str:
    r"""
    Convert inline math `\( … \)` -> `$ … $` in one text segment (already known
    to be OUTSIDE code). Deliberately does NOT touch `\[` / `\]`: outside a
    display-shaped line those are ambiguous with markdown's escaped-literal
    bracket (`interval \[a, b\]`), so display conversion is handled per line by
    _convert_line, and mid-prose `\[` is left as the literal it probably is.

    Currency escaping is SCOPED to segments that actually contain a bracket to
    convert. That is the only situation where we introduce a new math '$' that a
    currency '$' could collide with; a segment already in correct dollar form
    (no brackets) is left completely alone, so a legitimately dollar-delimited
    span that starts with a digit — `$1 - \alpha$` (a confidence level),
    `$2^n$` — is never corrupted. Order still matters within a converting
    segment: currency is escaped FIRST, before the brackets become '$', so the
    freshly-written delimiters are never re-escaped.
    """
    if escape_currency and (r"\(" in seg or r"\)" in seg):
        # Currency signature: '$' immediately followed by a digit ($1, $5,000).
        seg = re.sub(r"(?<!\\)\$(?=\d)", r"\\$", seg)
    seg = seg.replace(r"\(", "$").replace(r"\)", "$")
    return seg


def _is_display_line(stripped: str) -> bool:
    r"""
    True when a line IS a LaTeX display block, so its `\[` / `\]` are real math
    delimiters (not escaped literal brackets in prose). Two shapes, both drawn
    from how the synthesis model actually writes display math:
      - a bare delimiter alone on its line (`\[` … content … `\]`), or
      - the whole line wrapped as one block (`\[ E = mc^2 \]`).
    A `\[…\]` sitting mid-sentence with prose around it is neither, so a literal
    escaped interval is left untouched.
    """
    if stripped in (r"\[", r"\]"):
        return True
    return len(stripped) > 3 and stripped.startswith(r"\[") and stripped.endswith(r"\]")


def _convert_line(line: str, escape_currency: bool) -> str:
    """Convert one non-fenced line (may keep a trailing newline)."""
    nl = "\n" if line.endswith("\n") else ""
    body = line[:-1] if nl else line

    if _is_display_line(body.strip()):
        # Pure display math — no inline code or currency to worry about.
        return body.replace(r"\[", "$$").replace(r"\]", "$$") + nl

    # Otherwise inline-only, code-span aware: even-indexed parts are outside
    # inline `code`, so `arr\[i\]` / `$PATH` in code is never rewritten.
    parts = body.split("`")
    for i in range(0, len(parts), 2):
        parts[i] = _convert_inline_segment(parts[i], escape_currency)
    return "`".join(parts) + nl


def normalize_math_delimiters(text: str, escape_currency: bool = False) -> str:
    r"""
    Convert `\( … \)` / `\[ … \]` math to Obsidian's `$ … $` / `$$ … $$`.

    Obsidian's MathJax only renders the dollar delimiters; models trained on
    papers habitually emit the backslash-bracket form despite the prompt, so
    reports render as literal brackets. This is the deterministic backstop.

    Code is left untouched: fenced blocks (``` / ~~~) and inline `code` spans
    are skipped, so `arr\[i\]`-style content in code never becomes math.
    Display `\[` / `\]` are converted only on display-shaped lines (see
    _is_display_line), so an escaped literal interval mid-prose is preserved.

    escape_currency=True rewrites a currency '$' ('$'+digit) to '\$', but ONLY
    on a line where a bracket is being converted — the only place we introduce a
    new math '$' for currency to collide with. It neutralises the collision on
    lines like "bet $1 … lose \(\alpha\)" while leaving a line that is already
    correct dollar-math untouched, so an existing `$1 - \alpha$` or `$2^n$` is
    never corrupted. Pass False to skip currency handling entirely.
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        out.append(_convert_line(line, escape_currency))
    return "".join(out)


def today() -> str:
    return time.strftime("%Y-%m-%d")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
