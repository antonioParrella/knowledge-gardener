"""
researcher — the agentic research/callout/concept pipeline.

Triggered when the watchdog detects a note in _triggers/ with research: true (or
concept: true), or when a "> [!research] question" callout is found in any note.

Four phases (shared core in pipeline.py):
  ① Find relevant existing clippings (Gemini-judged over the MOC catalog)
  ② Discovery — agentic tool loop (arXiv / OpenAlex / web search) that queues sources
  ③ Process each queued source through the clipper pipeline → indexed clipping
  ④ Synthesise a long report (draft, or draft→critique→revise for comprehensive)

Module map:
  sources.py    ③ fetch a queued source → indexed clipping (shared)
  synthesis.py  ④ write the report + citation repair + completeness gate (shared)
  pipeline.py   the shared _run_research core + MOC index-entry helper
  triggers.py   research trigger notes (_triggers/, research: true) + naming
  callouts.py   inline `> [!research]` callouts answered in place
  concepts.py   concept extraction + explainer generation (the learning layer)

This package's public API — what the watchdog imports — is re-exported below, so
`from researcher import process_research_trigger` and `researcher.<name>` keep
working exactly as when this was a single module.
"""

# Research trigger notes
from .triggers import (
    process_research_trigger,
    find_pending_triggers,
    _title_from_report,
    _research_note_name,
    _depth_from_body,
)

# Inline research callouts
from .callouts import (
    find_research_callouts,
    process_research_callout,
    revert_stale_callouts,
)

# Concept notes
from .concepts import (
    process_concept_trigger,
    find_pending_concept_triggers,
    _match_key,
    _link_first_mention,
    _link_concepts_inline,
)

# Citation / completeness internals (referenced directly by the test suite)
from .synthesis import (
    IncompleteReportError,
    _assert_report_complete,
    _repair_wikilinks,
    _find_bad_wikilinks,
    _normalize_fused_wikilinks,
)

__all__ = [
    "process_research_trigger",
    "find_pending_triggers",
    "find_research_callouts",
    "process_research_callout",
    "revert_stale_callouts",
    "process_concept_trigger",
    "find_pending_concept_triggers",
]
