"""
fulltext.py — Open-access full-text resolution ladder.

A queued source arrives as one URL. That URL is often the *worst* way to reach the
document: a publisher PDF path that 403s, a bot-walled PMC page, a bare DOI that
redirects to a paywall. Meanwhile the same article is usually sitting in a free,
key-less API keyed by an identifier we can derive from that very URL.

So instead of "download the URL, give up", this module runs a ladder: derive every
identifier it can, then try the retrieval routes in descending order of MEASURED
precision, stopping at the first candidate that passes the identity gate.

Route precision, measured over a random sample of 30 abstract-only clips already in
the vault (see DESIGN_NOTES § Full-text recovery for the harness and the raw table):

    Europe PMC fullTextXML   5/5 good   100%
    NCBI BioC (PMC OA)       5/5 good   100%
    Unpaywall -> OA copy     2/4 good    50%
    OpenAlex locations[]     3/7 good    43%
    title -> identifier      1/4 good    25%   <- gated, never trusted
    reader proxies           2/9 good    22%   <- NOT implemented, see below

The ordering is that table, and it is the whole design. Reader proxies and bare
title resolution are cheap to add and were deliberately left out / hard-gated:
they return a *plausible wrong document* (a 404 page rendered as prose, a different
paper on the same topic), and the clipper's `usable` gate asks "is this content?"
not "is this THE content?" — so a wrong document sails straight through it and ends
up cited in a report. Recall is worth much less here than precision.

── The identity gate ──
Hence `identity_score`: the fraction of the abstract's key terms that appear in the
retrieved text. Same 30-clip sample, scored against the graded outcomes:

    right document, full text   n=20   min 80%   median 95%
    right document, thin/landing n=10   min 20%   median 53%
    WRONG document               n=8    min  0%   median 33%

A 0.75 threshold keeps every genuine full text and rejects 7 of 8 wrong documents.
It is lexical and free — no model call — which is what lets it sit in front of every
route without a cost argument.

The gate is applied to INFERRED routes only. When the PMCID came out of the URL
itself, Europe PMC's full text for that PMCID is the right document by construction,
and gating it would only add false rejections; see `Candidate.trusted`.
"""

import re
import time
from dataclasses import dataclass, field

import requests

from config import (
    FULLTEXT_ENABLED, FULLTEXT_CONTACT_EMAIL, FULLTEXT_IDENTITY_THRESHOLD,
    FULLTEXT_MIN_CHARS, FULLTEXT_TIMEOUT_SECS,
)

_HEADERS = {"User-Agent": f"knowledge-gardener-research/1.0 (mailto:{FULLTEXT_CONTACT_EMAIL})"}

_EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_BIOC_BASE = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi"
_IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"


# ── Identifier extraction ─────────────────────────────────────────────────────
# All four are derived from the URL alone. The pipeline ALSO persists identifiers
# handed over at queue time (web_tools.queue_source), because a search result knows
# the DOI even when the URL it hands us is an opaque publisher PDF path — 55% of the
# vault's abstract-only clips carry no identifier in their URL at all.

_DOI_RE    = re.compile(r"(10\.\d{4,9}/[^\s?#\"<>&]+)")
_PMCID_RE  = re.compile(r"(PMC\d+)", re.I)
_PMID_RE   = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", re.I)
_ARXIV_RE  = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


def extract_identifiers(url: str, extra: dict | None = None) -> dict:
    """
    Derive {doi, pmcid, pmid, arxiv} from a URL, merged with any identifiers the
    caller already knows (`extra` wins — a search result's DOI beats a guess).

    Trailing punctuation is stripped from a DOI: a DOI captured out of a URL query
    string or prose routinely picks up a '.' or ')' that makes every downstream
    lookup 404.
    """
    ids: dict[str, str] = {}

    m = _PMCID_RE.search(url or "")
    if m:
        ids["pmcid"] = m.group(1).upper()
    m = _PMID_RE.search(url or "")
    if m:
        ids["pmid"] = m.group(1)
    m = _ARXIV_RE.search(url or "")
    if m:
        ids["arxiv"] = m.group(1)
    m = _DOI_RE.search(url or "")
    if m:
        ids["doi"] = m.group(1).rstrip(".,);")

    for k, v in (extra or {}).items():
        if v and k in ("doi", "pmcid", "pmid", "arxiv"):
            v = str(v).strip()
            # Callers hand these over in whatever shape the upstream API used:
            # a full https://doi.org/… URL, or "pmcid: PMC123". Normalise to the
            # bare identifier so lookups don't have to re-parse.
            if k == "doi":
                mm = _DOI_RE.search(v)
                v = mm.group(1).rstrip(".,);") if mm else v
            elif k == "pmcid":
                mm = _PMCID_RE.search(v)
                v = mm.group(1).upper() if mm else v
            ids[k] = v

    return ids


