# Obsidian Auto-Research System — Complete Build Plan

---

## What This Is

A personal knowledge pipeline that runs silently on your Surface Laptop,
triggered from your iPhone. It has two modes that feed the same growing
knowledge base:

**Clip mode** — Save any web page with the Obsidian Web Clipper browser
extension. The agent automatically summarises it, extracts key takeaways and
tags, and indexes it into your vault.

**Research mode** — Create a trigger note on your iPhone (or add a
`> [!research]` callout to any note). The agent discovers academic papers
(arXiv + OpenAlex) and authoritative web sources, retrieves their **full text**,
runs each through the clip pipeline so it becomes an indexed clipping, then
synthesises a long, detailed report that cites every source with `[[wikilinks]]`.

Both pipelines maintain a set of **MOC notes** (Maps of Content) — topic-based
index notes the agent builds and updates automatically. Over time the agent
builds on prior knowledge rather than starting from scratch each time.

**Concept notes (automatic)** — Whenever a research report is compiled, the agent
also extracts the foundational ideas the report leans on and writes standalone,
university-textbook-level explainers for them (`Concept - <Term>`), linked inline
from the report. This is the *learning* layer: research reports resolve questions
and engage the expert debate; concept notes teach the underlying ideas — once —
and are reused by everything that references them (see *Concept Pipeline* below).

Total cost: **$0** (Tavily and Gemini both have free tiers; arXiv + OpenAlex need no key).

---

## Stack

| Component       | Choice                          | Why                                      |
|-----------------|---------------------------------|------------------------------------------|
| Sync            | Obsidian Sync (vault on local disk) | Understands Obsidian's write patterns; iCloud shredded `.obsidian/` (see *Sync*) |
| AI              | Gemini Flash (free) + OpenRouter | Per-task routing; research on DeepSeek V4 Pro (max reasoning) via OpenRouter |
| Trigger         | Python watchdog on Surface      | Monitors vault for new notes             |
| Academic search | arXiv + OpenAlex (no key)       | Real papers with full-text PDFs          |
| Full text       | PyMuPDF / pypdf                 | Extracts paper PDFs to text              |
| Web search      | Tavily (free API key)           | Ranked web results built for LLM agents  |
| Index           | Markdown MOCs in vault          | Obsidian-native, no extra tools          |
| Web Clipper     | Obsidian Web Clipper extension  | Saves pages directly to vault            |

---

## Vault Structure

```
MyVault/
│
├── Clippings/                      ← Web Clipper saves here
│   ├── Clipped - Article Title.md  ← Before processing
│   └── Clean Article Title.md      ← After (renamed by agent)
│
├── Research/                       ← Agent-generated research notes
│   └── Research - Topic Name.md
│
├── Concepts/                       ← Agent-written concept explainers (learning layer)
│   └── Concept - Reward Prediction Error.md
│
├── Sources/                        ← Sources the agent saved during research
│   └── Source Title.md
│
├── Index/                          ← Agent-maintained knowledge base
│   ├── _index.md                   ← Master list of all MOCs
│   ├── _tags.md                    ← Canonical tag vocabulary (fed to tagging prompts)
│   ├── MOC - LLM Training.md       ← specific sub-fields, not broad domains
│   ├── MOC - Generative Models.md
│   └── MOC - Sports Nutrition.md
│
└── _triggers/                      ← Research trigger notes (from iPhone)
    └── research - quantum computing.md
```

---

## Project File Structure

```
obsidian_system/
├── prompts/
│   ├── clip_system.md         ← System prompt for clip processing
│   ├── clip_analysis.md       ← User prompt template for clip analysis
│   ├── research_system.md     ← System prompt for the discovery tool loop
│   ├── research_synthesis.md  ← System prompt for report synthesis (draft/revise)
│   ├── research_critique.md   ← System prompt for the comprehensive critique pass
│   ├── research_callout.md    ← System prompt for inline [!research] callout answers
│   ├── research_repair_links.md ← Resolves citations that aren't real note titles
│   ├── research_tags.md       ← One-line MOC summary + topical tags for a finished report
│   ├── concept_extract.md     ← Picks foundational concepts from a finished report (+ verbatim mention to link)
│   ├── concept_system.md      ← System prompt for the concept discovery loop (restraint-first)
│   ├── concept_synthesis.md   ← System prompt for writing the textbook-level concept note
│   └── tag_consolidation.md   ← One-shot: map drifted tags → canonical (consolidate_tags.py)
└── src/
    ├── config.py            ← All settings (edit VAULT_PATH here)
    ├── notes.py             ← Read/write markdown note helpers
    ├── llm.py               ← Task-routed LLM facade (llm_simple / llm_tool_loop)
    ├── providers/           ← Per-provider impls behind a common interface
    │   ├── base.py          ← Provider ABC, control-flow exceptions, parse_json_response
    │   ├── gemini.py        ← Gemini provider (google-genai); fallback + retry; tool loop
    │   └── openrouter.py    ← OpenRouter provider (openai SDK); reasoning + tool loop
    ├── gemini_client.py     ← Backward-compat shim → llm.py
    ├── indexer.py           ← MOC creation/maintenance; find_relevant_clippings + find_relevant_research
    ├── academic.py          ← arXiv + OpenAlex search; PDF download + full-text extract
    ├── web_tools.py         ← search_arxiv/openalex/web, fetch_url, queue_source tools
    ├── clipper.py           ← Web Clipper note processing pipeline
    ├── researcher.py        ← Research pipeline (discovery → process → synthesis) + callouts + concept extraction/generation
    ├── pdf_processor.py     ← PDF text extraction + summarisation
    ├── obsidian_watchdog.py ← Main entry point, file system monitor
    ├── reset_clips.py       ← Standalone script to revert clips to original
    ├── clean_junk_clips.py  ← One-off: purge junk clips (garbled PDFs, bot-walls) + fix MOCs
    ├── consolidate_tags.py  ← One-time backfill: unify drifted tags → canonical vocabulary
    ├── fix_math_delimiters.py ← One-off backfill: LaTeX \(…\)/\[…\] → Obsidian $…$/$$…$$
    └── lint.py              ← Vault health checker (run periodically)
```

---

## How Each Pipeline Works

### Clip Pipeline

```
1. You clip a page in your browser (Chrome/Firefox/Safari)
2. Web Clipper saves it to Clippings/ with:
      clipped: true
      processed: false
      source: "https://..."
3. Obsidian Sync brings it to the Surface
4. obsidian_watchdog.py detects new file in Clippings/
5. clipper.py checks for duplicate source URLs (skips if already in vault)
6. clipper.py reads the content
7. Gemini returns: title, summary, takeaways, tags (as JSON)
8. Note is rewritten with Summary + Key Takeaways sections
9. Note is renamed to the clean title
10. indexer.py assigns it to a MOC (or creates one)
11. MOC - Topic.md is updated with a link and one-line description
12. _index.md is updated if a new MOC was created
```

