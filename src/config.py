"""
config.py — Central configuration for the Obsidian Auto-Research System.
Edit VAULT_PATH to point at your iCloud vault before running.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Model output is full of unicode (em-dashes, math symbols like ∈, …). On Windows
# stdout defaults to cp1252, so printing such text raises UnicodeEncodeError — which,
# inside a provider tool loop, would abort an entire research run. Force UTF-8 with
# replacement so logging can never crash the pipeline. Imported by every module.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Vault paths ───────────────────────────────────────────────────────────────
# Update this to your actual iCloud vault location
VAULT_PATH   = Path(r"C:\Users\parre\iCloudDrive\iCloud~md~obsidian\Knowledge Garden")

INBOX_PATH    = VAULT_PATH / "Clippings"       # Web Clipper saves here
RESEARCH_PATH = VAULT_PATH / "Research"   # Agent research summaries
INDEX_PATH    = VAULT_PATH / "Index"       # MOCs and master index
SOURCES_PATH  = VAULT_PATH / "Sources"    # Agent-saved research sources
TRIGGERS_PATH = VAULT_PATH / "_triggers"  # iPhone research trigger notes

ALL_PATHS = [INBOX_PATH, RESEARCH_PATH, INDEX_PATH, SOURCES_PATH, TRIGGERS_PATH]

# ── PDF paths ─────────────────────────────────────────────────────────────────
# Drop PDFs here from iPhone or Surface — iCloud synced but outside the vault
PDF_INBOX_PATH = Path(r"C:\Users\parre\iCloudDrive\PDF Inbox")
# Processed PDFs are moved here — local only, never synced to iCloud
PDF_ARCHIVE_PATH = Path(r"C:\Users\parre\PDFArchive")

# ── API ───────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# OpenRouter (OpenAI-compatible gateway). Optional — if unset, the router skips
# OpenRouter entries in the routing chain and falls back to Gemini.
OPENROUTER_API_KEY  = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Tavily web search (free tier at tavily.com). If unset, web search is skipped
# and discovery relies on the academic APIs only.
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# Models to try in order if rate limits are hit
GEMINI_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview"
]

GEMINI_THINKING_LEVEL = "high"  # "minimal" | "low" | "medium" | "high"

# ── LLM routing ─────────────────────────────────────────────────────────────────
# Per-task provider chains. Each task maps to an ordered list of
# (provider, model, opts); the router (llm.py) tries each in turn, falling
# through on quota exhaustion / errors. Keep the cheap/free option first.
#
#   clip / moc → free Gemini Flash first, then DeepSeek V4 Flash via OpenRouter
#                (better than Flash-Lite, no quota cliff) for ~cents.
#   research   → DeepSeek V4 Pro at max reasoning via OpenRouter (frontier-class,
#                ~1/15th Opus cost), falling back to free Gemini Flash (tool-capable).
# Reasoning opt is OpenRouter's normalized effort (minimal/low/medium/high/xhigh;
# "xhigh" = max — OpenRouter rejects the literal "max").
GEMINI_FLASH = "gemini-3-flash-preview"
OR_FLASH     = "deepseek/deepseek-v4-flash"
OR_PRO       = "deepseek/deepseek-v4-pro"

ROUTING = {
    "clip": [
        ("gemini",     GEMINI_FLASH, {}),
        ("openrouter", OR_FLASH,     {}),
    ],
    "moc": [
        ("gemini",     GEMINI_FLASH, {}),
        ("openrouter", OR_FLASH,     {}),
    ],
    "research": [
        ("openrouter", OR_PRO,       {"reasoning_effort": "xhigh"}),
        ("gemini",     GEMINI_FLASH, {}),
    ],
}

# ── Academic search endpoints (no API key required) ─────────────────────────────
ARXIV_API_URL    = "http://export.arxiv.org/api/query"
OPENALEX_API_URL = "https://api.openalex.org/works"
TAVILY_API_URL   = "https://api.tavily.com/search"

# ── Research settings ─────────────────────────────────────────────────────────
MAX_SEARCH_ITERATIONS  = 30    # max tool-call loops per research run (safety bound)
FETCH_CONTENT_LIMIT    = 8000  # max chars from a fetched URL
CLIP_CONTENT_LIMIT     = 12000 # max chars from a clipped note
PAPER_CONTENT_LIMIT    = 40000 # max chars sent to clip-analysis for full-text papers
SYNTHESIS_RAW_EXCERPT  = 15000 # max raw chars per source passed to report synthesis
RESEARCH_CONTEXT_EXCERPT = 6000 # max chars of a prior research report passed as related-work context
ICLOUD_SETTLE_SECS     = 3     # seconds to wait after file created before processing

# ── Prompt files ───────────────────────────────────────────────────────────────
PROMPTS_PATH = Path(__file__).parent.parent / "prompts"

def load_prompt(name: str, **kwargs) -> str:
    """Load a prompt template from the prompts/ folder and fill in placeholders."""
    prompt_path = PROMPTS_PATH / f"{name}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")
    
    content = prompt_path.read_text(encoding="utf-8")
    for key, value in kwargs.items():
        content = content.replace(f"{{{key}}}", value)
    return content

# ── Watchdog ──────────────────────────────────────────────────────────────────
WATCH_RECURSIVE = True
RESCAN_INTERVAL_SECS = 60  # re-scan inbox/triggers every X seconds (iCloud doesn't fireEvents reliably)

# ── Lint ──────────────────────────────────────────────────────────────────────
LINT_REPORT_PATH = VAULT_PATH.parent / "lint_report.txt"  # written by lint.py
