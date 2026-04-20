"""
config.py — Central configuration for the Obsidian Auto-Research System.
Edit VAULT_PATH to point at your iCloud vault before running.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Vault paths ───────────────────────────────────────────────────────────────
# Update this to your actual iCloud vault location
VAULT_PATH   = Path(r"C:\Users\parre\iCloudDrive\iCloud~md~obsidian\Knowledge Garden")

INBOX_PATH    = VAULT_PATH / "Clippings"       # Web Clipper saves here
RESEARCH_PATH = VAULT_PATH / "Research"   # Agent research summaries
INDEX_PATH    = VAULT_PATH / "Index"       # MOCs and master index
SOURCES_PATH  = VAULT_PATH / "Sources"    # Agent-saved research sources
TRIGGERS_PATH = VAULT_PATH / "_triggers"  # iPhone research trigger notes

ALL_PATHS = [INBOX_PATH, RESEARCH_PATH, INDEX_PATH, SOURCES_PATH, TRIGGERS_PATH]

# ── API ───────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Models to try in order if rate limits are hit
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite",
]

# ── Research settings ─────────────────────────────────────────────────────────
MAX_SEARCH_ITERATIONS  = 15    # max tool call loops per research run
MAX_SOURCES_PER_RUN    = 5     # max sources agent can save per research run
FETCH_CONTENT_LIMIT    = 8000  # max chars from a fetched URL
CLIP_CONTENT_LIMIT     = 12000 # max chars from a clipped note
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
