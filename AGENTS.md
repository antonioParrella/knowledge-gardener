# Obsidian Auto-Research System

A personal knowledge pipeline that runs silently on a Surface Laptop, triggered
from an iPhone. This file is the **operational spec** — what the system does and
how to run it. The **why** behind each hardened mechanism (the bugs that motivated
it, the history, the residual edge cases) lives in `DESIGN_NOTES.md`; this file
points there rather than inlining it, so it stays legible for both you and agents.

---

## What This Is

Three content types feed one growing knowledge base:

**Clips** — Save any web page with the Obsidian Web Clipper. The agent summarises
it, extracts takeaways and tags, and indexes it into a MOC.

**Research reports** — Create a trigger note on your iPhone (or add a
`> [!research]` callout to any note). The agent discovers academic papers (arXiv +
OpenAlex) and web sources, retrieves their **full text**, runs each through the
clip pipeline so it becomes an indexed clipping, then synthesises a long report
that cites every source with `[[wikilinks]]`.

**Concept notes (automatic)** — Whenever a research report is compiled, the agent
extracts the foundational ideas it leans on and writes standalone,
university-textbook-level explainers (`Concept - <Term>`), linked inline from the
report. Reports resolve questions; concept notes teach the underlying ideas once
and are reused by everything that references them.

Both the clip and research pipelines maintain **MOC notes** (Maps of Content) —
topic-based index notes the agent builds and updates automatically, so it builds on
prior knowledge rather than starting from scratch each time.

Total cost: **$0** on free tiers (Tavily + Gemini free; arXiv + OpenAlex need no key;
OpenRouter is cents and only used for report synthesis).

---

## Stack

| Component       | Choice                          | Why                                      |
|-----------------|---------------------------------|------------------------------------------|
| Sync            | Obsidian Sync (vault on local disk) | Understands Obsidian's write patterns; iCloud shredded `.obsidian/` (see DESIGN_NOTES § Sync) |
| AI              | Gemini Flash (free) + OpenRouter | Per-task routing; synthesis on DeepSeek V4 Pro via OpenRouter |
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
├── Clippings/        ← Web Clipper saves here; agent renames after processing
├── Research/         ← Agent-generated research reports
├── Concepts/         ← Agent-written concept explainers (learning layer)
├── Sources/          ← Sources the agent saved during research
├── Index/            ← Agent-maintained knowledge base
│   ├── _index.md     ← Master list of all MOCs
│   ├── _tags.md      ← Canonical tag vocabulary (fed to tagging prompts)
│   ├── _dashboard.md ← Pipeline status mirror (see Dashboard & Notifications)
│   └── MOC - *.md    ← specific sub-fields, not broad domains
└── _triggers/        ← Research/concept trigger notes (from iPhone)
```

---

## Project File Structure

```
obsidian_system/
├── prompts/
│   ├── clip_system.md / clip_analysis.md        ← clip processing
│   ├── research_system.md                       ← discovery tool loop
│   ├── research_synthesis.md                    ← report synthesis (draft/revise)
│   ├── research_critique.md                     ← comprehensive critique pass
│   ├── research_callout.md                      ← inline [!research] callout answers
│   ├── research_correct.md                      ← in-place correction of a callout's host note
│   ├── research_repair_links.md                 ← resolves citations that aren't real titles
│   ├── research_tags.md                         ← MOC summary + tags for a finished report
│   ├── concept_extract.md / concept_system.md / concept_synthesis.md
│   ├── moc_assign.md / moc_assign_system.md     ← MOC assignment
│   └── tag_consolidation.md                     ← consolidate_tags.py
└── src/
    ├── config.py            ← All settings (edit VAULT_PATH here) + ROUTING map
    ├── notes.py             ← Read/write markdown helpers; tag + math normalisation
    ├── llm.py               ← Task-routed LLM facade (llm_simple / llm_tool_loop)
    ├── providers/           ← Per-provider impls behind a common interface
    │   ├── base.py          ← Provider ABC, control-flow exceptions, parse_json_response
    │   ├── gemini.py        ← Gemini provider (google-genai)
    │   └── openrouter.py    ← OpenRouter provider (openai SDK)
    ├── gemini_client.py     ← Backward-compat shim → llm.py
    ├── indexer.py           ← MOC maintenance; find_relevant_clippings / _research
    ├── academic.py          ← arXiv + OpenAlex search; PDF download + text extract
    ├── fulltext.py          ← open-access retrieval ladder + identity/structure gates
    ├── web_tools.py         ← search/fetch/queue_source discovery tools
    ├── clipper.py           ← Web Clipper note processing pipeline
    ├── researcher/          ← Research + callout + concept pipelines (a package)
    │   ├── __init__.py      ← public API facade (what the watchdog imports)
    │   ├── sources.py       ← ③ fetch a queued source → indexed clipping
    │   ├── synthesis.py     ← ④ write report + citation repair + completeness gate
    │   ├── corrections.py   ← ⑤ correct the host note in place (callouts only)
    │   ├── pipeline.py      ← shared _run_research core + MOC index-entry helper
    │   ├── triggers.py      ← research trigger notes (_triggers/) + note naming
    │   ├── callouts.py      ← inline `> [!research]` callouts answered in place
    │   └── concepts.py      ← concept extraction + explainer generation
    ├── pdf_processor.py     ← PDF text extraction + summarisation
    ├── telemetry.py         ← Run state + spend ledger (what the dashboard reads)
    ├── dashboard.py         ← Local web UI (:8765) + vault dashboard note
    ├── notify.py            ← ntfy push notifications on run completion
    ├── obsidian_watchdog.py ← Main entry point, file system monitor
    ├── lint.py              ← Vault health checker (run periodically)
    └── (one-off scripts)    ← reset_clips, clean_junk_clips, consolidate_tags,
                               fix_math_delimiters — see "Maintenance scripts"
