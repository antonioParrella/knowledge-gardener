---
name: run-backfill
description: Run a one-off Knowledge Garden backfill with a scoped dry run and explicit apply step. Use when reprocessing clips or reports, cleaning historical vault data, consolidating tags, or retrying full-text retrieval. Identifies the local migration scripts and their costly or lower-precision options.
---

# Running backfills

These are one-time migrations, not the live pipeline. Start with the script's
preview mode and narrow the scope before applying a change. Read the relevant
`DESIGN_NOTES` section for the historical problem each script repairs.

| Script | Purpose and important options |
| --- | --- |
| `reset_clips.py` | Revert processed clips to their original state for re-indexing. Use `--dry-run` first. |
| `clean_junk_clips.py` | Remove raw-PDF byte dumps and bot-wall interstitials saved before the `usable` gate; also repairs MOCs. |
| `consolidate_tags.py` | Unify tags into canonical vocabulary. Review the proposed `tag_map.json` before applying it. |
| `fix_math_delimiters.py` | Convert `\\(…\\)` and `\\[…\\]` into Obsidian `$…$` and `$$…$$`. |
| `reconceptualize.py` | Re-extract concepts from reports truncated to 15k characters by the former extractor. Use `--only <substr>` to scope; `--apply` queues paid generation. |
| `backfill_fulltext.py` | Retry full text for clips that only had abstracts, then re-analyse recovered papers. Use `--limit N` or `--only <substr>` first. `--resolve-titles` has lower precision but remains gated; `--apply` commits the work. |

Some older scripts use `--dry-run` while others require `--apply`; inspect each
command's help before assuming its flag polarity.