# ── Identity gate ─────────────────────────────────────────────────────────────

_STOPWORDS = set("""
the a an and or of for in on to with by from as at is are was were be been being
this that these those we our their its it he she they which such using used
between among after before during into than then also more most other can may
however therefore thus both each some many much very will would could should
""".split())


def _key_terms(text: str, n: int = 30) -> set[str]:
    """The n most frequent content words — a cheap lexical fingerprint."""
    freq: dict[str, int] = {}
    for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{4,}", (text or "").lower()):
        if w in _STOPWORDS:
            continue
        freq[w] = freq.get(w, 0) + 1
    return set(sorted(freq, key=lambda w: freq[w], reverse=True)[:n])


def identity_score(reference: str, candidate: str) -> float | None:
    """
    Fraction of the reference's key terms present in the candidate text.

    `reference` is the source's abstract (preferred) or its title. Returns None when
    the reference is too thin to fingerprint — the caller must then decide, rather
    than being handed a meaningless 0.0 that reads as "rejected".
    """
    terms = _key_terms(reference, 30)
    if len(terms) < 5:
        return None
    body = (candidate or "").lower()
    return sum(1 for t in terms if t in body) / len(terms)


def passes_identity(reference: str, candidate: str,
                    threshold: float = FULLTEXT_IDENTITY_THRESHOLD) -> tuple[bool, str]:
    """
    Is `candidate` plausibly the document `reference` describes?
    Returns (verdict, human-readable reason) — the reason lands in the log so a
    rejection is diagnosable without re-running the ladder.
    """
    score = identity_score(reference, candidate)
    if score is None:
        # No usable fingerprint. Refuse rather than admit: an ungated inferred route
        # is exactly the failure mode this gate exists to prevent.
        return False, "no fingerprint (reference too short)"
    if score < threshold:
        return False, f"identity {score:.0%} < {threshold:.0%}"
    return True, f"identity {score:.0%}"


# ── Candidates ────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    """One retrieval attempt's outcome."""
    text: str = ""
    route: str = ""
    # True when the identifier came from the URL itself, so the route cannot have
    # fetched a different document: the identity gate is skipped. False for anything
    # inferred (an OA location we followed, a title we resolved), which is gated.
    trusted: bool = False


def _looks_like_full_text(text: str) -> bool:
    """Cheap floor: enough text to be a document, not a stub."""
    return bool(text) and len(text) >= FULLTEXT_MIN_CHARS


# The sections every research article has and no landing page does. Matched as
# whole words so "reference" in a nav bar doesn't count for much on its own.
_SECTION_RE = re.compile(
    r"\b(introduction|methods?|methodology|materials and methods|participants|"
    r"results|discussion|conclusions?|references|acknowledg(e)?ments|"
    r"statistical analys[ei]s|limitations)\b", re.I)


def article_structure(text: str) -> int:
    """How many DISTINCT article section names appear. A landing page has ~0-2."""
    return len({m.group(1).lower() for m in _SECTION_RE.finditer(text or "")})


def has_article_structure(text: str) -> bool:
    """
    Structural evidence that this is a full article rather than an abstract page.

    The identity gate alone is not enough for inferred routes. A repository landing
    page carries the abstract verbatim, so it scores ~100% on identity while being
    nothing but the abstract wrapped in institutional chrome ("Skip to main
    navigation … We use cookies …"). Accepting one would *downgrade* the clip we
    already had: a clean abstract-only note replaced by the same abstract plus
    boilerplate. On the graded sample, genuine full texts carried 5-9 distinct
    sections and landing pages 0-2, so three is a wide, safe line.
    """
    return article_structure(text) >= 3


def _strip_xml(xml: str) -> str:
    text = re.sub(r"<[^>]+>", " ", xml)
    return re.sub(r"\s+", " ", text).strip()


def _get(url: str, **kw) -> requests.Response | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=FULLTEXT_TIMEOUT_SECS, **kw)
        return r if r.status_code == 200 else None
    except Exception:
        return None


# ── Routes, in descending order of measured precision ─────────────────────────

def _route_europepmc(ids: dict, **_) -> Candidate | None:
    """Europe PMC full-text XML. 100% precision on the sample; the best route we have."""
    pmcid = ids.get("pmcid")
    if not pmcid:
        return None
    r = _get(f"{_EPMC_BASE}/{pmcid}/fullTextXML")
    if not r or "<" not in r.text[:200]:
        return None
    text = _strip_xml(r.text)
    if not _looks_like_full_text(text):
        return None
    return Candidate(text, f"europepmc:{pmcid}", trusted=True)


