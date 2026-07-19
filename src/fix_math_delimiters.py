"""
fix_math_delimiters.py — One-off backfill: convert reports that use standard
LaTeX math delimiters (`\\( … \\)`, `\\[ … \\]`) to Obsidian's dollar form.

The synthesis model habitually emits backslash-bracket delimiters despite the
prompt asking for `$…$` / `$$…$$`; Obsidian's MathJax only renders the dollar
form, so those reports show literal brackets. This pass fixes the notes written
before the synthesis-time guard existed. It is the math-delimiter analogue of
consolidate_tags.py / clean_junk_clips.py: reviewable dry-run first, then --apply.

    python src/fix_math_delimiters.py            # DRY RUN: show what would change
    python src/fix_math_delimiters.py --apply    # rewrite the affected notes

Currency safety: a currency '$' on the same line as converted inline math would
make MathJax pair the two, swallowing the prose between them. So the pass runs
normalize_math_delimiters(escape_currency=True), which escapes a currency '$' (a
'$' followed by a digit, e.g. "$1", "$5,000") to '\\$'. That signature leaves any
real `$…$` math untouched, so it is safe even if a note already mixes the two.
"""

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import VAULT_PATH
from notes import normalize_math_delimiters

# Folders whose notes are model-written prose that can carry math.
SCAN_DIRS = ["Research", "Concepts"]


def _has_bracket_math(text: str) -> bool:
    return bool(re.search(r"\\[\[\]()]", text))


def main(apply: bool):
    changed, scanned = [], 0
    for sub in SCAN_DIRS:
        d = VAULT_PATH / sub
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.md")):
            scanned += 1
            text = path.read_text(encoding="utf-8")
            if not _has_bracket_math(text):
                continue
            new = normalize_math_delimiters(text, escape_currency=True)
            if new == text:
                continue
            changed.append((path, text, new))

    print(f"Scanned {scanned} notes in {', '.join(SCAN_DIRS)}.\n")

    if not changed:
        print("No notes need conversion.")
        return

    print(f"{'WOULD CONVERT' if not apply else 'CONVERTING'} {len(changed)} note(s):")
    for path, old, new in changed:
        n_disp = old.count(r"\[")
        n_inl = old.count(r"\(")
        n_cur = len(re.findall(r"(?<!\\)\$", old))
        print(f"\n  {path.name}")
        print(f"    display \\[..\\]: {n_disp}   inline \\(..\\): {n_inl}   "
              f"currency $ escaped: {n_cur}")
        if not apply:
            _preview(old, new)
        else:
            path.write_text(new, encoding="utf-8")

    if not apply:
        print("\nDRY RUN — no files changed. Re-run with --apply to write.")
    else:
        print(f"\nApplied to {len(changed)} note(s).")


def _preview(old: str, new: str, context: int = 0):
    """Show the first few changed lines side by side for review."""
    o_lines = old.splitlines()
    n_lines = new.splitlines()
    shown = 0
    for i, (o, n) in enumerate(zip(o_lines, n_lines), 1):
        if o != n:
            print(f"    L{i}-  {o[:140]}")
            print(f"    L{i}+  {n[:140]}")
            shown += 1
            if shown >= 4:
                print("    … (further changes omitted)")
                break


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
