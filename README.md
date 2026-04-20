# knowledge-gardener

Personal knowledge management system that runs automatically on your Surface, triggered from your iPhone. It has two modes:

**Clip mode** — Save any web page with the Obsidian Web Clipper extension. The agent automatically summarises it, extracts key takeaways and tags, and indexes it into your vault.

**Research mode** — Create a trigger note on your iPhone. The agent searches the web, reads full articles, saves high-quality sources it finds, and writes a comprehensive summary back to your vault.

Both pipelines maintain Maps of Content (MOCs) — topic-based index notes the agent builds and updates automatically. Over time the agent builds on prior knowledge rather than starting from scratch each time.

## Stack

- **Sync**: iCloud Drive (native on iPhone, works on Surface)
- **AI**: Gemini 2.5 Flash (free tier)
- **Trigger**: Python watchdog on Surface
- **Web search**: DuckDuckGo
- **Index**: Markdown MOCs in vault

## Setup

1. Install iCloud for Windows and move your Obsidian vault to iCloud Drive
2. Get a Gemini API key from https://aistudio.google.com
3. Install dependencies: `pip install google-generativeai watchdog PyYAML requests`
4. Configure `src/config.py` with your vault path
5. Run: `python src/watchdog.py`

## Vault Structure

```
MyVault/
├── Inbox/           ← Web Clipper saves here
├── Research/        ← Agent-generated research notes
├── Sources/         ← Sources saved during research
├── Index/           ← Agent-maintained knowledge base (MOCs)
└── _triggers/       ← Research trigger notes
```

## How It Works

- **Clip pipeline**: Clip a page → agent summarises → indexes into MOC
- **Research pipeline**: Create trigger note → agent researches → writes summary → indexes into MOC