```

The `researcher/` package is a clean dependency DAG: `sources` and `synthesis` are
leaves; `pipeline` builds on them; `concepts`, `callouts`, and `triggers` build on
`pipeline`; `__init__` re-exports the public API. See `researcher/__init__.py` for
the module map.

---

## How Each Pipeline Works

### Clip Pipeline

```
1. You clip a page in your browser → Web Clipper saves to Clippings/ with
   clipped: true, processed: false, source: "https://..."
2. Obsidian Sync brings it to the Surface
3. obsidian_watchdog.py detects the new file
4. clipper.py skips it if the source URL already exists in the vault
5. Gemini returns title, summary, takeaways, tags (as JSON)
6. Note is rewritten with Summary + Key Takeaways, renamed to the clean title
7. indexer.py assigns it to a MOC (or creates one) and updates _index.md
```

### Research Pipeline

A trigger note in `_triggers/` (its **title** is the topic) drives four phases via
`researcher.process_research_trigger()`:

**① Prior knowledge — two separate lanes.**
- `find_relevant_clippings(topic)` — walks the **MOC graph** in two cheap steps
  rather than flattening it: `_shortlist_mocs()` picks the topic indexes worth
  opening from their names alone (~75 lines), then only those MOCs' entries are read
  and a second call picks the notes. These become cited primary **sources**. The old
  single call handed the model every note in the vault (~34k tokens at 900 notes, and
  unbounded) and silently returned nothing at that size — see DESIGN_NOTES § Prior
  knowledge. Capped by `MOC_SHORTLIST_MAX` / `MOC_CANDIDATE_MAX`; a shortlist the
  model can't produce falls back to deterministic name matching.
- `find_relevant_research(topic)` — picks related prior reports in Research/.
  These are **related work**, not sources: they ground discovery and synthesis, and
  the new report cross-links to them under a "## Related research" heading instead
  of re-deriving or re-citing them. The report being (re)written is excluded.

**② Discovery tool loop** (`pipeline._run_research` → `llm_tool_loop`) — the agent
calls `search_arxiv` / `search_openalex` / `search_web` / `fetch_url`, then
`queue_source(url, title, kind, reason, abstract)` for each keeper. No source cap;
already-vaulted URLs are skipped.

**③ Process each queued source** (`sources._process_source`) — three attempts,
stopping at the first that yields a usable document:

1. **The URL as given.** `pdf` → `academic.extract_paper_text()`; `web` →
   `web_tools.fetch_url()` (refuses non-HTML/binary responses). One request, and it
   already works about half the time.
2. **The open-access ladder** (`fulltext.retrieve()`) — runs when attempt 1 fails
   *or* when the clipper judges what it returned not to be the document. Derives
   every identifier it can (DOI / PMCID / PMID / arXiv) from the URL plus whatever
   discovery persisted, upgrades a PMID or DOI to a PMCID where possible, then walks
   the free key-less routes in descending order of **measured** precision: Europe PMC
   → NCBI BioC → arXiv → Unpaywall → OpenAlex.
3. **Abstract-only**, exactly as before — or skipped if there's no abstract.

The text is written to Clippings/ (`source_type: research_found`) and run through
`clipper.process_clipped_note()`, which returns a `usable` verdict. Non-content
(raw bytes, bot-wall / CAPTCHA / paywall, error page) is discarded — a blocked
source becomes a clean, thin, citable clip rather than a garbage note (see
DESIGN_NOTES § Rejecting non-content). A real source is indexed into a MOC.

**Two gates guard the ladder**, because a wrong document is worse than a thin one:
an *identity* check (retrieved text vs the source's abstract) and a *structure*
check (a real article has sections; a landing page is an abstract in chrome). A
route keyed by an identifier read off the URL is trusted and skips both. Never
raises — any failure falls through to the abstract-only clip. Why these routes and
not the higher-recall ones, and how the thresholds were calibrated: DESIGN_NOTES §
Full-text recovery.

**Identifiers are persisted** (`doi` / `pmcid` / `pmid` / `arxiv` in clip
frontmatter, plus `full_text_route` when the ladder succeeded). A source queued
with its DOI stays recoverable forever; one queued without it can only be found
again by an unreliable title search.

**④ Synthesise** (`synthesis._synthesise`) — builds an **unnumbered** source index
of exact `[[wikilink]]` titles and writes the report. Related prior reports are
passed as a distinct block. standard/deep = single draft; comprehensive = draft →
critique → revise. All synthesis runs OpenRouter-only (task `synthesis`, never
Gemini). Citations are then repaired (`synthesis._repair_wikilinks`), math
delimiters normalised, and a report cut off mid-generation is rejected by
`_assert_report_complete()` before anything is written — the trigger stays pending
and retries. (Why unnumbered, why OpenRouter-only, why the gates: DESIGN_NOTES §
Citation integrity, § Math rendering.)

The report is saved to Research/, indexed into a MOC, and the trigger marked
`research: done`. Filename comes from `triggers._research_note_name()`: an explicit
`output:` wins, else the report's own H1, else the topic. The trigger's brief body
is kept under a completion banner so it can be cleanly re-run (flip
`research: done` → `true`).

### Inline Research Callouts

Add `> [!research] your question` anywhere in **any** note. On the next rescan,
`find_research_callouts()` (scans the vault recursively, skipping `_triggers/`,
`Templates/`, `.obsidian/`, `.trash/`, and conflict copies) picks it up and
`process_research_callout()`:

1. Captures the **full text of the host note** (callout line stripped) as context.
2. Replaces the callout with `> [!info] Researching: …` immediately, so the next
   rescan won't double-process it.
3. Runs the four-phase pipeline at the callout's depth, passing the note context
   into discovery and synthesis. Uses the `research_callout` prompt, so the agent
   answers the question *as it applies to that note* (resolving references like "our
   two options" against the note).
4. **Corrects the host note in place** (`corrections.apply_corrections()`, phase ⑤)
   — see below.
5. Replaces the marker with the answer block, rendered by `render_answer_block()`
   — inline like a review comment, no separate note created.

**The answer is always DeepSeek V4 Pro, never Gemini** (synthesis is OpenRouter-only;
only source *discovery* can fall back to Gemini, and it never writes the answer).

**A callout is often an objection, not a question.** "This is wrong because…"
answered by appending leaves the wrong claim standing above its own refutation, and
the note ends up arguing with itself. So before the answer is written, phase ⑤
offers the note for in-place correction: the model drives an exact-match
`edit_note(old_string, new_string, why)` tool through `llm_tool_loop` — the same
mechanism a coding agent uses, where a failed or ambiguous match is a loud error it
repairs on the next iteration.

Two properties make that safe to run unattended. **The model never touches disk** —
it mutates an in-memory working copy, and `verify_edits()` decides whether that copy
is ever written (heading structure preserved, note not collapsed below
`CALLOUT_MIN_LENGTH_RATIO`, no invented `[[links]]`); a rejection discards the
**whole set**. And **every failure is best-effort** — disabled, oversized, no edits
wanted, gate rejection, or an outright crash all leave the note unedited and append
the answer exactly as before. Correcting is an improvement on answering, never a
precondition. A retryable rejection gets `MAX_CORRECTION_ATTEMPTS` (2) tries from a
pristine copy each time; a structural one doesn't retry at all. Prior answer blocks
are protected — they're a dated record. (Why exact-match over regeneration, and why
the gates are a weak substitute for a test suite: DESIGN_NOTES § Callout corrections.)

**The answer block** is delimited by `<!-- kg:answer -->` sentinels (invisible in
Obsidian, an exact anchor for the protected-span check). `clean_question()` restates
a hastily-typed question as the heading and keeps the original as an `*Asked:*`
subtitle; corrected passages are listed verbatim in a foldable `> [!quote]-` block
so nothing is silently lost. Headings sit at `####` (answer subheads at `#####`) and
sources are a one-line `**Sources:** [[A]], [[B]]`, so an inline annotation never
competes with the host note's outline.

