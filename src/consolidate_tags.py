"""
consolidate_tags.py — One-time backfill to unify the vault's tag vocabulary.

Over time the tagging LLM produced near-duplicate tags (machinelearning /
machine-learning / ml, sports-betting / sportsbetting / sportsbook, ...). This
script collapses them to a single canonical form per concept, rewrites every
note's frontmatter, and seeds the canonical tag list (Index/_tags.md) that the
tagging prompts read from going forward.

Two-step so the (LLM-proposed) merge can be reviewed before it touches the vault:

    python src/consolidate_tags.py          # DRY RUN: propose a map -> tag_map.json
    #   ... review / hand-edit tag_map.json ...
    python src/consolidate_tags.py --apply   # APPLY the reviewed tag_map.json

Apply reads the reviewed tag_map.json (it does NOT re-call the model), so what you
approve is exactly what gets written. Delete tag_map.json to start over.
"""

import json
import sys
from collections import Counter
from pathlib import Path

from config import VAULT_PATH, load_prompt
from notes import read_note, write_note, normalize_tag, normalize_tags
from llm import llm_simple, parse_json_response
from indexer import TAGS_VOCAB_PATH, _VOCAB_HEADER

MAP_PATH = Path(__file__).resolve().parent.parent / "tag_map.json"


def collect_tags() -> tuple[Counter, list[tuple[Path, list[str]]]]:
    """Return (raw tag counts, [(note_path, its raw tags)]) across the whole vault."""
    counts: Counter = Counter()
    per_note: list[tuple[Path, list[str]]] = []
    for path in VAULT_PATH.rglob("*.md"):
        try:
            fm, _ = read_note(path)
        except Exception:
            continue
        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tags = [str(t).strip() for t in tags if str(t).strip()]
        if tags:
            per_note.append((path, tags))
            counts.update(tags)
    return counts, per_note


def propose_map(counts: Counter) -> dict[str, str]:
    """Ask the model for a raw->canonical map. Unmapped tags fall back to normalize_tag."""
    tag_list = "\n".join(f"- {tag} ({n})" for tag, n in counts.most_common())
    response = llm_simple(
        task="research",  # one-off; use the strongest model for merge quality
        prompt=load_prompt("tag_consolidation", tag_list=tag_list),
    )
    data = parse_json_response(response)
    if not isinstance(data, dict):
        raise SystemExit("Model did not return a JSON object; aborting. Re-run to retry.")

    result: dict[str, str] = {}
    for raw in counts:
        canon = data.get(raw)
        if canon is None:                 # model omitted it -> keep, just normalised
            result[raw] = normalize_tag(raw)
        else:
            result[raw] = normalize_tag(canon)  # "" (junk) stays "" -> dropped on apply
    return result


def summarise(counts: Counter, mapping: dict[str, str]) -> None:
    """Print a human-readable view: canonical <- [variants], and dropped tags."""
    groups: dict[str, list[str]] = {}
    dropped: list[str] = []
    for raw in counts:
        canon = mapping.get(raw, normalize_tag(raw))
        if not canon:
            dropped.append(raw)
        else:
            groups.setdefault(canon, []).append(raw)

    merged = {c: v for c, v in groups.items() if len(v) > 1 or v[0] != c}
    print(f"\n{len(counts)} raw tags -> {len(groups)} canonical tags "
          f"({len(dropped)} dropped, {len(merged)} canonical tags absorb a rename/merge)\n")

    print("── Merges & renames (canonical <- variants) ──")
    for canon in sorted(merged, key=lambda c: -sum(counts[r] for r in groups[c])):
        variants = ", ".join(f"{r}({counts[r]})" for r in sorted(groups[canon], key=lambda r: -counts[r]))
        print(f"  {canon}  <-  {variants}")

    if dropped:
        print("\n── Dropped as junk ──")
        print("  " + ", ".join(sorted(dropped)))


def write_vocabulary(counts: Counter, mapping: dict[str, str]) -> None:
    """Seed Index/_tags.md with the canonical tags, most-used first."""
    canon_counts: Counter = Counter()
    for raw, n in counts.items():
        canon = mapping.get(raw, normalize_tag(raw))
        if canon:
            canon_counts[canon] += n
    ordered = [t for t, _ in canon_counts.most_common()]
    TAGS_VOCAB_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = _VOCAB_HEADER.rstrip() + "\n" + "\n".join(f"- {t}" for t in ordered) + "\n"
    TAGS_VOCAB_PATH.write_text(body, encoding="utf-8")
    print(f"[vocab] Wrote {len(ordered)} canonical tags -> {TAGS_VOCAB_PATH}")


def apply_map(per_note: list[tuple[Path, list[str]]], mapping: dict[str, str]) -> int:
    """Rewrite each note's frontmatter tags through the map. Returns notes changed."""
    changed = 0
    for path, raw_tags in per_note:
        new_tags = normalize_tags([mapping.get(t, normalize_tag(t)) for t in raw_tags])
        try:
            fm, body = read_note(path)
        except Exception as e:
            print(f"[skip] {path.name}: {e}")
            continue
        old = fm.get("tags")
        old_list = [old] if isinstance(old, str) else (old or [])
        # Compare the RAW stored tags (not their normalised form) to the target:
        # a tag whose only change is normalisation (e.g. "tax evasion" -> "tax-evasion",
        # "Pareto" -> "pareto") differs on disk and must still be rewritten.
        if old_list == new_tags:
            continue  # on-disk value already canonical -> skip to avoid churn
        if new_tags:
            fm["tags"] = new_tags
        else:
            fm.pop("tags", None)
        write_note(path, fm, body)
        changed += 1
    return changed


def main():
    apply = "--apply" in sys.argv[1:]
    counts, per_note = collect_tags()
    print(f"Scanned {VAULT_PATH}")
    print(f"Found {sum(counts.values())} tag uses across {len(per_note)} notes "
          f"({len(counts)} unique tags).")

    if apply:
        if not MAP_PATH.exists():
            raise SystemExit(f"No {MAP_PATH.name} to apply. Run without --apply first to create it.")
        mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        # Any tag missing from the reviewed map keeps its normalised self; any
        # falsy value ("" or null from a hand-edit) means "drop this tag".
        for raw in counts:
            val = mapping.get(raw, normalize_tag(raw))
            mapping[raw] = normalize_tag(val) if val else ""
        summarise(counts, mapping)
        changed = apply_map(per_note, mapping)
        write_vocabulary(counts, mapping)
        print(f"\n[apply] Rewrote tags in {changed} notes.")
        print("Done.")
    else:
        mapping = propose_map(counts)
        summarise(counts, mapping)
        MAP_PATH.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[dry-run] Proposed map written to {MAP_PATH}")
        print("Review / edit it, then run:  python src/consolidate_tags.py --apply")


if __name__ == "__main__":
    main()
