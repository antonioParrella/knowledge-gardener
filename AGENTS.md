# Obsidian Auto-Research System — Complete Build Plan

---

## What This Is

A personal knowledge pipeline that runs silently on your Surface Laptop,
triggered from your iPhone. It has two modes that feed the same growing
knowledge base:

**Clip mode** — Save any web page with the Obsidian Web Clipper browser
extension. The agent automatically summarises it, extracts key takeaways and
tags, and indexes it into your vault.

**Research mode** — Create a trigger note on your iPhone. The agent searches
the web, reads full articles, saves high-quality sources it finds, and writes
a comprehensive summary back to your vault.

Both pipelines maintain a set of **MOC notes** (Maps of Content) — topic-based
index notes the agent builds and updates automatically. Over time the agent
builds on prior knowledge rather than starting from scratch each time.

Total cost: **$0**.

---

## Stack

| Component      | Choice                          | Why                                      |
|----------------|---------------------------------|------------------------------------------|
| Sync           | iCloud Drive                    | Native on iPhone, works on Surface       |
| AI             | Gemini Flash (free tier)        | `gemini-3-flash-preview` + lite fallback |
| Trigger        | Python watchdog on Surface      | Monitors vault for new notes             |
| Web search     | DuckDuckGo (no key needed)      | Free, no signup                          |
| Index          | Markdown MOCs in vault          | Obsidian-native, no extra tools          |
| Web Clipper    | Obsidian Web Clipper extension  | Saves pages directly to vault            |

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
│   ├── clip_system.md      ← System prompt for clip processing
│   ├── clip_analysis.md    ← User prompt template for clip analysis
│   └── research_system.md  ← System prompt for agentic research
└── src/
    ├── config.py            ← All settings (edit VAULT_PATH here)
    ├── notes.py             ← Read/write markdown note helpers
    ├── gemini_client.py     ← Gemini API wrapper with fallback + retry
    ├── indexer.py           ← MOC creation and maintenance
    ├── web_tools.py         ← search_web, fetch_url, save_source tools
    ├── clipper.py           ← Web Clipper note processing pipeline
    ├── researcher.py        ← Agentic research pipeline
    ├── pdf_processor.py     ← PDF text extraction + summarisation
    ├── obsidian_watchdog.py ← Main entry point, file system monitor
    ├── reset_clips.py       ← Standalone script to revert clips to original
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
      depth: standard   # or deep
      urls:             # optional seed URLs
        - https://...
2. iCloud syncs to Surface
3. obsidian_watchdog.py detects new file in _triggers/
4. researcher.py checks existing MOCs for prior context on this topic
5. Gemini enters agentic tool loop:
      → calls search_web("quantum computing 2025")
      → calls fetch_url("https://...")
      → calls save_source("https://...", "reason") ← NEW
        (saves valuable sources to Sources/, indexes them)
      → repeats until satisfied
6. Gemini writes final markdown summary
7. Summary saved to Research/Research - Topic.md
8. Research note indexed into relevant MOC
9. Trigger note marked research: done
```

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

### save_source (new in v3)

During research, Gemini has access to a third tool: `save_source(url, reason)`.

When the agent finds a page it judges genuinely valuable — not just relevant,
but worth keeping — it calls this tool with the URL and a one-sentence reason.
Your script then:
- Fetches the full page content
- Asks Gemini to summarise it (same as the clip pipeline)
- Saves it to Sources/ with the agent's reason in the frontmatter
- Indexes it into the same MOC system as clipped notes

Capped at 5 saves per research run to prevent the agent saving everything.

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

---

## Trigger Note Format

Create this in `_triggers/` from your iPhone:

```yaml
---
research: true
topic: "quantum computing breakthroughs 2025"
depth: standard
urls:
  - https://specific-article.com/to-include
output: "Research - Quantum Computing.md"
---
```

`depth` controls how thorough the agent is:
- `standard` — 3-6 searches, 1-3 saved sources
- `deep` — 6-12 searches, 3-5 saved sources

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
- Set as an environment variable on your Surface:
  ```
  setx GEMINI_API_KEY "your-key-here"
  ```
  Restart your terminal after running this.

### 3. Python Dependencies
```bash
pip install google-genai watchdog PyYAML requests python-dotenv pymupdf
```

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
| Gemini requests      | 1,500/day      | 10–30/day          |
| DuckDuckGo searches  | Unlimited      | Free               |
| iCloud sync          | 5 GB free      | Well within limits |
| Web Clipper          | Free           | Free               |

The script automatically falls back from `gemini-3-flash-preview` →
`gemini-3.1-flash-lite-preview` if a model's daily quota is hit (see
`GEMINI_MODELS` in `config.py`).

---

## Upgrade Path

| When                             | Upgrade                                              |
|----------------------------------|------------------------------------------------------|
| DuckDuckGo results are too thin  | Add SerpAPI free tier (100 searches/day)             |
| Want richer search               | Use Gemini built-in grounding for some queries       |
| Privacy concerns about free tier | Point at local Ollama model instead of Gemini        |
| Want semantic search             | Add ChromaDB + embeddings alongside the MOCs         |
| Want iPhone web trigger          | Add small Flask endpoint that creates trigger notes  |
| Want to use in OpenCode          | Research notes are plain markdown — already usable   |