**Per-callout depth** is encoded in the callout *type* so it stays one-tap
insertable and still renders as a normal callout:

| Marker | Depth |
|--------|-------|
| `> [!research] q` | standard (default) — ~4-6 sources, single draft |
| `> [!research-deep] q` | deep — ~8-12 sources, single draft |
| `> [!research-comprehensive] q` | comprehensive — ~12-20 sources, draft → critique → revise |

Concurrency guards (quiet gate, tolerant write-back, stale-callout crash recovery)
keep the watchdog from fighting your live editing — see DESIGN_NOTES § Callout
concurrency.

**Writing a callout easily (Templater).** Install the **Templater** community
plugin, point its template folder at `Templates/`, and use the scaffolded
`Research Callout.md` / `Research Callout (Deep).md` / `Research Callout
(Comprehensive).md` templates (each a one-liner with `<% tp.file.cursor() %>`).
Bind each to a hotkey on desktop and add them to the mobile toolbar for one-tap
insertion. You only need the standard one to start.

Trigger notes vs callouts: a trigger writes a standalone Research/ note with the
generic `research_synthesis` prompt, and its `## Details` / `## Acceptance Criteria`
body is passed as a research *brief* (`context_kind="brief"`). Callouts pass the
host note as `context_kind="callout"` and answer as it applies to that note.

