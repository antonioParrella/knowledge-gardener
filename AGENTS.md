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
| AI             | Gemini 2.5 Flash (free tier)    | 1,500 req/day, strong tool calling       |
| Trigger        | Python watchdog on Surface      | Monitors vault for new notes             |
| Web search     | DuckDuckGo (no key needed)      | Free, no signup                          |
| Index          | Markdown MOCs in vault          | Obsidian-native, no extra tools          |
| Web Clipper    | Obsidian Web Clipper extension  | Saves pages directly to vault            |

---

## Vault Structure

```
MyVault/
│
├── Inbox/                          ← Web Clipper saves here
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
│   ├── MOC - AI.md
│   ├── MOC - Programming.md
│   └── MOC - Health.md
│
└── _triggers/                      ← Research trigger notes (from iPhone)
    └── research - quantum computing.md
```

---

## Project File Structure

```
obsidian_system/
└── src/
    ├── config.py          ← All settings (edit VAULT_PATH here)
    ├── notes.py           ← Read/write markdown note helpers
    ├── gemini_client.py   ← Gemini API wrapper with fallback + retry
    ├── indexer.py         ← MOC creation and maintenance
    ├── web_tools.py       ← search_web, fetch_url, save_source tools
    ├── clipper.py         ← Web Clipper note processing pipeline
    ├── researcher.py      ← Agentic research pipeline
    └── watchdog.py        ← Main entry point, file system monitor
```

---

## How Each Pipeline Works

### Clip Pipeline

```
1. You clip a page in your browser (Chrome/Firefox/Safari)
2. Web Clipper saves it to Inbox/ with:
      clipped: true
      processed: false
      source: "https://..."
3. iCloud syncs to Surface
4. watchdog.py detects new file in Inbox/
5. clipper.py reads the content
6. Gemini returns: title, summary, takeaways, tags (as JSON)
7. Note is rewritten with Summary + Key Takeaways sections
8. Note is renamed to the clean title
9. indexer.py assigns it to a MOC (or creates one)
10. MOC - Topic.md is updated with a link and one-line description
11. _index.md is updated if a new MOC was created
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
3. watchdog.py detects new file in _triggers/
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

---

## MOC Format

MOCs are created and maintained entirely by the agent. Example:

```markdown
---
moc: true
topic: AI
note_count: 8
updated: '2025-04-15'
---

# AI — Knowledge Index

## Notes
- [[Research - Gemini API Overview]] — Free tier limits, function calling, grounding options
- [[Attention Is All You Need]] — Original transformer paper, key architectural insights
- [[GPT-4 Technical Report]] — Benchmarks, safety approach, multimodal capability
- [[Source - Andrej Karpathy LLM Intro]] — Accessible explanation of how LLMs work
- [[Research - Agentic Coding Tools]] — Comparison of Claude Code, Cursor, OpenCode
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
2. In extension settings → set save folder to `Inbox/`
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
pip install google-generativeai watchdog PyYAML requests
```

### 4. Configure the Script
Edit `src/config.py` — update `VAULT_PATH` to match your actual vault location.

### 5. Run
```bash
cd obsidian_system/src
python watchdog.py
```

You should see:
```
Obsidian Auto-Research System
Vault: C:\Users\You\iCloud Drive\Obsidian\MyVault
Watching for:
  Clips    → Inbox/
  Research → _triggers/
Press Ctrl+C to stop.
```

### 6. Auto-start on Login (optional)
Create a `.bat` file:
```bat
@echo off
cd C:\path\to\obsidian_system\src
python watchdog.py
```
Press `Win+R` → type `shell:startup` → copy the `.bat` file there.

---

## How the Knowledge Base Grows

```
Week 1
  Clip 5 AI articles → MOC - AI.md created (5 entries)

Week 2
  Research "LLM fine-tuning"
    → agent finds MOC - AI.md, uses it as prior context
    → saves 2 high-quality sources → Sources/
    → those sources also added to MOC - AI.md
    → research note added → MOC - AI.md now has 8 entries

Week 3
  Clip Python tutorial → MOC - Programming.md created
  Research "agentic coding tools"
    → agent pulls from MOC - AI.md AND MOC - Programming.md
    → builds on both, doesn't repeat what's already known

Month 2
  30+ notes across 5-6 MOCs
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

The script automatically falls back through `gemini-2.5-flash` →
`gemini-2.0-flash` → `gemini-2.5-flash-lite` if rate limits are hit.

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