### Research Pipeline

```
1. You create a trigger note on iPhone in _triggers/ (its TITLE is the topic):
      research: true
      depth: comprehensive   # standard | deep | comprehensive
      urls:                  # optional seed URLs
        - https://...
2. Obsidian Sync brings it to the Surface; obsidian_watchdog.py picks it up via the create
   event, the 60s periodic rescan, or the startup backlog drain — a trigger
   stays `research: true` until a run completes, so missed events and
   crashed runs are retried automatically. All pipeline work is serialized
   through one lock (`PIPELINE_LOCK`) so the rescan thread can never race a
   research run that's writing source clips into Clippings/.
3. researcher.process_research_trigger() runs four phases:

   ① Two prior-knowledge lanes, kept separate:
      - find_relevant_clippings(topic) — Gemini reads the MOC catalog
        (title + one-line summary per note, minus Research/ reports) and picks
        the relevant clippings. These become cited primary **sources**.
      - find_relevant_research(topic) — Gemini reads the titles + one-line
        summaries of prior reports in Research/ and picks the related ones.
        These are treated as **related work**, not sources: their text grounds
        discovery and synthesis, and the new report cross-links to them (under a
        "## Related research" heading) instead of re-deriving or re-citing them.
        The report being (re)written is excluded so it never references itself.
   ② Discovery tool loop (gemini_tool_loop) — the agent calls
      search_arxiv / search_openalex / search_web / fetch_url, then
      queue_source(url, title, kind, reason, abstract) for each keeper.
      No source cap; already-vaulted URLs are skipped.
   ③ _process_source() for each queued source:
      - pdf  → academic.extract_paper_text() downloads the PDF and extracts
               full text (falls back to a landing-page scrape, but never by
               re-fetching the PDF URL itself — that returns raw bytes)
      - web  → web_tools.fetch_url() (refuses non-HTML/binary responses, so a
               PDF served at a URL is rejected instead of dumped in as "text")
      The text is written to Clippings/ (source_type: research_found) and run
      through clipper.process_clipped_note(), which returns a `usable` verdict.
      If the analyzer judges the content to be non-content — raw PDF/binary
      bytes, a bot-wall / CAPTCHA / paywall interstitial, an error page — the
      stub is discarded and _process_source falls back to an abstract-only clip
      (full_text: false), or skips the source if there's no abstract. A blocked
      source thus becomes a clean, thin, citable clip rather than a garbage note
      (see *Rejecting non-content* below). A real source is indexed into a MOC.
   ④ _synthesise() — builds a source index of exact [[wikilink]] titles
      and writes the report. The index is deliberately **unnumbered**: an
      ordinal beside each title gives the model a number to cite instead of
      the title, and it takes it (see *Citation integrity* below). Any related
      prior research reports from ① are passed as a distinct block (not merged
      into the sources), so the report builds on and cross-links to them under
      "## Related research" without citing them as primary evidence or listing
      them under "## Sources". standard/deep = single draft; comprehensive =
      draft → critique (research_critique) → revise — all on OpenRouter only
      (task `synthesis`, never Gemini). Citations are then checked and repaired by
      _repair_wikilinks() (valid set includes prior-research titles), and a report
      that comes back cut off mid-generation is rejected by _assert_report_complete()
      before anything is written, so the trigger stays pending and retries instead
      of committing a half-report.

4. Report saved to Research/, indexed into a MOC, trigger marked research: done.
   The filename comes from _research_note_name(): an explicit `output:` in the
   trigger wins, else the report's own H1 (trimmed of subtitle), else the topic
   (the note's title).
   The trigger's brief body is kept under a completion banner (not overwritten), so
   a trigger can be cleanly re-run later by flipping research: done → true.
```

### Citation integrity

Reports cite sources as `[[wikilinks]]` that must match a note title **exactly**;
anything else is a dead link in Obsidian. Synthesis can drift out of that
convention and into the numbered-reference style of the papers it is
summarising — one report came back citing `[[21]]`, `[[27]]` throughout, every
link dead. Notably it was *not* the report with the most sources (a 39-source
report cited cleanly); the trigger appears to be the genre of the corpus, which
makes it a matter of luck rather than load. Three mechanisms defend against it:

1. **No ordinals in the prompt.** `_build_source_block` / `_build_prior_research_block`
   list sources as `- [[Title]]`, never `1. [[Title]]`. If the model is never shown
   a number, it has none to substitute for the title.
2. **An explicit ban.** `research_synthesis` and `research_callout` both state that
   citing by number is always wrong, however strongly the sources' own style
   suggests it.
3. **A repair pass, not a warning.** `_repair_wikilinks()` collects every link that
   doesn't match a real title and, if any exist, hands the report back to the model
   with the valid titles (`research_repair_links`) to resolve them from context;
   unresolvable ones are dropped rather than left dead. The repair is only accepted
   if it actually reduces the invalid-link count and doesn't shrink the report — a
   mangled repair must never clobber good prose. This replaced `_validate_wikilinks()`,
   which only printed a warning and wrote the broken report to the vault anyway.
4. **Malformed-link normalisation.** Synthesis also fuses adjacent citations into
   one pair — `[[A], [B]]` instead of `[[A]], [[B]]` — which the old detector's
   regex couldn't even see (it required a bracket-free interior).
   `_normalize_fused_wikilinks()` splits them deterministically before the repair
   pass, and `_find_bad_wikilinks()` now flags any link with a stray interior
   bracket, so nothing malformed slips through.

Two backstops keep a truncated report out of the vault. Both providers now raise on
a cut-off generation — OpenRouter on `finish_reason == "length"`, Gemini on
`MAX_TOKENS` (`_extract_text`) — so it errors / fails over instead of returning half
a report. Because a fail-over target could truncate too, `_assert_report_complete()`
is the final gate: a report still ending mid-sentence is rejected before it is
written or indexed, and the trigger stays pending to retry. Synthesis is also routed
OpenRouter-only (`synthesis` task), so reports are never written by Gemini.

### Math rendering

Obsidian's MathJax only renders `$…$` / `$$…$$`; the synthesis model, trained on
papers, habitually emits standard LaTeX `\( … \)` / `\[ … \]` despite the prompt
asking for the dollar form — so math-heavy reports render as literal brackets. This
is the same shape of problem as *Citation integrity*: a prompt instruction alone
doesn't hold against a strong stylistic prior, so it needs a deterministic backstop.