### Concept Pipeline

At the end of `process_research_trigger()`, `concepts._conceptualize()` runs
(best-effort; a failure never blocks the research run):

1. One cheap `moc`-tier call (`concept_extract`) reads the finished report and picks
   the foundational, *reusable* concepts it leans on but doesn't build from scratch
   — capped at `MAX_CONCEPTS_PER_REPORT` (8), fewer is better. It rejects the
   report's own thesis, one-off jargon, and paper-specific proper nouns, and is
   shown existing concepts **each with a one-line gloss** so it can judge a *meaning*
   match, not just a name match. Returns `{term, mention, why, context_excerpt}`.
2. Each concept is linked INTO the report body deterministically
   (`_link_concepts_inline`): the first clean occurrence of `mention` becomes
   `[[Concept - Term|mention]]`. Pure string surgery — no LLM, no regeneration. A
   trailing `## Concepts` list is appended as a backstop.
3. For each pick: if a Concepts/ note or a pending `concept: true` trigger already
   covers it, just add this report as a backlink (`## Appears in`). Otherwise write
   a `concept: true` trigger to `_triggers/`.

The watchdog dispatches concept triggers to `process_concept_trigger()` →
`_run_concept()`: a restrained discovery loop (queue sources ONLY if genuinely
needed — for a well-settled concept that is **zero**, and unlike research there is
no "no sources" stub), then synthesis on the top model (`concept_synthesis`,
OpenRouter-only) writes the textbook explainer. Written to
`Concepts/Concept - <term>.md`, indexed into a MOC's `## Concepts` subsection,
trigger marked `concept: done`.

**One concept, one note, linked everywhere** — the dedup guarantee that keeps this
cumulative rather than duplicative is described in DESIGN_NOTES § Concept dedup.

---

## Trigger Note Format

Create this in `_triggers/` from your iPhone (the `Templates/Research Trigger.md`
template scaffolds it). The note's **title is the topic** — there is no `topic:`
field:

```markdown
---
research: true
urls:
  - https://specific-article.com/to-include   # optional seed URLs
output: "Research - Quantum Computing.md"      # optional; pins an exact filename
---

## Depth
- [ ] standard — ~4-6 searches, single-draft report
- [ ] deep — ~8-12 searches, single-draft report
- [x] comprehensive — ~12-20 searches, draft → critique → revise

## Details
Focus on error-correction and logical-qubit milestones from 2024-2025; skip
pop-science coverage.

## Acceptance Criteria
- [ ] Names the specific labs/companies and their qubit counts
- [ ] Distinguishes physical vs logical qubits throughout

## Ready
- [ ] Start research
```

- **Depth** is picked by ticking one box in `## Depth` (a plugin-free "pick one"
  that works by tapping on mobile). Precedence: ticked box → frontmatter `depth:` →
  `standard`.
- **`## Details` / `## Acceptance Criteria`** are passed to the agent as a brief
  (both optional; omit for a title-only run). HTML comments are stripped.
- **Ready gate:** while `## Ready` holds an unticked box the trigger is skipped and
  re-checked every 60s rescan, so a brief can be drafted over multiple sessions and
  started (from any device) by ticking the box. A trigger with **no** `## Ready`
  section is treated as ready, so minimal hand-written triggers fire immediately.
- The title falls back to the brief if empty or left at Obsidian's `Untitled`
  default (see DESIGN_NOTES § Naming).

**Concept triggers** (`concept: true`, with `term` / `source` frontmatter) also live
in `_triggers/` but you never write them — the conceptualizer emits them. The
watchdog routes on the `concept` flag so they never collide with `research: true`.

---

## MOC Format & Granularity

Each MOC is a **specific sub-field, not a broad domain** — `MOC - Generative
Models`, not `MOC - AI`; `MOC - Sports Nutrition`, not `MOC - Health`. MOCs are
created and named entirely by the agent (`assign_to_moc()` in `indexer.py`). Why the
prompt deliberately does *not* say "be consistent with existing names": DESIGN_NOTES
§ MOC granularity.

```markdown
---
moc: true
topic: Generative Models
note_count: 5
updated: '2026-06-20'
---

# Generative Models — Knowledge Index

## Notes
- [[An Introduction to Flow Matching and Diffusion Models]] — Noise→data via learned neural vector fields
- [[Geometric Stability Analysis of Autonomous Generative Models]] — Riemannian gradient flow on marginal energy

## Concepts
- [[Concept - Reward Prediction Error]] — The dopamine signal encoding actual-minus-expected reward
```