def _route_bioc(ids: dict, **_) -> Candidate | None:
    """NCBI BioC — the same PMC OA subset via a different door, for when EPMC 404s."""
    pmcid = ids.get("pmcid")
    if not pmcid:
        return None
    r = _get(f"{_BIOC_BASE}/BioC_json/{pmcid}/unicode")
    if not r:
        return None
    try:
        data = r.json()
    except Exception:
        return None  # HTML error page => not in the OA subset
    parts = []
    for doc in (data[0].get("documents", []) if isinstance(data, list) and data else []):
        for p in doc.get("passages", []):
            if p.get("text"):
                parts.append(p["text"])
    text = "\n".join(parts)
    if not _looks_like_full_text(text):
        return None
    return Candidate(text, f"bioc:{pmcid}", trusted=True)


def _route_arxiv(ids: dict, **_) -> Candidate | None:
    """
    arXiv abs -> pdf normalisation. An `/abs/` URL queued as kind="pdf" downloads as
    HTML, fails the %PDF check, and (because of the landing_url bug this ladder
    replaces) had no fallback at all — despite every arXiv paper being free.
    """
    aid = ids.get("arxiv")
    if not aid:
        return None
    for base in ("https://arxiv.org/pdf/", "https://export.arxiv.org/pdf/"):
        text = _pdf_text(f"{base}{aid}")
        if _looks_like_full_text(text):
            return Candidate(text, f"arxiv:{aid}", trusted=True)
    return None


def _route_unpaywall(ids: dict, **_) -> Candidate | None:
    """Unpaywall: DOI -> a legal OA copy. Inferred, so gated."""
    doi = ids.get("doi")
    if not doi:
        return None
    r = _get(f"https://api.unpaywall.org/v2/{doi}",
             params={"email": FULLTEXT_CONTACT_EMAIL})
    if not r:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not data.get("is_oa"):
        return None
    locations = [data.get("best_oa_location")] + list(data.get("oa_locations") or [])
    for loc in locations:
        if not loc:
            continue
        for key in ("url_for_pdf", "url"):
            target = loc.get(key)
            if not target:
                continue
            text = _fetch_document(target)
            # Structure is checked HERE, not only at the ladder level, so a landing
            # page in the first slot doesn't mask a real PDF in the second — several
            # OA records list the repository page ahead of the file.
            if _looks_like_full_text(text) and has_article_structure(text):
                return Candidate(text, f"unpaywall:{doi}", trusted=False)
    return None


def _route_openalex(ids: dict, **_) -> Candidate | None:
    """OpenAlex locations[]. Often a landing page rather than full text, hence gated."""
    doi = ids.get("doi")
    if not doi:
        return None
    r = _get(f"https://api.openalex.org/works/doi:{doi}",
             params={"mailto": FULLTEXT_CONTACT_EMAIL})
    if not r:
        return None
    try:
        work = r.json()
    except Exception:
        return None
    for loc in (work.get("locations") or []):
        target = loc.get("pdf_url") or loc.get("landing_page_url")
        if not target:
            continue
        text = _fetch_document(target)
        if _looks_like_full_text(text) and has_article_structure(text):
            return Candidate(text, f"openalex:{doi}", trusted=False)
    return None


# Ordered by the measured precision table in the module docstring.
ROUTES = (
    ("europepmc", _route_europepmc),
    ("bioc",      _route_bioc),
    ("arxiv",     _route_arxiv),
    ("unpaywall", _route_unpaywall),
    ("openalex",  _route_openalex),
)


# ── Identifier upgrading ──────────────────────────────────────────────────────

def resolve_pmcid(ids: dict) -> str | None:
    """
    Upgrade a PMID or DOI to a PMCID, which unlocks the two 100%-precision routes.
    Tries NCBI's ID converter, then Europe PMC's index.

    This is an identifier-to-identifier mapping over the SAME work — not a search —
    so a PMCID it returns is as trustworthy as one read off the URL.
    """
    ident = ids.get("pmid") or ids.get("doi")
    if not ident:
        return None

    r = _get(_IDCONV, params={"ids": ident, "format": "json",
                              "tool": "knowledge-gardener",
                              "email": FULLTEXT_CONTACT_EMAIL})
    if r:
        try:
            records = r.json().get("records", [])
            if records and records[0].get("pmcid"):
                return records[0]["pmcid"].upper()
        except Exception:
            pass

    query = f'DOI:"{ids["doi"]}"' if ids.get("doi") else f'EXT_ID:{ids["pmid"]}'
    r = _get(f"{_EPMC_BASE}/search",
             params={"query": query, "format": "json", "resultType": "core", "pageSize": 1})
    if r:
        try:
            results = r.json().get("resultList", {}).get("result", [])
            if results and results[0].get("pmcid"):
                return results[0]["pmcid"].upper()
        except Exception:
            pass
    return None