`notes.normalize_math_delimiters()` is that pass, run in `_run_research` and
`_run_concept` right after `_repair_wikilinks` (link-only) and before the
completeness gate, so every research / callout / concept report is normalised
before it is written. It converts `\(…\)` → `$…$` and — only on a **display-shaped
line** (the delimiter alone on its line, or the whole line wrapped, `_is_display_line`)
— `\[…\]` → `$$…$$`. Display conversion is line-anchored because `\[` is ambiguous:
in LaTeX it opens display math, in markdown it's an *escaped literal bracket*
(`interval \[a, b\]`), and only a display-shaped line is unambiguously the former.
Fenced (``` / ~~~) and inline `` `code` `` spans are skipped, so `arr\[i\]` in code
is never touched.

The subtle part is **currency**. A `$5` next to freshly-written math `$…$` makes
MathJax pair the two and swallow the prose between them (the real trigger: a
Conformal report with "bet $1 … lose \(\alpha\)" on one line). So the pass also
escapes a currency `$` (signature: `$` immediately followed by a digit) to `\$` —
but **only on a line where it is also converting a bracket**, i.e. the one place it
introduces a new `$` to collide with. A line already in correct dollar form (no
brackets) is left completely untouched, so an existing `$1 - \alpha$` (a confidence
level) or `$2^n$` is never corrupted — escaping where there's nothing to collide with
could only do harm. Escaping runs *before* the bracket→dollar conversion on that
line, so the delimiters it just wrote are never re-escaped.

The prompt side is hardened too (belt to the deterministic suspenders):
`research_synthesis`, `research_callout` and `concept_synthesis` explicitly ban
`\(…\)` / `\[…\]` and require escaped `\$` amounts, and the Research Trigger template
carries a standing formatting acceptance-criterion. `lint.py`'s `unrendered_math`
check (and `--fix`) flags any Research/Concepts note the normaliser would still
change — the detector *is* the fixer, so lint and the fix can't disagree, and a
correct dollar-only note (even with currency) is never a false positive.
`fix_math_delimiters.py` is the reviewable one-off backfill (dry-run, then `--apply`)
for reports written before the guard existed.

The one residual: a single line that mixes *both* `$…$` and `\(…\)` styles where the
dollar-span starts with a digit — its opening `$` gets escaped. It needs the model to
use two delimiter conventions on one line, which the prompt ban pushes against; the
`lint` check surfaces whatever slips through.

### Inline Research Callouts

Add `> [!research] your question` anywhere in **any** note in the vault. On the
next periodic rescan, `find_research_callouts()` — which scans the whole vault
recursively (`VAULT_PATH`), skipping `_triggers/`, `.obsidian/`, `.trash/` and
iCloud/Obsidian conflict copies (`*(conflicted)*`, `*sync-conflict*`) — picks it
up and `process_research_callout()`:

1. Captures the **full text of the host note** (callout line stripped) as
   document context.
2. Replaces the callout with `> [!info] Researching: …` immediately, so the
   next rescan won't double-process it.
3. Runs the four-phase pipeline at the callout's chosen depth (default
   `standard`), passing the note context into both discovery and synthesis.
   Synthesis uses the dedicated `research_callout` prompt, so the agent answers
   the question *as it applies to that note* (resolving references like "our two
   options" against the note) rather than researching the literal phrase.
4. Replaces the marker in place with a `> [!done]` callout followed by the
   findings — appended inline to the same note, like a review comment, no
   separate note created.

**The answer is always DeepSeek V4 Pro, never Gemini.** Callout synthesis runs on
`task="synthesis"`, which is OpenRouter-only (see *LLM routing*). Only the source
*discovery* step can fall back to Gemini, and it never writes the answer — so an
inline answer is either the real DeepSeek report or nothing.

**Per-callout depth.** Depth is encoded in the callout *type*, so it stays one-tap
insertable and still renders as a normal callout (your question is the visible
title). `_CALLOUT_RE` captures the suffix (`-` or `|` separator both accepted):

| Marker | Depth |
|--------|-------|
| `> [!research] q` | standard (default) — ~4-6 sources, single draft |
| `> [!research-deep] q` | deep — ~8-12 sources, single draft |
| `> [!research-comprehensive] q` | comprehensive — ~12-20 sources, draft → critique → revise (slow; drops many source clips — heavy for an inline question) |

**Concurrency & recovery.** The watchdog writes the host note from a separate
process, so several guards keep it from fighting your editing:
- **Quiet gate.** `find_research_callouts` skips any note modified within
  `CALLOUT_QUIET_SECS` (45s, < the 60s rescan) — a note you're actively typing in
  is left alone and picked up once you pause.
- **Robust write-back.** The final marker swap matches the in-progress line by
  topic with a tolerant regex (any depth label, any `.`/`…` form), so ellipsis or
  whitespace drift during the run can't miss it. If the marker was removed/edited
  away entirely, the `> [!done]` result is **appended** rather than silently
  dropped — a finished report is never lost.
- **Crash recovery.** A callout stranded at `> [!info] Researching…` on a note
  untouched for `STALE_CALLOUT_SECS` (30 min) — a crashed run, or OpenRouter down —
  is reverted to `> [!research…]` (original depth preserved) by
  `revert_stale_callouts()` each rescan, so it retries. This is the callout analogue
  of pending-trigger recovery; the earlier "stuck on `[!info]` forever" limitation
  is gone.

**Writing a callout easily (Templater).** Rather than typing the brackets, insert
one with a hotkey. Install the **Templater** community plugin, point its template
folder at `Templates/`, and use the three templates the system scaffolds:
`Research Callout.md`, `Research Callout (Deep).md`, `Research Callout
(Comprehensive).md` — each is a one-liner with `<% tp.file.cursor() %>` so the
cursor lands right after the marker. Bind each to a hotkey (Templater → *Template
Hotkeys*, e.g. `Ctrl+Shift+R` for standard) on desktop, and add the same commands
to Obsidian's mobile toolbar for one-tap insertion on iPhone. You only need the
standard one to start.

Trigger notes (`_triggers/`) write a standalone `Research/` note with the generic
`research_synthesis` prompt (not the callout-tailored one). Their **body** — the
`## Details` and `## Acceptance Criteria` sections from the Research Trigger
template — is passed through as a research *brief* (`context_kind="brief"`): the
details steer discovery, and synthesis is told the report must satisfy the
acceptance criteria. HTML comments (the template's usage notes) are stripped and
an empty body leaves behaviour unchanged. This differs from callouts, which pass
the host note as `context_kind="callout"` and answer *as it applies to that note*.

### Concept Pipeline

Research reports are *outcome-focused* — they resolve a question and engage the
expert debate. **Concept notes** are the learning-focused complement: standalone,
university-textbook-level explainers of the foundational ideas a report leans on
(`Concept - Dopamine`, `Concept - Elasticity of Taxable Income`). They are written
once, reused everywhere, and accumulate across the vault — so the knowledge base
teaches the basics, not just the frontier. Concept notes are **derived
automatically** from finished research reports; you don't author them.

```
1. At the end of process_research_trigger() — after the report is written and
   indexed — _conceptualize() runs. It is best-effort: any failure is caught and
   never blocks completing the research run (concepts are additive).
2. _conceptualize() — one cheap `moc`-tier call with the concept_extract prompt:
   - Reads the finished report and picks the foundational, *reusable* concepts it
     leans on but doesn't build from scratch — capped at MAX_CONCEPTS_PER_REPORT
     (8), fewer is better. It rejects the report's own thesis, one-off jargon, and
     paper-specific proper nouns, and is shown the existing concepts **each with a
     one-line gloss** (`_existing_concept_summaries` → the first paragraph of each
     note) — not just their names — so it can judge a *meaning* match rather than a
     name match: reuse the note when the sense is the same (even under a different
     name), and disambiguate its `term` (e.g. `Attention (machine learning)`) when a
     name collides with an existing concept of a different sense. Returns
     {term, mention, why, context_excerpt} per pick.
   - Links each concept INTO the report body deterministically
     (_link_concepts_inline): the first clean occurrence of `mention` is wrapped as
     `[[Concept - Term|mention]]` — the report keeps its own wording as the alias,
     the link resolves to the concept note. Pure string surgery, no LLM and no
     regeneration (so it can never drift prose or truncate), skipping headings,
     fenced/inline code, existing wikilinks, and the trailing ## Sources /
     ## Related research apparatus. A trailing `## Concepts` list is appended as a
     backstop, so a concept whose mention can't be placed inline is never lost. The
     report gets `concepts_extracted: true`.
   - For each pick: if a Concepts/ note or a pending `concept: true` trigger already
     covers it, no new work is queued — an existing note just gets this report added
     as a backlink (`## Appears in`). Otherwise a `concept: true` trigger is written
     to _triggers/.
3. The watchdog dispatches concept triggers (startup backlog, on-create, 60s rescan;
   routed by their `concept` flag) to process_concept_trigger() → _run_concept():
   - A restrained discovery loop (task `research`, concept_system prompt): queue
     sources ONLY if genuinely needed. For a well-settled concept that is **zero**,
     a normal outcome — unlike research, there is no "no sources" stub; the explainer
     is written from established knowledge. Relevant existing clippings are reused
     as seeds (find_relevant_clippings).
   - Any queued sources are processed by the same _process_source() into indexed
     clippings, reusable by future work.
   - Synthesis on the top model (task `synthesis`, OpenRouter-only, concept_synthesis
     prompt) writes the textbook explainer; citations are repaired against the
     concept's own sources (_repair_wikilinks) only when it actually cited any.
4. Written to Concepts/Concept - <term>.md (frontmatter `concept_note: true`; H1 = the
   bare term; `## Appears in` backlinks; `## Sources` only if it pulled any), indexed
   into a topic MOC under a `## Concepts` subsection, and the trigger is marked
   `concept: done`.
```

**One concept, one note, linked everywhere.** The dedup that makes concepts
cumulative rather than duplicative has three guards: the conceptualizer skips terms
already built (Concepts/ glob) or already queued (pending triggers); a returned term
is **snapped onto the canonical title** of an existing/pending concept via
`_match_key` — a casing- *and* punctuation-insensitive key, so `Chamley-Judd Theorem`
(hyphen) maps onto an existing `Chamley–Judd Theorem` (en-dash) note instead of
minting a dead link and a duplicate; and process_concept_trigger re-checks at run
time, adding a backlink instead of regenerating. Mechanical drift (casing, spacing,
punctuation) is caught deterministically; a genuine *synonym* — different words for
the same idea — relies on the model, but it now decides from each existing concept's
one-line gloss rather than its bare name (`_existing_concept_summaries` feeds the
`concept_extract` prompt), so it can recognise a same-meaning note under a different
name and, conversely, keep two different concepts that share a name apart by
disambiguating the new one's `term`. The residual is a true synonym the model still
fails to connect from the gloss.

**Cost & scope.** Concept generation deliberately uses the best model (`synthesis`,
OpenRouter-only, no free-tier fallback) because the explainer's quality is what
matters; the dedup guarantee is what stops this compounding, since each concept is
paid for exactly once and thereafter only linked. Only standalone Research/ reports
are conceptualized — inline `[!research]` callouts are not (they annotate arbitrary
notes in place). If OpenRouter is unavailable the concept synthesis fails and the
trigger stays pending to retry, exactly like research synthesis.

### Reset Clips

```
python src/reset_clips.py                  # reset all processed clips
python src/reset_clips.py --dry-run        # preview without changes
```

Reverses the clip pipeline for all processed clips in Clippings/:
1. Finds notes with `processed: true`
2. Extracts original content from after the `## Original Content` header
3. Resets frontmatter: `processed: false`, removes `tags` and `processed_date`, sets `preserve_title: true`
4. Surgically removes clip entries from MOCs, decrementing `note_count`
5. Deletes MOCs that drop to 0 entries and cleans up `_index.md`

Skipped clips (no `## Original Content` header) and non-clip entries in MOCs
(research notes, sources) are left untouched.

**Re-indexing into new MOCs.** After changing the MOC-assignment prompt in
`indexer.py`, run `reset_clips.py` then re-run the pipeline (`python
src/obsidian_watchdog.py` drains the unprocessed backlog on startup). Every clip
is re-summarised and re-assigned to a MOC under the new rules. The one-shot
`preserve_title` flag set during reset tells `clipper.py` to keep each clip's
existing filename on reprocess, so wikilinks never break — only the analysis,
one-line summary, and MOC assignment are regenerated. The flag is consumed
(popped) on the first reprocess and never persists. Brand-new clips (no flag)
are renamed to their clean Gemini title as usual.

### Rejecting non-content (the `usable` gate)

A research fetch doesn't always return the article. A PDF can download fine yet
extract to nothing (subsetted fonts, scanned images); an anti-scraping page
(Cloudflare, reCAPTCHA, Anubis) or a paywall/login wall returns HTTP 200 with an
interstitial instead of the paper. Left unchecked, that garbage became a real,
indexed, citable clip — and worse, feeding a bot-wall page to the analyzer made
it *hallucinate* a plausible-looking summary from the title alone. Three layers
now stop this, cheapest first:

1. **`fetch_url` refuses non-HTML/binary responses.** It checks `Content-Type`
   and sniffs `%PDF-` magic bytes; a PDF (or other binary) served at a URL is not
   web text, so it returns the `Failed to fetch` sentinel rather than decoding
   raw bytes into "text". Deterministic — no false positives on real articles.
2. **`extract_paper_text` never re-fetches the PDF as its own landing page.** The
   landing-page fallback is skipped when the landing URL equals the PDF URL or is
   itself a `.pdf` — re-fetching it could only ever return bytes. (This was the
   exact bug behind the `%PDF-…endobj…stream` byte-dump clips.)
3. **The clip analyzer decides the semantic cases.** `clip_analysis` returns a
   `usable` boolean; the model — which already reads the content — sets it `false`
   for binary dumps, CAPTCHA/paywall/JS interstitials, and error pages, and
   `true` for genuine content (even a thin abstract). This is the only reliable
   test for interstitials: keyword-matching "reCAPTCHA"/"proof-of-work" would
   false-positive on a real article *about* those topics. `clipper.py` discards a
   `usable: false` stub (`pdf_processor.py` archives it), and `_process_source`
   falls back to the abstract.

**Backfill (`clean_junk_clips.py`).** The gate only prevents *future* junk;
clips saved before it were removed with a one-off, reviewable pass (like
`consolidate_tags.py`):

```
python src/clean_junk_clips.py            # DRY RUN: list junk (raw-PDF / interstitial) + snippets
python src/clean_junk_clips.py --apply    # delete them, then fix MOCs and _index.md
```

It classifies each clip from its captured `## Original Content` (raw-PDF tokens,
mojibake ratio, short pages carrying bot-wall markers) and the analyzer's own
failure admissions, prints every candidate for review, and — with `--apply` —
deletes them and reuses `reset_clips`' MOC surgery so each `[[link]]` is stripped,
`note_count` decremented, and emptied MOCs deleted. Abstract-only clips (a real
abstract under a warning callout) are never flagged. Report/concept notes that
cite a removed clip are reported, not auto-edited (report prose is the user's).

### Discovery tools & queue_source

During phase ② the agent has five tools (`src/web_tools.py`, `src/academic.py`):

| Tool | Purpose |
|------|---------|
| `search_arxiv(query)`    | arXiv papers — every result has a full-text PDF |
| `search_openalex(query)` | OpenAlex papers, all disciplines — OA PDF when available |
| `search_web(query)`      | Tavily web search (skipped with a clear message if no key) |
| `fetch_url(url)`         | Read a web page to evaluate it |
| `queue_source(url, title, kind, reason, abstract)` | Mark a source for processing |

`queue_source` dedups against the vault and the current queue; there is **no
count cap** — the agent queues as many sources as the topic needs. The
`abstract` argument is kept as a fallback so a source survives as an
abstract-only clipping (`full_text: false`) when its full text can't be
retrieved (e.g. paywalled PDFs). Queued sources are processed in phase ③ and
become ordinary indexed clippings in `Clippings/` (tagged
`source_type: research_found`), so they're reusable by future research.

### Vault Linting

```
python src/lint.py           # full report to console
python src/lint.py --quiet   # only print if issues found (for scheduled runs)
python src/lint.py --fix     # auto-fix what can be fixed
```

Scans the vault for common issues without using the Gemini API. Detects:
1. **Duplicate source URLs** — Multiple notes in Clippings/ or Sources/ with the same `source`
2. **Broken YAML frontmatter** — Notes with malformed or unparseable frontmatter
3. **MOC note_count mismatch** — Frontmatter count doesn't match actual `[[links]]`
4. **Orphan wikilinks** — `[[Some Note]]` in a MOC with no matching .md file anywhere
5. **Duplicate MOC entries** — Same `[[link]]` listed twice in one MOC
8. **Empty body notes** — Notes with no content after frontmatter
9. **Stale _index.md references** — Index points to MOCs that don't exist
10. **Unrendered LaTeX math** — Research/Concepts notes with `\(…\)` / display `\[…\]`
    that Obsidian won't render (see *Math rendering*)

`--fix` automatically resolves checks 1, 3, 4, 5, 9, and 10:
| Check | Fix action |
|-------|-----------|
| Duplicate sources | Deletes the lesser copy, then strips its `[[link]]` from any MOC (via `reset_clips` surgery) so deletion doesn't leave an orphan. The keeper is chosen by `_clip_quality`: **full_text over abstract-only, processed over unprocessed, larger body as tiebreaker** — not file age. The old "delete newest" rule dropped the good copy of a research duplicate (abstract stub written first, full text indexed later). |
| Orphan wikilinks | Removes dead `[[link]]` from MOC, decrements `note_count` |
| MOC note_count | Updates frontmatter to match actual count (counts `## Notes` **and** `## Concepts`) |
| Stale _index.md | Removes dead MOC references |
| Duplicate MOC entries | Deduplicates, updates `note_count` |
| Unrendered LaTeX math | Rewrites delimiters to `$…$` / `$$…$$` via `normalize_math_delimiters` (the detector is the fixer — reuses the exact normalised text) |

MOC checks count entries under both `## Notes` and `## Concepts` (`_moc_entry_links`),
since `indexer` increments `note_count` for concept explainers too; counting only
`## Notes` made every MOC with a concept note read as a false mismatch.

### Inline Duplicate Prevention

`clipper.py` checks for existing notes with the same `source` URL before processing a
new clip (`find_existing_source`). If found, the new clip is deleted and no LLM call is
wasted. The check passes `exclude=path` so it can't match the note against itself — the
first glob match is otherwise often the file being processed, and a self-match let real
duplicates through (a research stub whose title sorted before its twin sailed past the
`existing != path` guard — the overnight-duplicate origin).

Research source clips get a second layer: `researcher._clip_source` deletes its stub
whenever `process_clipped_note` returns `None` (discard, JSON-parse failure, or
full-text→abstract fallback). An abandoned `processed: false` stub would otherwise be
re-ingested by the watchdog's backlog scan as a second clip for the same source.

---

## Testing

A lightweight pytest safety net lives in `tests/` (repo root, not under `src/`),
scoped to the logic that actually breaks rather than coverage for its own sake.
`pytest.ini` sets `testpaths = tests` and, crucially, `addopts = -m "not llm"` so a
bare `pytest` never spends money. Install the test-only deps with
`pip install -r requirements-dev.txt` (just `pytest>=8`).

```
pytest                       # Tiers 1 + 2 (Tier 3 auto-deselected)
pytest tests/unit            # just Tier 1
pytest -m llm                # Tier 3 only — real LLM, costs money, needs API keys
```

Three tiers:

| Tier | Folder | What | In default run? |
|------|--------|------|-----------------|
| 1 | `tests/unit/` | Pure `str → str` logic: math-delimiter normalisation, tag hygiene, wikilink repair, report-completeness gate, note naming, depth parsing, concept linking, MOC helpers | ✅ free, ~0.5s |
| 2 | `tests/integration/` | Filesystem behaviour against a throwaway vault (`tmp_vault` fixture): note round-trips, source dedup, MOC surgery, clip pipeline with the **LLM mocked** | ✅ free, fast |
| 3 | `tests/llm/` | **Real LLM calls** — judgments that can't be mocked: the `usable` gate, citation repair, MOC granularity | ❌ opt-in (`-m llm`), a few cents |

Two fixtures carry the setup. `tests/conftest.py` puts `src/` on `sys.path` (the
modules import each other by bare name) and provides `tmp_vault`: a real on-disk
vault whose config path constants (`INBOX_PATH`, `INDEX_PATH`, …) are monkeypatched
into **every module that imported them** — rebinding only `config.INBOX_PATH`
wouldn't reach the copy `clipper`/`reset_clips`/`indexer` already hold. `tests/llm/`
adds `require_openrouter` / `require_gemini_or_openrouter`, which **skip cleanly**
when the key is absent (keys come from `.env` via `config.py`), so `pytest -m llm`
on a keyless machine reports skips, not errors.

**Reach for Tier 3 after touching a prompt or model route** — the changes unit
tests can't catch: edit `clip_analysis.md`/`clip_system.md` → `test_usable_gate.py`;
edit `research_repair_links.md` or synthesis routing → `test_citation_integrity.py`;
edit the MOC-assignment prompt → `test_moc_granularity.py`. Tier-3 fixtures
(bot-wall / article text) live in `tests/llm/fixtures/`.

---

## MOC Format & Granularity

MOCs are created and named entirely by the agent (`assign_to_moc()` in
`indexer.py`). Each MOC should be a **specific sub-field, not a broad domain** —
`MOC - Generative Models`, not `MOC - AI`; `MOC - Sports Nutrition`, not
`MOC - Health`. The assignment prompt gives Gemini sub-field examples and
explicitly calls out broad labels as too coarse. Critically, it does **not**
tell Gemini to "be consistent with existing names" — that instruction used to
create a feedback loop where every new note piled into whichever big MOC already
existed (e.g. a single 40-note `MOC - AI`). Acronyms (AI, LLM, ML, RL…) are kept
uppercase by `_titlecase_topic()`.

Example:

```markdown
---
moc: true
topic: Generative Models
note_count: 5
updated: '2026-06-20'
---

# Generative Models — Knowledge Index

## Notes
- [[An Introduction to Flow Matching and Diffusion Models]] — Noise→data via learned neural vector fields simulating ODE/SDE trajectories
- [[Geometric Stability Analysis of Autonomous Generative Models]] — Riemannian gradient flow on marginal energy with implicit metrics for stability
- [[Metropolis-Adjusted Diffusion Models]] — Replaces biased ULA correctors with Metropolis-adjusted, score-based steps
```

**Entry summaries are one line, always.** An entry is a single markdown list item,
so a multi-line summary splits the list and breaks the MOC. Clips and PDFs pass the
clipper's one-liner; research notes get theirs from `research_tags` (which returns
the MOC summary *and* the tags in one call, since both need the model to have read
the report). Research notes used to pass `report[:300]` — a raw prefix that dragged
the report's H1 and opening paragraphs into the list item. `indexer.one_line()`
now flattens and clamps whatever a caller passes, at the point the entry is
written, so no future caller can reintroduce this.

**Concept notes get their own subsection.** A MOC lists clippings and research
reports under `## Notes` and concept explainers under a separate `## Concepts`
heading (`update_moc(..., section="Concepts")`), created on demand when the first
concept lands. Both subsections count toward `note_count`, and the linter counts
both (see *Vault Linting*) — counting only `## Notes` made every MOC with a concept
note read as a false mismatch.

---

## Tags & the Canonical Vocabulary

Every clip, PDF, and research note carries frontmatter `tags`. Left unmanaged, an
LLM tags freely and the vault fragments into near-duplicates that don't connect
when you filter — real examples from this vault: `machinelearning` / `machine-learning`,
`llm` / `llms` / `languagemodels`, `sports-betting` / `sportsbetting` / `sportsbook`,
`wealth-tax` / `wealthtax`. Two mechanisms keep tags consistent:

**1. Deterministic normalisation (`notes.normalize_tag` / `normalize_tags`).**
Every tag written by any pipeline is canonicalised to **lowercase-hyphenated**:
casing, a leading `#`, and separators (spaces / underscores / slashes → hyphen)
are fixed, so `Tax Evasion` and `tax_evasion` both become `tax-evasion`. This is
pure string hygiene — it does *not* merge synonyms or split concatenations.

**2. A canonical vocabulary (`Index/_tags.md`).** A single hand-editable list of
the tags in use, fed into the tagging prompts (`clip_analysis`, `research_tags`)
via `indexer.format_tag_vocabulary()`. The prompts instruct the model to **reuse
an existing tag whenever it means the same thing** (use `machine-learning`, don't
coin `ml`) and only coin a new tag when nothing fits — while still keeping tags
specific, so the vocabulary doesn't collapse into a few mega-tags. This is the
opposite of the MOC-assignment rule (which deliberately avoids "be consistent
with existing names" to prevent one giant MOC): for tags, cross-cutting reuse is
exactly what we want. New tags are appended to `_tags.md` by `indexer.register_tags()`,
which every note flows through inside `index_note()`.

**Backfill (`consolidate_tags.py`).** The vocabulary only prevents *future* drift;
existing notes are unified with a one-time pass. It's two-step so the merge can be
reviewed before it touches the vault:

```
python src/consolidate_tags.py            # DRY RUN: LLM proposes raw→canonical, writes tag_map.json
#   ... review / hand-edit tag_map.json (drops shown as "" ) ...
python src/consolidate_tags.py --apply    # applies the reviewed map, rewrites frontmatter, seeds _tags.md
```

Apply reads the reviewed `tag_map.json` (it does **not** re-call the model, so what
you approve is what's written), rewrites every note's `tags`, drops junk (stopword
fragments, bare years, broken markers), skips notes already canonical, and seeds
`Index/_tags.md` most-used-first. Any tag missing from the map keeps its normalised
self, so nothing is silently lost.

---

## Trigger Note Format

Create this in `_triggers/` from your iPhone (the `Templates/Research Trigger.md`
template in the vault scaffolds it for you):

The note's **title** is the topic (name it "Quantum computing breakthroughs
2025"), so there is no `topic:` field — the frontmatter is just plumbing:

```markdown
---
research: true
urls:
  - https://specific-article.com/to-include
output: "Research - Quantum Computing.md"
---

## Depth
- [ ] standard — ~4-6 searches, single-draft report
- [ ] deep — ~8-12 searches, single-draft report
- [x] comprehensive — ~12-20 searches, draft → critique → revise

## Details
Focus on error-correction and logical-qubit milestones from 2024-2025; skip
pop-science coverage. We care about what's actually shipping in hardware.

## Acceptance Criteria
- [ ] Names the specific labs/companies and their qubit counts
- [ ] Distinguishes physical vs logical qubits throughout
- [ ] Ends with a short "what to watch next" section
```

**The title is the topic.** There is no `topic:` frontmatter field — it was
redundant with the Details brief, so the note's name now carries the focused
phrase and the brief carries the detail. `process_research_trigger` reads the
topic from the note's filename stem (a leading "research - " is stripped).

**Naming.** `output:` is optional and only needed to pin an exact filename.
Without it the note is named from the finished report's own H1 — synthesis writes
a far better title than a trigger keyword ("Research - World Models and Their
Origins" rather than "Research - World Models"), and that title used to be
discarded. A subtitle after `:` / `–` is trimmed. Only if the report has no H1
does the topic (the note's title) become the name.

**An empty title falls back to the brief.** A trigger created from the phone's
+ button and left at the Obsidian default carries no topic in its title —
naming a run `Research - Untitled` that also searched prior knowledge for the
literal string "Untitled". An empty or `Untitled*` title now falls back to the
brief, where the actual question is.

The frontmatter drives the run; the `## Details` and `## Acceptance Criteria`
body sections are passed to the agent as a brief (see *Inline Research Callouts*
above). Both body sections are optional — omit them for a title-only run.

**Depth** is chosen by ticking one box in a `## Depth` checklist — a plugin-free
"pick one" that works by tapping on mobile. `_depth_from_body()` reads the first
ticked box (scoped to the `## Depth` section, so Acceptance-Criteria boxes are
never misread), and that section is stripped from the brief. Precedence: a ticked
box wins, then a frontmatter `depth:` key (still supported), then `standard`.

**Ready gate.** The template ends with a `## Ready` section holding a single
`- [ ] Start research` checkbox. While that box is unticked the pipeline skips
the trigger — and because the note stays `research: true`, every 60s rescan
re-checks it, so ticking the box (from any device) starts the run within about
a minute. This lets a brief be drafted over multiple sessions without the
watchdog grabbing it mid-edit. A trigger with **no** `## Ready` section at all
is treated as ready, so minimal hand-written triggers still fire immediately.
The section is stripped from the brief like `## Depth`.
Discovery thoroughness (source count is up to the agent):
- `standard` — ~4-6 searches, single-draft synthesis
- `deep` — ~8-12 searches, single-draft synthesis
- `comprehensive` — ~12-20 searches, draft → critique → revise synthesis

**Concept triggers.** `_triggers/` also holds `concept: true` notes, but you never
write these — the conceptualizer pass emits them automatically (see *Concept
Pipeline*). The watchdog routes on the `concept` flag, so they never collide with
your `research: true` triggers, and they carry `term` / `source` frontmatter plus
the concept's context in the body.

---

## Web Clipper Setup

1. Install **Obsidian Web Clipper** from the Chrome or Firefox extension store
2. In extension settings → set save folder to `Clippings/`
3. Set the note template:

```
---
clipped: true
source: "{{url}}"
site: "{{domain}}"
date: "{{date}}"
processed: false
tags: []
---

{{content}}
```

The `processed: false` flag is what the watchdog keys off. It flips to `true`
after the agent has processed the note so it never gets processed twice.

---

## Setup Steps

### 1. Sync

**The vault lives on local disk and is synced by Obsidian Sync. Do not put it on
iCloud Drive, OneDrive, Dropbox, or any other file-level sync service.**

- Keep the vault somewhere local, e.g. `C:\Users\You\Obsidian\MyVault`
- Obsidian → Settings → Sync → create a remote vault and upload from the Surface
- On iPhone: create a **new empty vault**, then connect it to that remote

Why this matters — the vault was originally on iCloud Drive, and iCloud
conflict-copies any file two devices write concurrently. Obsidian rewrites
`workspace.json` on nearly every pane change and `community-plugins.json` on every
plugin toggle, so those files were being renamed away constantly. The end state:
**1,239 conflict copies** inside `.obsidian/` (1,225 of them `workspace N.json`),
and **no canonical `workspace.json` or `community-plugins.json` at all**. With the
enabled-plugins file permanently destroyed, every community plugin silently
reverted to disabled on each launch — Templater could never stay on — and the
constant sync churn made the iPhone crawl.

This is not only a two-device race: with Obsidian fully closed on both devices,
a locally written `community-plugins.json` was still renamed to
`community-plugins 10.json` within about a second.

When syncing settings, **"Active community plugin list" and "Installed community
plugins" must both be enabled** in Sync settings. Either one alone leaves the
phone knowing a plugin should be on but not having it installed.

Two consequences for this system:
- Obsidian Sync only runs while Obsidian is **open**, so Obsidian must stay
  running on the Surface for phone-written triggers to reach the watchdog.
- `PDF_INBOX_PATH` is deliberately still on iCloud — it is outside the vault and
  is a low-churn drop folder, so none of the above applies to it.

### 2. Gemini API Key
- Go to https://aistudio.google.com
- Click "Get API key" → Create API key (no credit card needed)
- Set environment variables on your Surface (or put them in `.env`):
  ```
  setx GEMINI_API_KEY "your-key-here"
  setx TAVILY_API_KEY "your-tavily-key"         # free tier at https://tavily.com
  setx OPENROUTER_API_KEY "your-openrouter-key" # https://openrouter.ai/keys
  ```
  Restart your terminal after running this. `TAVILY_API_KEY` is optional —
  without it, research falls back to the academic sources only. `OPENROUTER_API_KEY`
  is **required for research reports**: synthesis is routed OpenRouter-only (the
  `synthesis` task has no Gemini fallback), so without the key a research run
  gathers sources but the report itself fails and the trigger retries. The cheap
  `clip`/`moc` tasks still run fine on free Gemini without it.

### 3. Python Dependencies
```bash
pip install -r requirements.txt
```
(`google-genai`, `openai`, `watchdog`, `PyYAML`, `requests`, `python-dotenv`, `pymupdf`, `feedparser`)

To run the test suite, also `pip install -r requirements-dev.txt` (`pytest>=8`) — see *Testing*.

### 4. Configure the Script
Edit `src/config.py` — update `VAULT_PATH` to match your actual vault location.

### 5. Run
```bash
python src/obsidian_watchdog.py
```

You should see:
```
Obsidian Auto-Research System
Vault: C:\Users\You\Obsidian\MyVault
Watching for:
  Clips    → Clippings/
  Research → _triggers/
  Concepts → _triggers/ (concept: true)
Press Ctrl+C to stop.
```

### 6. Auto-start on Login (optional)
Create a `.bat` file:
```bat
@echo off
cd C:\path\to\knowledge-gardener
python src\obsidian_watchdog.py
```
Press `Win+R` → type `shell:startup` → copy the `.bat` file there.

---

## How the Knowledge Base Grows

```
Week 1
  Clip 5 diffusion-model papers → MOC - Generative Models.md created (5 entries)

Week 2
  Research "LLM fine-tuning"
    → agent finds MOC - LLM Training.md, uses it as prior context
    → saves 2 high-quality sources → Sources/
    → those sources also added to MOC - LLM Training.md
    → research note added → MOC - LLM Training.md grows

Week 3
  Clip a mechanistic-interpretability paper → MOC - Mechanistic Interpretability.md created
  Research "agentic coding tools"
    → agent pulls from MOC - LLM Training.md AND MOC - Agentic Systems.md
    → builds on both, doesn't repeat what's already known

Month 2
  30+ notes across many specific MOCs (not one giant catch-all)
  Research notes reference prior research
  Agent produces progressively more specific, personalised summaries
```

---

## Free Tier Limits

| Resource             | Limit          | Your likely usage  |
|----------------------|----------------|--------------------|
| Gemini requests      | 1,500/day      | research is call-heavy: relevance + discovery loop + N source analyses + synthesis (a few comprehensive runs can exhaust the free daily quota) |
| arXiv / OpenAlex     | Unlimited      | Free, no key       |
| Tavily searches      | ~1,000/month   | Free tier          |
| Obsidian Sync        | 1 GB (Standard) | ~32 MB — well within limits |

### LLM routing

Calls are routed per task through `llm.py` using the `ROUTING` map in `config.py`.
Each task has an ordered chain of `(provider, model, opts)`; the router tries each
in turn, falling through on daily-quota exhaustion or provider errors (an unset
`DEEPSEEK_API_KEY` counts as "unavailable", so DeepSeek entries are skipped).

| Task (call site) | Primary | Fallback |
|------------------|---------|----------|
| `clip` — clip/PDF summaries (`clipper.py`, `pdf_processor.py`) | Gemini 3 Flash (free) | DeepSeek V4 Flash (OpenRouter) |
| `moc` — MOC assignment + relevance (`indexer.py`) | Gemini 3 Flash (free) | DeepSeek V4 Flash (OpenRouter) |
| `research` — discovery tool loop only (`researcher.py`) | DeepSeek V4 Pro, max reasoning (OpenRouter) | Gemini 3 Flash (free) |
| `synthesis` — the report: draft/critique/revise/repair (`researcher.py`) | DeepSeek V4 Pro, max reasoning (OpenRouter) | **none — OpenRouter only** |

Free Gemini Flash carries the high-volume cheap tasks until its daily quota is
hit, then DeepSeek V4 Flash (via OpenRouter) takes over (better than Flash-Lite,
no quota cliff, ~cents). Discovery (`research`) still falls back to Gemini — it only
gathers sources, which become Gemini-processed clippings anyway. The report itself
(`synthesis`) runs on DeepSeek V4 Pro at OpenRouter's normalized
`reasoning={"effort": "xhigh"}` with an explicit `max_tokens`
(`RESEARCH_MAX_OUTPUT_TOKENS`) and has **no Gemini fallback** — reports must always
be written and reviewed by OpenRouter, so if it is unavailable the run fails and
retries rather than producing a lower-tier report. Adding a provider or re-routing a
task is a one-line edit to `ROUTING`. `thinking_level` is `high` on every Gemini
call (`config.py`).

Concept notes add no new routes: conceptualization (picking concepts + placing the
inline links) runs on the cheap `moc` task, the concept discovery loop reuses
`research`, and the concept explainer reuses `synthesis` (OpenRouter-only, top model
— the note's quality is worth it, and dedup means each concept is written once).

---

## Logs & Debugging

Three things that aren't obvious when debugging a run:

- **The log is `VAULT_PATH.parent/watchdog.log`** — beside the vault, *not* in the
  repo (don't conclude "there are no logs" from searching the project dir). It
  captures the whole pipeline's stdout — the `[research]` / `[llm]` / provider
  trace, finish reasons, fall-throughs — not just the watchdog's own events, and is
  flushed per line so it survives a mid-run kill. Rotates at 10 MB × 5.
- **The vault is on local disk** (`config.VAULT_PATH`), synced by Obsidian Sync.
  It used to live on iCloud Drive, which was slow enough that a recursive
  grep/glob over the whole vault could time out — that is no longer true, so a
  slow scan now means something is actually wrong rather than being expected.
  See *Sync* below for why it must not go back on a file-level sync service.
- **Truncated report ≠ manual stop.** An interrupted/crashed run writes no report
  and leaves the trigger `research: true` (retried next rescan). A report that
  *exists* but ends mid-sentence with its trigger `research: done` is a silent
  synthesis truncation that went through the success path — `_assert_report_complete()`
  now blocks that, and the completeness of a report is judged by its last character,
  not its length.

---

## Upgrade Path

| When                             | Upgrade                                              |
|----------------------------------|------------------------------------------------------|
| Hitting the Gemini daily quota   | Paid Gemini key, or lower `thinking_level` for per-source analysis |
| Want more paywalled full text    | Add an Unpaywall / institutional resolver to `academic.py` |
| Privacy concerns about free tier | Point at local Ollama model instead of Gemini        |
| Want semantic search             | Add ChromaDB + embeddings alongside the MOCs         |
| Want iPhone web trigger          | Add small Flask endpoint that creates trigger notes  |
| Want to use in OpenCode          | Research notes are plain markdown — already usable   |