- **Entry summaries are one line, always** — an entry is a single markdown list
  item, so a multi-line summary breaks the MOC. `indexer.one_line()` flattens and
  clamps whatever a caller passes.
- **Concept notes get their own `## Concepts` subsection**
  (`update_moc(..., section="Concepts")`). Both subsections count toward
  `note_count`, and the linter counts both.

---

## Tags & the Canonical Vocabulary

Every clip, PDF, and research note carries frontmatter `tags`, kept consistent by
two mechanisms:

1. **Deterministic normalisation** (`notes.normalize_tag` / `normalize_tags`) —
   every tag is canonicalised to lowercase-hyphenated.
2. **A canonical vocabulary** (`Index/_tags.md`) — fed into the tagging prompts so
   the model reuses an existing tag whenever it means the same thing and only coins
   a new one when nothing fits. New tags are appended by `indexer.register_tags()`.

This is the opposite of the MOC rule — for tags, cross-cutting reuse is exactly what
we want; see DESIGN_NOTES § Tags for the fragmentation this prevents.

---

## LLM Routing

Calls are routed per task through `llm.py` using the `ROUTING` map in `config.py`.
Each task has an ordered chain of `(provider, model, opts)`; the router tries each
in turn, falling through on quota exhaustion or provider errors.

| Task (call site) | Primary | Fallback |
|------------------|---------|----------|
| `clip` — clip/PDF summaries (`clipper.py`, `pdf_processor.py`) | Gemini 3 Flash (free) | DeepSeek V4 Flash (OpenRouter) |
| `moc` — MOC assignment + relevance + concept extract + question restatement (`indexer.py`, `concepts.py`, `callouts.py`) | Gemini 3 Flash (free) | DeepSeek V4 Flash (OpenRouter) |
| `research` — discovery tool loop only (`pipeline.py`, `concepts.py`) | DeepSeek V4 Pro, max reasoning (OpenRouter) | Gemini 3 Flash (free) |
| `synthesis` — the report: draft/critique/revise/repair, and the correction loop (`synthesis.py`, `concepts.py`, `corrections.py`) | DeepSeek V4 Pro, max reasoning (OpenRouter) | **none — OpenRouter only** |

The report itself (`synthesis`) runs on DeepSeek V4 Pro at `reasoning={"effort":
"xhigh"}` with an explicit `max_tokens` (`RESEARCH_MAX_OUTPUT_TOKENS`) and has **no
Gemini fallback** — if OpenRouter is unavailable the run fails and retries rather
than producing a lower-tier report. Adding a provider or re-routing a task is a
one-line edit to `ROUTING`. `thinking_level` is `high` on every Gemini call.

**Provider exhaustion is handled in two places.** A spent quota or OpenRouter
key/credit cap raises `QuotaExhausted`, which the router parks on a
`QUOTA_COOLDOWN_SECS` cooldown so it stops re-attempting a doomed call. And
because synthesis is OpenRouter-only, `llm.research_preflight()` polls the key's
live remaining credit (`GET /api/v1/key`) before any research/concept/callout run
and **blocks it** if credit is under `RESEARCH_MIN_KEY_CREDITS` — the trigger stays
pending and auto-resumes on a later rescan once the cap is raised (a healthy
reading also clears the cooldown). It fails open on an unreachable/uncapped key so
a blip can't wedge research. Why both, and how they reconcile: DESIGN_NOTES §
Provider exhaustion.

Concept notes add no new routes: conceptualization runs on `moc`, the concept
discovery loop reuses `research`, and the explainer reuses `synthesis`.

Every OpenRouter request carries `usage: {include: true}`, so the response returns
that call's real charge in `usage.cost`; the router books it against the task in
`telemetry.py`, which is what makes per-report cost visible on the dashboard.

### Free Tier Limits

| Resource        | Limit          | Notes  |
|-----------------|----------------|--------|
| Gemini requests | **20/day per model** | `GenerateRequestsPerDayPerProjectPerModel-FreeTier`. Tiny — a *single* comprehensive research run exhausts it, then DeepSeek V4 Flash takes over. (1,500/day is the paid-tier figure; the dashboard meter used to be drawn against it and read ~2% on a spent quota.) |
| arXiv / OpenAlex| Unlimited      | Free, no key |
| Tavily searches | ~1,000/month   | Free tier |
| Obsidian Sync   | 1 GB (Standard)| ~32 MB used |

---

## Discovery Tools

During phase ② the agent has five tools (`src/web_tools.py`, `src/academic.py`):

| Tool | Purpose |
|------|---------|
| `search_arxiv(query)`    | arXiv papers — every result has a full-text PDF. Paced to arXiv's 1-request-per-3s limit and retried on 429 (shared with PDF downloads and the OA ladder's arXiv route); an unrecoverable rate-limit is now **printed**, because a silent one reads to the agent as "arXiv has nothing" — see DESIGN_NOTES § arXiv rate limiting |
| `search_openalex(query)` | OpenAlex papers, all disciplines — OA PDF when available |
| `search_web(query)`      | Tavily web search (skipped with a clear message if no key) |
| `fetch_url(url)`         | Read a web page to evaluate it |
| `queue_source(url, title, kind, reason, abstract, doi, landing_url)` | Mark a source for processing |

