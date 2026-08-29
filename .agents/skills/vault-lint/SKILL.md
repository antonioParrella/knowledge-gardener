---
name: vault-lint
description: Inspect or repair the Knowledge Garden vault with its local lint command. Use when checking vault integrity, diagnosing broken links or metadata, or deciding whether the lint auto-fix is safe. Explains the report modes and exactly what `--fix` changes.
---

# Vault linting

The lint runs without the Gemini API and checks duplicate source URLs, broken
YAML, MOC `note_count` mismatches, orphan wikilinks, duplicate MOC entries,
empty-body notes, stale `_index.md` references, and unrendered LaTeX math.

```bash
python src/lint.py           # full report
python src/lint.py --quiet   # print only when issues exist; useful for schedules
python src/lint.py --fix     # repair only the supported cases
```

`--fix` chooses duplicate-source keepers by `_clip_quality`, not age. It repairs
duplicate or orphan MOC entries, `note_count`, stale index references, and math
delimiters. Review anything else manually; a clean run does not mean the fixer
can resolve every category the report detects.