# ── Document fetching ─────────────────────────────────────────────────────────

def _pdf_text(url: str) -> str:
    """Download and extract a PDF. Returns "" on anything that isn't really a PDF."""
    try:
        # The arXiv route retries the same paper against two hosts, and the ladder
        # runs once per source — pace it against the same process-wide gate the
        # search tools use, or the recovery path becomes its own burst.
        from academic import is_arxiv, pace_arxiv
        if is_arxiv(url):
            pace_arxiv()
    except Exception:
        pass
    try:
        r = requests.get(url, headers=_HEADERS, timeout=FULLTEXT_TIMEOUT_SECS,
                         allow_redirects=True)
        if r.status_code != 200 or not r.content[:5].startswith(b"%PDF"):
            return ""
    except Exception:
        return ""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=r.content, filetype="pdf")
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    except Exception:
        return ""


# A bot wall reads as perfectly good prose to a length check, so it is matched
# explicitly. This is the same judgement the clipper's `usable` gate makes on the
# analysed text; catching it here saves a model call and a discarded stub.
_BOTWALL_RE = re.compile(
    r"(just a moment|enable javascript|are you a robot|verify you are human|"
    r"captcha|unusual traffic|access denied|request could not be processed|"
    r"page not found|404 (file )?not found)", re.I)


def _fetch_document(url: str) -> str:
    """Fetch a URL as either a PDF or an HTML article. Returns "" if neither."""
    if url.lower().endswith(".pdf") or "/pdf" in url.lower():
        text = _pdf_text(url)
        if text:
            return text

    try:
        r = requests.get(url, headers={**_HEADERS, "Accept": "text/html,application/xhtml+xml"},
                         timeout=FULLTEXT_TIMEOUT_SECS, allow_redirects=True)
        if r.status_code != 200:
            return ""
        ctype = r.headers.get("Content-Type", "").lower()
        if r.content[:5] == b"%PDF-":
            return _pdf_text(url)
        if ctype and "html" not in ctype and "text" not in ctype:
            return ""
        text = re.sub(r"<script[^>]*>.*?</script>", " ", r.text, flags=re.DOTALL | re.I)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if _BOTWALL_RE.search(text[:3000]):
            return ""
        return text
    except Exception:
        return ""


# ── The ladder ────────────────────────────────────────────────────────────────

def retrieve(url: str, reference: str = "", identifiers: dict | None = None) -> tuple[str, str]:
    """
    Try every open-access route for `url`, newest-best-first, and return
    (text, route) for the first candidate that passes the identity gate —
    or ("", reason) if none does.

    `reference` is the source's abstract (or, failing that, its title): the
    fingerprint the identity gate matches inferred candidates against.
    `identifiers` are any the caller already knows (a search result's DOI).

    Never raises. A failure here means the caller falls back to its abstract-only
    clip exactly as it did before this ladder existed — the ladder can only add
    recoveries, never take a working path away.
    """
    if not FULLTEXT_ENABLED:
        return "", "disabled"

    ids = extract_identifiers(url, identifiers)

    # A PMCID unlocks the only two 100%-precision routes, so it is worth one extra
    # lookup to try to get one before walking the ladder.
    if not ids.get("pmcid") and (ids.get("pmid") or ids.get("doi")):
        pmcid = resolve_pmcid(ids)
        if pmcid:
            ids["pmcid"] = pmcid

    if not ids:
        return "", "no identifiers"

    tried: list[str] = []
    for name, route in ROUTES:
        try:
            candidate = route(ids)
        except Exception as e:
            tried.append(f"{name}: error {type(e).__name__}")
            continue
        if not candidate:
            tried.append(f"{name}: miss")
            continue

        if candidate.trusted:
            # A PMC full-text document keyed by an identifier we hold: right document
            # by construction, and full text by construction. Nothing left to check.
            print(f"[fulltext] {candidate.route} -> {len(candidate.text):,} chars (trusted)")
            return candidate.text, candidate.route

        # Inferred candidates must clear BOTH gates: the right document (identity)
        # AND actually the full text (structure). A landing page passes the first
        # trivially — it quotes the abstract — and fails the second.
        ok, why = passes_identity(reference, candidate.text)
        if not ok:
            tried.append(f"{name}: rejected ({why})")
            continue
        if not has_article_structure(candidate.text):
            tried.append(f"{name}: rejected (landing page — "
                         f"{article_structure(candidate.text)} sections)")
            continue
        print(f"[fulltext] {candidate.route} -> {len(candidate.text):,} chars ({why})")
        return candidate.text, candidate.route

    return "", "; ".join(tried) or "no routes applicable"