`queue_source` dedups against the vault and the current queue; there is **no count
cap**. The `abstract` argument is kept so a source survives as an abstract-only
clipping (`full_text: false`) when its full text can't be retrieved. `doi` /
`landing_url` are optional but matter a lot: the URL a search hands over is often an
opaque publisher PDF path carrying no identifier, and a source queued without its
DOI can't be re-resolved later by the open-access ladder. Academic search results
now print the DOI on its own line so the agent can pass it straight through.

---

## Development Workflow

`main` is the **deployed** branch. The watchdog runs whatever is checked out in
the working tree (`python src/obsidian_watchdog.py` imports the `.py` files from
disk at start-up — not a committed or pushed revision), so `main` must stay green
and runnable at all times. Do work on short-lived branches and merge back only
once the suite passes:

1. **Branch off main:** `git switch -c fix/<slug>` (or `feat/<slug>`).
2. **Make the change**, keeping commits focused, and add/adjust tests — a change
   to a `str → str` helper or a routing/gate decision should land with a Tier 1/2
   test (see Testing).
3. **Run `pytest`** — Tiers 1 + 2 must pass; reach for `-m llm` after touching a
   prompt or model route.
4. **Merge to main and push:** `git switch main && git merge <branch> && git push`.
5. **Restart the watchdog from main** so the running process matches the deployed
   branch — never leave it running a half-merged working tree. Because the running
   code is the working tree, a merge only takes effect on the next restart.

Keep it lightweight — this is a solo project, so the point is only that `main`
stays runnable and every change lands with its tests, not heavyweight git-flow.

---

## Testing

A pytest safety net lives in `tests/` (repo root). `pytest.ini` sets
`addopts = -m "not llm"` so a bare `pytest` never spends money. Install test deps
with `pip install -r requirements-dev.txt`.

```
pytest                # Tiers 1 + 2 (Tier 3 auto-deselected)
pytest tests/unit     # just Tier 1
pytest -m llm         # Tier 3 only — real LLM, costs money, needs API keys
```

| Tier | Folder | What | In default run? |
|------|--------|------|-----------------|
| 1 | `tests/unit/` | Pure `str → str` logic: math normalisation, tag hygiene, wikilink repair, completeness gate, note naming, depth parsing, concept linking, MOC helpers, the callout edit contract + correction gates, identifier extraction + the full-text identity/structure gates, the arXiv rate-limit gate, the run ledger + dashboard note render | ✅ free, ~0.5s |
| 2 | `tests/integration/` | Filesystem behaviour against a throwaway vault (`tmp_vault`): note round-trips, source dedup, MOC surgery, clip pipeline with the **LLM mocked**, the three-attempt source cascade with the **network mocked**, the MOC-graph prior-knowledge lookup, dashboard note + `/api/state` over HTTP | ✅ free, fast |
| 3 | `tests/llm/` | **Real LLM calls** — the `usable` gate, citation repair, MOC granularity | ❌ opt-in (`-m llm`) |

`tests/conftest.py` puts `src/` on `sys.path` and provides `tmp_vault` (monkeypatches
the config path constants into every module that imported them). It also has an
**autouse** `isolate_telemetry` fixture that redirects the run ledger to a tmp dir
and mutes ntfy for every test — without it, any test touching a pipeline function
would write run state (and push notifications) from the live vault. `tests/llm/`
adds `require_openrouter` / `require_gemini_or_openrouter`, which skip cleanly when
the key is absent.

**Reach for Tier 3 after touching a prompt or model route:** edit
`clip_analysis.md`/`clip_system.md` → `test_usable_gate.py`; edit
`research_correct.md` or the edit-tool schema → `test_callout_corrections.py`; edit
`research_repair_links.md` or synthesis routing → `test_citation_integrity.py`; edit
the MOC-assignment prompt → `test_moc_granularity.py`.

---

## Maintenance Scripts & Linting

### Vault linting

```
python src/lint.py           # full report to console
python src/lint.py --quiet   # only print if issues found (for scheduled runs)
python src/lint.py --fix     # auto-fix what can be fixed
```

Scans the vault without the Gemini API. Detects: duplicate source URLs, broken YAML,
MOC `note_count` mismatch, orphan wikilinks, duplicate MOC entries, empty-body notes,
stale `_index.md` references, and unrendered LaTeX math. `--fix` resolves duplicate
sources (keeper chosen by `_clip_quality`, not age), orphan/duplicate MOC entries,
`note_count`, stale index refs, and math delimiters.

### One-off backfills (reviewable: dry-run, then `--apply`)

