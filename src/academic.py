"""
academic.py — Academic paper discovery and full-text retrieval.

Two search backends, both free and key-less:
  search_arxiv     — arXiv Atom API. Every result has a downloadable full-text PDF.
  search_openalex  — OpenAlex API. All disciplines; full-text PDF when open-access.

Plus PDF retrieval helpers used by the research pipeline to turn a paper into
full text, reusing the vault's existing PDF extraction (pdf_processor).
"""

import tempfile
from pathlib import Path

import requests

from config import (
    ARXIV_API_URL, OPENALEX_API_URL, FETCH_CONTENT_LIMIT,
)

_HEADERS = {"User-Agent": "knowledge-gardener-research/1.0 (mailto:parrella17@gmail.com)"}


# ── arXiv ───────────────────────────────────────────────────────────────────────

def search_arxiv(query: str, max_results: int = 8) -> list[dict]:
    """
    Search arXiv. Returns a list of dicts:
      {title, authors, abstract, pdf_url, landing_url, source}
    pdf_url is always present and freely downloadable.
    """
    try:
        import feedparser
    except ImportError:
        return [{"error": "feedparser not installed — run: pip install feedparser"}]

    try:
        resp = requests.get(
            ARXIV_API_URL,
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": max_results,
                "sortBy": "relevance",
            },
            headers=_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
    except Exception as e:
        return [{"error": f"arXiv search failed: {e}"}]

    feed = feedparser.parse(resp.text)
    results = []
    for entry in feed.entries:
        pdf_url = ""
        for link in entry.get("links", []):
            if link.get("type") == "application/pdf" or link.get("title") == "pdf":
                pdf_url = link.get("href", "")
                break
        if not pdf_url and entry.get("id"):
            # arXiv id pages map cleanly to /pdf/
            pdf_url = entry.id.replace("/abs/", "/pdf/")
        results.append({
            "title": entry.get("title", "").strip().replace("\n", " "),
            "authors": [a.get("name", "") for a in entry.get("authors", [])],
            "abstract": entry.get("summary", "").strip().replace("\n", " "),
            "pdf_url": pdf_url,
            "landing_url": entry.get("id", ""),
            "source": "arxiv",
        })
    return results


# ── OpenAlex ──────────────────────────────────────────────────────────────────

def _reconstruct_abstract(inverted_index: dict | None) -> str:
    """Rebuild an abstract from OpenAlex's abstract_inverted_index."""
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort(key=lambda p: p[0])
    return " ".join(word for _, word in positions)


def search_openalex(query: str, max_results: int = 8) -> list[dict]:
    """
    Search OpenAlex. Returns a list of dicts:
      {title, authors, abstract, pdf_url, landing_url, source}
    pdf_url may be "" when the work is not open-access.
    """
    try:
        resp = requests.get(
            OPENALEX_API_URL,
            params={
                "search": query,
                "per-page": max_results,
                "mailto": "parrella17@gmail.com",
            },
            headers=_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return [{"error": f"OpenAlex search failed: {e}"}]

    results = []
    for work in data.get("results", []):
        oa = work.get("best_oa_location") or {}
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in work.get("authorships", [])
        ]
        results.append({
            "title": work.get("title", "") or "",
            "authors": authors,
            "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
            "pdf_url": oa.get("pdf_url") or "",
            "landing_url": work.get("doi") or work.get("id", ""),
            "source": "openalex",
        })
    return results


# ── Full-text retrieval ─────────────────────────────────────────────────────────

def download_pdf(url: str) -> Path | None:
    """Download a PDF to a temp file. Returns the path, or None on failure."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        # Guard against HTML error pages served with a .pdf URL
        ctype = resp.headers.get("Content-Type", "")
        if "pdf" not in ctype.lower() and not resp.content[:5].startswith(b"%PDF"):
            return None
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(resp.content)
        tmp.close()
        return Path(tmp.name)
    except Exception:
        return None


def extract_paper_text(pdf_url: str, landing_url: str = "") -> str:
    """
    Get the full text of a paper. Tries the PDF first (extracted via the vault's
    existing PyMuPDF/pypdf extractor), then falls back to fetching the landing page.
    Returns the full text, or "" if nothing could be retrieved.
    """
    from pdf_processor import extract_pdf_text  # reuse existing extractor

    if pdf_url:
        pdf_path = download_pdf(pdf_url)
        if pdf_path:
            try:
                text = extract_pdf_text(pdf_path)
            finally:
                pdf_path.unlink(missing_ok=True)
            if text.strip():
                return text

    # Fallback: scrape the landing page (lazy import avoids circular dependency).
    # Never re-fetch the PDF itself as a "landing page" — fetching a PDF URL returns
    # raw bytes, not text, which is exactly how garbled-PDF clips were created. Skip
    # when the landing URL is the PDF we already tried, or is itself a PDF.
    if landing_url and landing_url != pdf_url and not landing_url.lower().endswith(".pdf"):
        from web_tools import fetch_url
        text = fetch_url(landing_url)
        if text and not text.startswith("Failed to fetch"):
            return text

    return ""
