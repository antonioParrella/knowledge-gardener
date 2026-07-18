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
CONCEPTS_PATH = VAULT_PATH / "Concepts"   # Agent-written textbook-level explainers
INDEX_PATH    = VAULT_PATH / "Index"       # MOCs and master index
SOURCES_PATH  = VAULT_PATH / "Sources"    # Agent-saved research sources
TRIGGERS_PATH = VAULT_PATH / "_triggers"  # iPhone research trigger notes

ALL_PATHS = [INBOX_PATH, RESEARCH_PATH, CONCEPTS_PATH, INDEX_PATH, SOURCES_PATH, TRIGGERS_PATH]

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

GEMINI_THINKING_LEVEL = "high"  # "minimal" | "low" | "medium" | "high"

# When a model raises QuotaExhausted (daily free-tier cap hit), the router marks
# it as cooling down for this long and skips it on subsequent calls instead of
# re-attempting a doomed request every time. Kept short so a reset quota (Gemini
# free tier resets daily, ~midnight Pacific) is re-probed promptly — at most one
# wasted probe per model per window.
QUOTA_COOLDOWN_SECS = 1800  # 30 minutes

# ── LLM routing ─────────────────────────────────────────────────────────────────
# Per-task provider chains. Each task maps to an ordered list of
# (provider, model, opts); the router (llm.py) tries each in turn, falling
# through on quota exhaustion / errors. Keep the cheap/free option first.
#
#   clip / moc → two free Gemini models first (each has its own daily free-tier
#                bucket, so stacking them ~doubles free headroom), then DeepSeek
#                V4 Flash via OpenRouter (better than Flash-Lite, no quota cliff)
#                for ~cents. The quota circuit breaker in llm.py makes the
#                gemini→gemini→openrouter hop instant once a bucket is spent.
#   research   → DeepSeek V4 Pro at max reasoning via OpenRouter (frontier-class,
#                ~1/15th Opus cost), falling back to the free Gemini models.
# Reasoning opt is OpenRouter's normalized effort (minimal/low/medium/high/xhigh;
# "xhigh" = max — OpenRouter rejects the literal "max").
GEMINI_FLASH_35 = "gemini-3.5-flash"
GEMINI_FLASH    = "gemini-3-flash-preview"
OR_FLASH        = "deepseek/deepseek-v4-flash"
OR_PRO          = "deepseek/deepseek-v4-pro"

# Explicit output ceiling for report synthesis. Comprehensive reports run to many
# thousands of words; relying on a provider's *default* output cap is how a report
# gets silently truncated mid-sentence. Set it high and deliberate so truncation is
# a ceiling we chose (and can see in logs), not a hidden default. Passed as
# `max_tokens` (OpenRouter) / `max_output_tokens` (Gemini) via the routing opts.
RESEARCH_MAX_OUTPUT_TOKENS = 32000

ROUTING = {
    "clip": [
        ("gemini",     GEMINI_FLASH_35, {}),
        ("gemini",     GEMINI_FLASH,    {}),
        ("openrouter", OR_FLASH,        {}),
    ],
    "moc": [
        ("gemini",     GEMINI_FLASH_35, {}),
        ("gemini",     GEMINI_FLASH,    {}),
        ("openrouter", OR_FLASH,        {}),
    ],
    # Discovery tool loop only — it gathers sources that become Gemini-processed
    # clippings, so a Gemini fallback here is acceptable when OpenRouter is down.
    "research": [
        ("openrouter", OR_PRO,          {"reasoning_effort": "xhigh"}),
        ("gemini",     GEMINI_FLASH_35, {}),
        ("gemini",     GEMINI_FLASH,    {}),
    ],
    # The report itself — draft, critique, revise, citation repair. OpenRouter
    # ONLY: research reports must always be written and reviewed by DeepSeek V4
    # Pro, never Gemini. If OpenRouter is unavailable the synthesis call raises,
    # the run fails, and the trigger stays pending to retry later — which is far
    # better than committing a lower-tier report as if it were the real thing.
    "synthesis": [
        ("openrouter", OR_PRO, {"reasoning_effort": "xhigh",
                                "max_tokens": RESEARCH_MAX_OUTPUT_TOKENS}),
    ],
}

# ── Academic search endpoints (no API key required) ─────────────────────────────
ARXIV_API_URL    = "http://export.arxiv.org/api/query"
OPENALEX_API_URL = "https://api.openalex.org/works"
TAVILY_API_URL   = "https://api.tavily.com/search"

# ── Concept settings ────────────────────────────────────────────────────────────
# Upper bound on how many concepts the conceptualizer pass extracts from a single
# research report — a cap on over-triggering, not a target. Each new concept fires
# its own paid discovery+synthesis run, so keep this modest.
MAX_CONCEPTS_PER_REPORT = 8

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