| Script | Purpose |
|--------|---------|
| `reset_clips.py` | Revert processed clips to original (for re-indexing under new MOC/prompt rules). `--dry-run` to preview. |
| `clean_junk_clips.py` | Purge junk clips (raw-PDF byte dumps, bot-wall interstitials) saved before the `usable` gate, and fix MOCs. |
| `consolidate_tags.py` | Unify drifted tags → canonical vocabulary. Proposes `tag_map.json`, apply after review. |
| `fix_math_delimiters.py` | Rewrite LaTeX `\(…\)`/`\[…\]` → Obsidian `$…$`/`$$…$$` in reports written before the guard existed. |
| `reconceptualize.py` | Re-extract concepts from reports conceptualized while the extractor only saw their first 15k chars. `--only <substr>` for one report. `--apply` queues paid generation runs — dry-run first. |
| `backfill_fulltext.py` | Retry full-text retrieval for clips that settled for an abstract before the OA ladder existed, and re-analyse the ones it recovers. `--limit N` / `--only <substr>` to scope, `--resolve-titles` to also try resolving an identifier from the title (lower precision, still gated), `--apply` to commit. |

These are one-time migrations, not part of the live pipeline — see the relevant
DESIGN_NOTES section for what each was cleaning up.

---

## Setup

### 1. Sync

**The vault lives on local disk and is synced by Obsidian Sync. Do not put it on
iCloud Drive, OneDrive, Dropbox, or any file-level sync service** (this is not
optional — see DESIGN_NOTES § Sync for the 1,239-conflict-copy disaster that
motivated the rule).

- Keep the vault local, e.g. `C:\Users\You\Obsidian\MyVault`.
- Obsidian → Settings → Sync → create a remote vault and upload from the Surface.
- On iPhone: create a **new empty vault**, then connect it to that remote.
- In Sync settings, enable **both** "Active community plugin list" and "Installed
  community plugins".
- Obsidian Sync only runs while Obsidian is **open**, so Obsidian must stay running
  on the Surface for phone-written triggers to reach the watchdog.

### 2. API Keys

```
setx GEMINI_API_KEY "your-key-here"           # https://aistudio.google.com (no card)
setx TAVILY_API_KEY "your-tavily-key"         # optional — https://tavily.com
setx OPENROUTER_API_KEY "your-openrouter-key" # required for reports — https://openrouter.ai/keys
setx NTFY_TOPIC "some-long-unguessable-name"  # optional — phone push notifications
```

Restart the terminal after. `NTFY_TOPIC` is any hard-to-guess string — ntfy.sh
topics are public to anyone who knows the name, so treat it as the secret; unset
means no notifications. `TAVILY_API_KEY` is optional (research falls back to
academic sources without it). `OPENROUTER_API_KEY` is **required for research
reports** — synthesis is OpenRouter-only, so without it a run gathers sources but the
report fails and retries. The cheap `clip`/`moc` tasks run fine on free Gemini alone.

### 3. Dependencies & config

```
pip install -r requirements.txt        # google-genai, openai, watchdog, PyYAML,
                                        # requests, python-dotenv, pymupdf, feedparser
pip install -r requirements-dev.txt    # pytest>=8, for the test suite
```

Edit `src/config.py` → set `VAULT_PATH` to your vault location.

### 4. Run

```
python src/obsidian_watchdog.py
```

You should see the vault path, the watched routes (Clips → Clippings/, Research
& Concepts → _triggers/), and the dashboard URL to open on your phone. To
auto-start on login, put a `.bat` that runs it into `shell:startup` (there's a
`start.bat` launcher in the repo).

### 5. Web Clipper

Install **Obsidian Web Clipper**, set its save folder to `Clippings/`, and set the
note template so `processed: false` is present (that flag is what the watchdog keys
off):

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

---

## Dashboard & Notifications

Three ways to see what the pipeline is doing. All read one source of truth —
`telemetry.py`, which records run phases and per-call cost as the pipeline runs.
Why a ledger rather than a log parser, and where the cost figures come from:
DESIGN_NOTES § Telemetry.

**Web dashboard** — the watchdog serves a live, read-only page on
`http://<surface>:8765` from a daemon thread. Current run with a phase stepper
and source-by-source progress, spend today / 7d / 30d, free-tier meters (Gemini
calls/day, Tavily searches/month), OpenRouter credit remaining, share of spend by
task, and the last 20 runs. Polls every 2s. Preview it without waiting for a real
run:

```
python src/dashboard.py --demo     # seeded data on :8765, writes no real state
```

**Vault note** — the same state rendered into `Index/_dashboard.md` on a 60s
cadence by `start_note_writer()`'s own daemon thread, so Obsidian Sync carries it
to the phone with **no networking at all**. Refreshed only when something actually
changed (an identical rewrite every 60s would push a file to the phone every
minute). Use this when you're off the LAN and haven't set up Tailscale.

