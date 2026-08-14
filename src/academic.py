"""
academic.py — Academic paper discovery and full-text retrieval.

Two search backends, both free and key-less:
  search_arxiv     — arXiv Atom API. Every result has a downloadable full-text PDF.
  search_openalex  — OpenAlex API. All disciplines; full-text PDF when open-access.

Plus PDF retrieval helpers used by the research pipeline to turn a paper into
full text, reusing the vault's existing PDF extraction (pdf_processor).
"""

import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from config import (
    ARXIV_API_URL, OPENALEX_API_URL, FETCH_CONTENT_LIMIT,
    ARXIV_MIN_INTERVAL_SECS, ARXIV_MAX_ATTEMPTS, ARXIV_BACKOFF_SECS,
)

_HEADERS = {"User-Agent": "knowledge-gardener-research/1.0 (mailto:parrella17@gmail.com)"}


# ── arXiv request pacing ────────────────────────────────────────────────────────
# arXiv answers a burst with 429s carrying no Retry-After, and a rate-limited search
# returns [{"error": ...}] to the model instead of raising — so the lane goes dark
# without a single line in the log saying so (see config § ARXIV_MIN_INTERVAL_SECS).
#
# The gate is process-wide and covers searches AND PDF downloads, because they share
# the host's budget: three back-to-back searches followed by a dozen PDF fetches is
# one burst as far as arXiv is concerned, however the code is factored.

_arxiv_lock = threading.Lock()
_arxiv_last = 0.0


def is_arxiv(url: str) -> bool:
    """True for any arxiv.org host (arxiv.org, export.arxiv.org, …)."""
    host = urlparse(url).netloc.lower()
    return host == "arxiv.org" or host.endswith(".arxiv.org")


def pace_arxiv() -> None:
    """
    Block until ARXIV_MIN_INTERVAL_SECS have elapsed since the last arXiv request.

    The lock is held across the sleep on purpose: two threads that computed their
    wait independently would both wake and fire together, which is the burst this
    exists to prevent.
    """
    global _arxiv_last
    with _arxiv_lock:
        wait = ARXIV_MIN_INTERVAL_SECS - (time.monotonic() - _arxiv_last)
        if wait > 0:
            time.sleep(wait)
        _arxiv_last = time.monotonic()


def arxiv_get(url: str, **kwargs) -> requests.Response:
    """
    GET an arXiv URL, paced and retried on rate-limiting.

    Retries only what a retry can fix — a 429 or a transport error. Any other HTTP
    error (404 on a withdrawn paper) raises on the first attempt. Exhausting the
    attempts re-raises, so the caller reports a real failure rather than silently
    treating "we were throttled" as "arXiv has nothing".
    """
    last: Exception | None = None
    for attempt in range(ARXIV_MAX_ATTEMPTS):
        pace_arxiv()
        try:
            resp = requests.get(url, headers=_HEADERS, **kwargs)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp
            last = requests.HTTPError(
                f"429 Too Many Requests (arXiv rate limit) for url: {url}",
                response=resp,
            )
        except requests.HTTPError:
            raise
        except Exception as e:      # connection reset, read timeout, DNS blip
            last = e
        if attempt < ARXIV_MAX_ATTEMPTS - 1:
            time.sleep(ARXIV_BACKOFF_SECS * (2 ** attempt))
    raise last


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
        resp = arxiv_get(
            ARXIV_API_URL,
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": max_results,
                "sortBy": "relevance",
            },
            timeout=20,
        )
    except Exception as e:
        # Loud, because the model only sees the string it gets back: a silent
        # "no results" is indistinguishable from "arXiv has nothing on this".
        print(f"[academic] arXiv search failed after {ARXIV_MAX_ATTEMPTS} attempts: {e}")
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
        # The DOI is surfaced as its own field, not just buried in landing_url: it is
        # the key the OA ladder needs to find a free copy when pdf_url is absent or
        # paywalled, and the discovery agent can only pass on what it is shown.
        doi = (work.get("doi") or "").replace("https://doi.org/", "")
        results.append({
            "title": work.get("title", "") or "",
            "authors": authors,
            "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
            "pdf_url": oa.get("pdf_url") or "",
            "landing_url": work.get("doi") or work.get("id", ""),
            "doi": doi,
            "pmcid": ((work.get("ids") or {}).get("pmcid") or "").rsplit("/", 1)[-1],
            "source": "openalex",
        })
    return results


# ── Full-text retrieval ─────────────────────────────────────────────────────────

def download_pdf(url: str) -> Path | None:
    """
    Download a PDF to a temp file. Returns the path, or None on failure.

    arXiv PDFs go through the same paced/retried path as searches — they draw on the
    same per-host budget, and a research run downloads far more PDFs than it searches.
    """
    try:
        if is_arxiv(url):
            resp = arxiv_get(url, timeout=30)
        else:
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
