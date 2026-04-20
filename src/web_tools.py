"""
web_tools.py — Web search and fetch tools used by the research agent.

Three tools available to the Gemini agent:
  search_web   — DuckDuckGo search, returns top results
  fetch_url    — Fetches and cleans a URL's text content
  save_source  — Agent calls this when it judges a source worth keeping;
                 saves it to Sources/ and indexes it like a clipped note
"""

import re
import json
import time
import requests
from pathlib import Path

from config import FETCH_CONTENT_LIMIT, SOURCES_PATH, MAX_SOURCES_PER_RUN
from notes import write_note, safe_filename, today, read_note
from gemini_client import gemini_simple, parse_json_response
from indexer import index_note


# Track how many sources have been saved in the current research run
_sources_saved_this_run = 0


def reset_source_counter():
    """Call this at the start of each research run."""
    global _sources_saved_this_run
    _sources_saved_this_run = 0


# ── Tool implementations ──────────────────────────────────────────────────────

def search_web(query: str) -> str:
    """
    Search DuckDuckGo Instant Answer API.
    Returns top results as plain text.
    """
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=10,
        )
        data = resp.json()
        results = []

        # Abstract (top result)
        if data.get("Abstract"):
            results.append(f"**{data['Heading']}**: {data['Abstract']}\n{data.get('AbstractURL', '')}")

        # Related topics
        for r in data.get("RelatedTopics", [])[:6]:
            if isinstance(r, dict) and "Text" in r:
                results.append(f"- {r['Text']}\n  {r.get('FirstURL', '')}")

        if results:
            return "\n\n".join(results)

        # Fallback: try web results field
        for r in data.get("Results", [])[:5]:
            results.append(f"- {r.get('Text', '')}\n  {r.get('FirstURL', '')}")

        return "\n".join(results) if results else f"No results found for: {query}"

    except Exception as e:
        return f"Search failed: {e}"


def fetch_url(url: str) -> str:
    """
    Fetch a URL and return cleaned text content.
    Strips HTML tags and normalises whitespace.
    """
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"},
        )
        resp.raise_for_status()

        # Strip HTML
        text = re.sub(r"<script[^>]*>.*?</script>", " ", resp.text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return text[:FETCH_CONTENT_LIMIT]

    except Exception as e:
        return f"Failed to fetch {url}: {e}"


def save_source(url: str, reason: str) -> str:
    """
    Agent calls this when it judges a source worth keeping permanently.

    Fetches the full page, summarises it with Gemini, saves it to Sources/,
    and indexes it in the knowledge base MOCs — exactly like a clipped note.

    Capped at MAX_SOURCES_PER_RUN per research session to prevent over-saving.
    """
    global _sources_saved_this_run

    if _sources_saved_this_run >= MAX_SOURCES_PER_RUN:
        return (
            f"Source save limit ({MAX_SOURCES_PER_RUN}) reached for this run. "
            f"Skipping: {url}"
        )

    print(f"[source] Agent saving source: {url}")
    print(f"[source] Reason: {reason}")

    # Fetch the page content
    content = fetch_url(url)
    if content.startswith("Failed to fetch"):
        return f"Could not save source — fetch failed: {content}"

    # Summarise with Gemini
    result_text = gemini_simple(
        prompt=(
            f"Source URL: {url}\n"
            f"Agent's reason for saving: {reason}\n\n"
            f"Page content:\n{content[:8000]}\n\n"
            "Return a JSON object with:\n"
            "- title: clean descriptive title for this source\n"
            "- summary: 2-3 sentence summary of the key content\n"
            "- takeaways: list of 3-5 key points\n"
            "- tags: list of 3-6 lowercase single-word tags\n"
            "Return only valid JSON, no markdown fences."
        ),
        system="You are summarising a web source for a personal research knowledge base.",
    )

    data = parse_json_response(result_text)
    if not data:
        # Fallback if JSON parse fails
        data = {
            "title": f"Source - {url[:60]}",
            "summary": reason,
            "takeaways": [],
            "tags": [],
        }

    # Build the note
    title = safe_filename(data.get("title", f"Source {today()}"))
    note_path = SOURCES_PATH / f"{title}.md"

    # Avoid duplicate saves
    if note_path.exists():
        return f"Source already saved: {title}"

    takeaways_md = "\n".join(f"- {t}" for t in data.get("takeaways", []))
    body = (
        f"## Why This Was Saved\n{reason}\n\n"
        f"## Summary\n{data.get('summary', '')}\n\n"
        f"## Key Takeaways\n{takeaways_md}\n\n"
        f"## Original Content\n{content}\n"
    )

    write_note(
        note_path,
        frontmatter={
            "source_type": "agent_saved",
            "source": url,
            "agent_reason": reason,
            "date": today(),
            "tags": data.get("tags", []),
            "processed": True,
        },
        body=body,
    )

    # Index into the knowledge base
    index_note(
        note_title=title,
        note_path=note_path,
        summary=data.get("summary", reason),
        tags=data.get("tags", []),
    )

    _sources_saved_this_run += 1
    print(f"[source] Saved and indexed: {title}")
    return f"Source saved as '{title}' and added to knowledge base."


# ── Tool schema for Gemini ────────────────────────────────────────────────────

TOOL_SCHEMA = [
    {
        "name": "search_web",
        "description": (
            "Search the web for information on a topic. "
            "Use for finding current facts, recent developments, or overview information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Be specific for better results."
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Fetch and read the full content of a specific web page. "
            "Use when you have a URL from search results and need the full article."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch."
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "save_source",
        "description": (
            "Save a high-quality source permanently to the knowledge base. "
            "Use this when a page contains genuinely valuable, non-duplicated information "
            "that would be useful for future research. "
            "Do NOT use for basic or low-quality pages. "
            f"Maximum {MAX_SOURCES_PER_RUN} saves per research session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the source to save."
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "A one-sentence explanation of why this source is worth keeping. "
                        "Be specific about what unique value it provides."
                    )
                },
            },
            "required": ["url", "reason"],
        },
    },
]


def execute_tool(name: str, args: dict) -> str:
    """Dispatch a tool call by name. Called by the research loop."""
    if name == "search_web":
        return search_web(args.get("query", ""))
    elif name == "fetch_url":
        return fetch_url(args.get("url", ""))
    elif name == "save_source":
        return save_source(args.get("url", ""), args.get("reason", ""))
    else:
        return f"Unknown tool: {name}"