Both presentations start **before** `drain_backlog()`, and the note renders from a
thread rather than the main loop, so a long run is watchable while it happens. Get
this ordering wrong and a restart that picks up a pending trigger goes dark for the
whole run — no page on the port, and a note frozen at the previous process's last
write (DESIGN_NOTES § Telemetry).

**ntfy push** — set `NTFY_TOPIC` and finished research / concept / callout runs
push to your phone (`"Research done — <title>", 8m32s · $0.83 · 41 model calls`).
*Any* kind notifies on failure, including clips. Unset = off, and the pipeline
behaves identically without it.

Everything here is best-effort: telemetry swallows its own errors, the server runs
in a daemon thread, and a failed note write is logged and dropped. Losing the
dashboard never costs a run.

### Phone access — open the firewall first

The dashboard binds all interfaces and the watchdog logs every URL it's reachable
at when it starts. But **Windows blocks inbound connections to `python.exe` by
default**, so the page works on the Surface and times out from the phone until you
add one rule. Run this **once**, in an *elevated* PowerShell:

```powershell
New-NetFirewallRule -DisplayName "Knowledge Gardener dashboard" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private
```

`-Profile Private` covers home Wi-Fi and the Tailscale interface (both register as
Private) while still refusing connections on Public networks — which is the right
default given the dashboard has no auth. A port rule rather than a program rule on
purpose: recreating `venv/` replaces `python.exe` and would silently break a
program-scoped rule.

Symptom check: "connection refused" from the phone but fine on the Surface means
either this rule is missing, or the server bound loopback only.

### Phone access from anywhere (Tailscale)

With the rule above in place, a phone on the **same Wi-Fi** reaches the dashboard
at `http://<surface-ip>:8765`. There is no auth — keep it off untrusted networks.
For access from anywhere, use Tailscale rather than port forwarding:

1. Install Tailscale on the Surface (`winget install tailscale.tailscale`) and
   sign in — this puts the machine on your private tailnet.
2. Install the Tailscale app on the iPhone and sign in with the same account.
3. Find the Surface's tailnet name in the app (e.g. `surface.tail1234.ts.net`).
4. Open `http://surface.tail1234.ts.net:8765` on the phone, from any network.

Nothing is exposed to the public internet and no ports are forwarded; the tailnet
is a private WireGuard network between your own devices. Optionally run
`tailscale serve https / http://localhost:8765` on the Surface for HTTPS with a
real certificate.

### Settings

| Setting | Where | Default |
|---|---|---|
| `DASHBOARD_ENABLED` / `DASHBOARD_PORT` | `config.py` | `True` / `8765` |
| `DASHBOARD_NOTE_PATH` | `config.py` | `Index/_dashboard.md` (`None` disables) |
| `NTFY_TOPIC` / `NTFY_SERVER` | env | unset (off) / `https://ntfy.sh` |
| `STATE_PATH` / `EVENTS_PATH` | `config.py` | beside `watchdog.log`, outside the vault |

`dashboard_state.json` (live snapshot) and `events.jsonl` (append-only history of
every run and LLM call) sit **beside the vault, not in it** — same rule as the log.

## Logs & Debugging

- **The log is `VAULT_PATH.parent/watchdog.log`** — beside the vault, *not* in the
  repo. It captures the whole pipeline's stdout (the `[research]` / `[llm]` /
  provider trace, finish reasons, fall-throughs), flushed per line so it survives a
  mid-run kill. Rotates at 10 MB × 5. Why `print()` ends up there: DESIGN_NOTES §
  Logging.
- **The vault is on local disk** — a slow recursive scan now means something is
  actually wrong (it used to be expected on iCloud).
- **Truncated report ≠ manual stop.** An interrupted run writes no report and leaves
  the trigger `research: true` (retried). A report that *exists* but ends
  mid-sentence with `research: done` was a silent synthesis truncation —
  `_assert_report_complete()` now blocks that (DESIGN_NOTES § Citation integrity).

---

## How the Knowledge Base Grows

```
Week 1  Clip 5 diffusion papers → MOC - Generative Models created (5 entries)
Week 2  Research "LLM fine-tuning" → agent uses MOC - LLM Training as prior context,
        saves 2 sources, grows the MOC, extracts concept notes
Week 3  Research "agentic coding tools" → pulls from LLM Training AND Agentic Systems
        MOCs, builds on both instead of repeating known material
Month 2 30+ notes across many specific MOCs; reports reference prior reports;
        concept notes teach the basics once and are reused everywhere
```

---

## Upgrade Path

| When | Upgrade |
|------|---------|
| Hitting the Gemini daily quota | Paid Gemini key, or lower `thinking_level` for per-source analysis |
| Want more paywalled full text | Add an Unpaywall / institutional resolver to `academic.py` |
| Privacy concerns about free tier | Point at local Ollama instead of Gemini |
| Want semantic search | Add ChromaDB + embeddings alongside the MOCs |
| Want iPhone web trigger | Add a small Flask endpoint that creates trigger notes |
| Want to use in OpenCode | Research notes are plain markdown — already usable |
```
