"""
web_tools.py — Search, fetch, and source-queueing tools for the research agent.

Tools available to the Gemini agent:
  search_arxiv / search_openalex — academic paper search
  search_web                     — Tavily general web search
  fetch_url                      — fetch and clean a web page's text
  queue_source                   — mark a source for full processing into the vault
"""

import re
import requests

from config import (
    FETCH_CONTENT_LIMIT,
    TAVILY_API_KEY, TAVILY_API_URL,
)
from academic import search_arxiv, search_openalex


# ── Source queue ────────────────────────────────────────────────────────────────
# The discovery agent calls queue_source to mark sources worth processing; the
# research pipeline then fetches + analyses each one. Tracked per run.
_queued_sources: list[dict] = []


def reset_queue():
    """Call this at the start of each research run."""
    global _queued_sources
    _queued_sources = []


# Backwards-compatible alias (older callers used this name).
reset_source_counter = reset_queue


def get_queue() -> list[dict]:
    """Return the sources queued during the current run."""
    return list(_queued_sources)


def queue_source(url: str, title: str, kind: str, reason: str, abstract: str = "") -> str:
    """
    Agent calls this to mark a source for full processing into the knowledge base.
    kind is "pdf" (academic paper) or "web" (general web page).
    abstract is the source's abstract/snippet — kept as a fallback so the source
    survives as an abstract-only clipping if its full text can't be retrieved.
    Deduplicated against the existing vault and the current queue; no count limit.
    """
    from clipper import find_existing_source  # lazy: clipper imports web tools indirectly

    existing = find_existing_source(url)
    if existing:
        return f"Already in vault as '{existing.stem}' — no need to queue."

    if any(s["url"] == url for s in _queued_sources):
        return f"Already queued: {title}"

    _queued_sources.append({
        "url": url, "title": title, "kind": kind, "reason": reason,
        "abstract": abstract,
    })
    return f"Queued '{title}' ({kind}) for processing."


# ── Tool implementations ──────────────────────────────────────────────────────

def search_web(query: str) -> str:
    """
    Search the web via the Tavily API. Returns ranked results with snippets.
    Falls back to a clear message if no API key is configured.
    """
    if not TAVILY_API_KEY:
        return (
            "Web search is not configured (no TAVILY_API_KEY). "
            "Use search_arxiv and search_openalex for academic sources instead."
        )
    try:
        resp = requests.post(
            TAVILY_API_URL,
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "advanced",
                "max_results": 6,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Web search failed: {e}"

    results = []
    if data.get("answer"):
        results.append(f"Summary: {data['answer']}")
    for r in data.get("results", []):
        snippet = (r.get("content", "") or "")[:400]
        results.append(f"- {r.get('title', '')}\n  {r.get('url', '')}\n  {snippet}")

    return "\n\n".join(results) if results else f"No results found for: {query}"


def _format_academic_results(results: list[dict]) -> str:
    """Render academic search results for the agent."""
    if results and "error" in results[0]:
        return results[0]["error"]
    if not results:
        return "No results found."
    lines = []
    for r in results:
        authors = ", ".join(r.get("authors", [])[:4])
        pdf = r.get("pdf_url") or "(no open PDF)"
        lines.append(
            f"- **{r.get('title', '')}** ({r.get('source', '')})\n"
            f"  Authors: {authors}\n"
            f"  Landing: {r.get('landing_url', '')}\n"
            f"  PDF: {pdf}\n"
            f"  Abstract: {(r.get('abstract', '') or '')[:500]}"
        )
    return "\n\n".join(lines)


def search_arxiv_tool(query: str) -> str:
    return _format_academic_results(search_arxiv(query))


def search_openalex_tool(query: str) -> str:
    return _format_academic_results(search_openalex(query))


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

        # Refuse non-HTML / binary bodies. A PDF (or other binary) served at this URL
        # is not web text: decoding its bytes and stripping "tags" yields garbage, not
        # an article — exactly how garbled-PDF clips were created. PDFs are retrieved
        # and extracted by extract_paper_text, not here. Returning the "Failed to
        # fetch" sentinel makes callers fall back cleanly (abstract-only clip).
        ctype = resp.headers.get("Content-Type", "").lower()
        if resp.content[:5] == b"%PDF-" or (ctype and "html" not in ctype and "text" not in ctype):
            return f"Failed to fetch {url}: non-HTML content ({ctype or 'binary'})"

        # Strip HTML
        text = re.sub(r"<script[^>]*>.*?</script>", " ", resp.text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return text[:FETCH_CONTENT_LIMIT]

    except Exception as e:
        return f"Failed to fetch {url}: {e}"


# ── Tool schema for Gemini ────────────────────────────────────────────────────

TOOL_SCHEMA = [
    {
        "name": "search_arxiv",
        "description": (
            "Search arXiv for academic papers. Every result has a downloadable "
            "full-text PDF. Best for STEM topics (CS, physics, math, stats, "
            "quantitative biology/finance). Returns titles, authors, abstracts, and PDF URLs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query. Be specific."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_openalex",
        "description": (
            "Search OpenAlex for academic papers across ALL disciplines. "
            "Returns titles, authors, abstracts, and an open-access PDF URL when available "
            "(not every paper has one). Use for broad scholarly coverage beyond arXiv."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query. Be specific."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_web",
        "description": (
            "Search the general web for context, news, and authoritative non-academic "
            "sources. Use to complement the academic searches, not replace them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query. Be specific."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Fetch and read the full content of a specific web page. "
            "Use to evaluate a web page before deciding whether to queue it. "
            "You do NOT need to fetch academic PDFs — queue_source retrieves their full text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The full URL to fetch."}
            },
            "required": ["url"],
        },
    },
    {
        "name": "queue_source",
        "description": (
            "Queue a valuable source for full processing into the knowledge base. "
            "Its full text is retrieved, analysed, and saved as an indexed clipping. "
            "Queue as many sources as the topic genuinely needs — there is no limit. "
            "Already-vaulted URLs are skipped automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "For papers: the PDF URL (or landing URL if no PDF). "
                        "For web pages: the page URL."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "A clean descriptive title for the source.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["pdf", "web"],
                    "description": "'pdf' for academic papers, 'web' for general web pages.",
                },
                "reason": {
                    "type": "string",
                    "description": "One sentence on the unique value this source provides.",
                },
                "abstract": {
                    "type": "string",
                    "description": (
                        "The source's abstract or a representative snippet. Used as a "
                        "fallback if the full text can't be retrieved (e.g. paywalled). "
                        "Always provide it for papers."
                    ),
                },
            },
            "required": ["url", "title", "kind", "reason"],
        },
    },
]


def execute_tool(name: str, args: dict) -> str:
    """Dispatch a tool call by name. Called by the research loop."""
    if name == "search_arxiv":
        return search_arxiv_tool(args.get("query", ""))
    elif name == "search_openalex":
        return search_openalex_tool(args.get("query", ""))
    elif name == "search_web":
        return search_web(args.get("query", ""))
    elif name == "fetch_url":
        return fetch_url(args.get("url", ""))
    elif name == "queue_source":
        return queue_source(
            args.get("url", ""), args.get("title", ""),
            args.get("kind", "web"), args.get("reason", ""),
            args.get("abstract", ""),
        )
    else:
        return f"Unknown tool: {name}"
