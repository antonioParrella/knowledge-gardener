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

Total cost: **$0** (Tavily and Gemini both have free tiers; arXiv + OpenAlex need no key).

---

## Stack

| Component       | Choice                          | Why                                      |
|-----------------|---------------------------------|------------------------------------------|
| Sync            | iCloud Drive                    | Native on iPhone, works on Surface       |
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
    ├── researcher.py        ← Research pipeline (discovery → process → synthesis) + callouts
    ├── pdf_processor.py     ← PDF text extraction + summarisation
    ├── obsidian_watchdog.py ← Main entry point, file system monitor
    ├── reset_clips.py       ← Standalone script to revert clips to original
    ├── consolidate_tags.py  ← One-time backfill: unify drifted tags → canonical vocabulary
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
3. iCloud syncs to Surface
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
1. You create a trigger note on iPhone in _triggers/:
      research: true
      topic: "quantum computing breakthroughs"
      depth: comprehensive   # standard | deep | comprehensive
      urls:                  # optional seed URLs
        - https://...
2. iCloud syncs to Surface; obsidian_watchdog.py picks it up via the create
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
      - pdf  → academic.extract_paper_text() downloads the PDF and
               extracts full text (falls back to landing-page scrape)
      - web  → web_tools.fetch_url()
      - on failure with an abstract → abstract-only clip (full_text: false)
      The text is written to Clippings/ (source_type: research_found) and
      run through clipper.process_clipped_note() → indexed into a MOC.
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
   trigger wins, else the report's own H1 (trimmed of subtitle), else the topic.
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

### Inline Research Callouts

Add `> [!research] your question` anywhere in **any** note in the vault. On the
next periodic rescan, `find_research_callouts()` — which scans the whole vault
recursively (`VAULT_PATH`), skipping `_triggers/`, `.obsidian/`, `.trash/` —
picks it up and `process_research_callout()`:

1. Captures the **full text of the host note** (callout line stripped) as
   document context.
2. Replaces the callout with `> [!info] Researching: …` immediately, so the
   next rescan won't double-process it.
3. Runs the four-phase pipeline at `standard` depth, passing the note context
   into both discovery and synthesis. Synthesis uses the dedicated
   `research_callout` prompt, so the agent answers the question *as it applies to
   that note* (resolving references like "our two options" against the note)
   rather than researching the literal phrase.
4. Replaces the marker in place with a `> [!done]` callout followed by the
   findings — appended inline to the same note, like a review comment, no
   separate note created.

If the process dies mid-research the note is left with the `> [!info]` marker
(not retried) — a known limitation.

Trigger notes (`_triggers/`) write a standalone `Research/` note with the generic
`research_synthesis` prompt (not the callout-tailored one). Their **body** — the
`## Details` and `## Acceptance Criteria` sections from the Research Trigger
template — is passed through as a research *brief* (`context_kind="brief"`): the
details steer discovery, and synthesis is told the report must satisfy the
acceptance criteria. HTML comments (the template's usage notes) are stripped and
an empty body leaves behaviour unchanged. This differs from callouts, which pass
the host note as `context_kind="callout"` and answer *as it applies to that note*.

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

`--fix` automatically resolves checks 1, 3, 4, 5, and 9:
| Check | Fix action |
|-------|-----------|
| Duplicate sources | Deletes the lesser copy (iCloud ghost > "Copy" > unprocessed > newest) |
| Orphan wikilinks | Removes dead `[[link]]` from MOC, decrements `note_count` |
| MOC note_count | Updates frontmatter to match actual count |
| Stale _index.md | Removes dead MOC references |
| Duplicate MOC entries | Deduplicates, updates `note_count` |

### Inline Duplicate Prevention

`clipper.py` checks for existing notes with the same `source` URL before processing a new clip. If found, the duplicate is deleted and no Gemini call is wasted.

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

```markdown
---
research: true
topic: "quantum computing breakthroughs 2025"
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

**Naming.** `output:` is optional and only needed to pin an exact filename.
Without it the note is named from the finished report's own H1 — synthesis writes
a far better title than a trigger keyword ("Research - World Models and Their
Origins" rather than "Research - World Models"), and that title used to be
discarded. A subtitle after `:` / `–` is trimmed. Only if the report has no H1
does the `topic` become the name.

**A missing topic falls back to the brief.** `topic:` and the filename are both
`or`-fallbacks, so a trigger created from the phone's + button and left with
`topic: null` used to be named from its filename stem — yielding a real run
titled `Research - Untitled` that also searched prior knowledge for the literal
string "Untitled". An empty topic on an `Untitled*` note now falls back to the
brief, where the actual question is.

The frontmatter drives the run; the `## Details` and `## Acceptance Criteria`
body sections are passed to the agent as a brief (see *Inline Research Callouts*
above). Both body sections are optional — omit them for a topic-only run.

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

### 1. iCloud on Surface
- Install iCloud for Windows from the Microsoft Store
- Sign in with your Apple ID
- Enable iCloud Drive
- Move your Obsidian vault into the iCloud Drive folder:
  `C:\Users\You\iCloud Drive\Obsidian\MyVault`
- Reopen Obsidian pointing at the new vault path
- On iPhone: install Obsidian → open vault → select iCloud vault

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

### 4. Configure the Script
Edit `src/config.py` — update `VAULT_PATH` to match your actual vault location.

### 5. Run
```bash
python src/obsidian_watchdog.py
```

You should see:
```
Obsidian Auto-Research System
Vault: C:\Users\You\iCloud Drive\Obsidian\MyVault
Watching for:
  Clips    → Clippings/
  Research → _triggers/
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
| iCloud sync          | 5 GB free      | Well within limits |

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

---

## Logs & Debugging

Three things that aren't obvious when debugging a run:

- **The log is `VAULT_PATH.parent/watchdog.log`** — beside the vault, *not* in the
  repo (don't conclude "there are no logs" from searching the project dir). It
  captures the whole pipeline's stdout — the `[research]` / `[llm]` / provider
  trace, finish reasons, fall-throughs — not just the watchdog's own events, and is
  flushed per line so it survives a mid-run kill. Rotates at 10 MB × 5.
- **The vault is on iCloud** (`config.VAULT_PATH`), which is slow: a recursive
  grep/glob over the whole vault can time out — scope searches to one subfolder.
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
