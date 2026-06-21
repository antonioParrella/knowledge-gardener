# knowledge-gardener

Personal knowledge management system that runs automatically on your Surface, triggered from your iPhone. It has two modes:

**Clip mode** — Save any web page with the Obsidian Web Clipper extension. The agent automatically summarises it, extracts key takeaways and tags, and indexes it into your vault.

**Research mode** — Create a trigger note on your iPhone (or add a `> [!research]` callout to any note). The agent discovers academic papers (arXiv + OpenAlex) and authoritative web sources, retrieves their **full text**, runs each one through the clip pipeline so it becomes an indexed clipping, then synthesises a long, detailed report that cites every source with `[[wikilinks]]`.

Both pipelines maintain Maps of Content (MOCs) — topic-based index notes the agent builds and updates automatically. Over time the agent builds on prior knowledge rather than starting from scratch each time.

## Stack

- **Sync**: iCloud Drive (native on iPhone, works on Surface)
- **AI**: Gemini Flash (`gemini-3-flash-preview` → `gemini-3.1-flash-lite-preview` fallback)
- **Trigger**: Python watchdog on Surface
- **Academic search**: arXiv + OpenAlex (no key needed); full-text PDFs via PyMuPDF
- **Web search**: Tavily (free API key)
- **Index**: Markdown MOCs in vault

## Setup

1. Install iCloud for Windows and move your Obsidian vault to iCloud Drive
2. Get a Gemini API key from https://aistudio.google.com and set `GEMINI_API_KEY`
3. Get a free Tavily key from https://tavily.com and set `TAVILY_API_KEY` (optional — without it, research uses the academic sources only)
4. Install dependencies: `pip install -r requirements.txt`
5. Configure `src/config.py` with your vault path
6. Run: `python src/obsidian_watchdog.py`

## Vault Structure

```
MyVault/
├── Clippings/      ← Web Clipper saves here; research-found sources land here too
├── Research/       ← Agent-generated research reports
├── Sources/        ← Legacy agent-saved sources (still read for context/dedup)
├── Index/          ← Agent-maintained knowledge base (MOCs + _index.md)
└── _triggers/      ← Research trigger notes
```

## How It Works

- **Clip pipeline**: Clip a page → agent summarises → indexes into MOC
- **Research pipeline** (4 phases): ① select relevant existing clippings (Gemini-judged over the MOC catalog) → ② discovery loop searches arXiv/OpenAlex/web and queues sources → ③ each queued source's full text is fetched, analysed, and saved as an indexed clipping → ④ synthesis writes a long report citing every source with `[[wikilinks]]`. Paywalled papers fall back to an abstract-only clipping (`full_text: false`) rather than being dropped.

### Research triggers

Create a note in `_triggers/`:

```yaml
---
research: true
topic: "diffusion models for protein design"
depth: comprehensive   # standard | deep | comprehensive
urls:                  # optional seed URLs
  - https://...
---
```

`depth` controls how thorough discovery is. For `comprehensive`, synthesis runs a draft → critique → revise pass for extra depth. The agent queues as many or as few sources as the topic needs — there is no cap.

### Inline research callouts

Add a callout anywhere in an existing note and save:

```markdown
> [!research] How does X compare to Y?
```

On the next scan the agent researches it and replaces the callout in place with a `> [!done]` marker followed by the findings — no separate note needed.

## MOCs: Specific Sub-Fields, Not Broad Domains

MOCs are created and named by the agent (`assign_to_moc()` in `src/indexer.py`). Each MOC is meant to be a **specific sub-field**, not a broad catch-all. For example:

- ❌ `MOC - AI` → ✅ `MOC - LLM Training`, `MOC - Generative Models`, `MOC - Representation Learning`, `MOC - AI Consciousness`
- ❌ `MOC - Health` → ✅ `MOC - Sports Nutrition`, `MOC - Physiotherapy`, `MOC - Vaccines`
- ❌ `MOC - Finance` → ✅ `MOC - Tax Policy`, `MOC - Options Pricing`

The assignment prompt steers Gemini toward narrow topics and away from the "pile everything into one big MOC" failure mode (there is intentionally **no** "be consistent with existing names" instruction, which previously caused that). Common acronyms (AI, LLM, ML, RL…) are preserved in uppercase in MOC names via `_titlecase_topic()`.

## Resetting & Re-indexing Clips

Revert processed clips to their original content and clean up MOC/index references:

```bash
python src/reset_clips.py              # reset all processed clips
python src/reset_clips.py --dry-run    # preview without changes
```

This extracts everything after the `## Original Content` header, resets the `processed` flag, removes agent-assigned tags, and surgically cleans up MOC entries (leaving research/source entries untouched). It also tags each reset clip with a one-shot `preserve_title` flag.

**Re-indexing into new MOCs:** to re-bucket your whole vault after changing the MOC-assignment prompt, run `reset_clips.py` then re-run the pipeline (`python src/obsidian_watchdog.py`, which drains the unprocessed backlog on startup). The `preserve_title` flag ensures clips keep their existing filenames on reprocess, so wikilinks stay stable — only the analysis, one-line summary, and MOC assignment are regenerated. The flag is consumed on the first reprocess and never persists.

## Vault Health

```bash
python src/lint.py           # check for duplicate sources, broken frontmatter, orphan links, etc.
python src/lint.py --fix     # auto-fix what can be fixed (deletes dupes, removes dead links)
python src/lint.py --quiet   # quiet mode for scheduled runs (Task Scheduler)
```

Zero Gemini cost — just reads markdown files in the vault.
