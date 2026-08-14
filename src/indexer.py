"""
indexer.py — Manages the Obsidian knowledge base.

Responsible for:
  - Assigning notes to MOCs (Maps of Content)
  - Creating new MOCs when needed
  - Updating existing MOCs with new entries
  - Maintaining the master _index.md
"""

import re
import time
from pathlib import Path
from notes import read_note, write_note, today, normalize_tag, normalize_tags
from llm import llm_simple, parse_json_response
from config import (
    INDEX_PATH, INBOX_PATH, SOURCES_PATH, RESEARCH_PATH, CONCEPTS_PATH,
    RESEARCH_CONTEXT_EXCERPT, MOC_SHORTLIST_MAX, MOC_CANDIDATE_MAX, load_prompt,
)

# Matches a MOC note entry: "- [[Title]] — summary" (em-dash or hyphen separator).
_MOC_ENTRY_RE = re.compile(r"-\s*\[\[([^\]|#]+)\]\]\s*(?:[—-]\s*(.*))?$")

# A MOC entry is one markdown list item, so its summary must be one line. Callers
# have passed raw report prefixes here before, which dragged headings and whole
# paragraphs into the list and broke the MOC's structure — flatten defensively
# rather than trusting every caller to.
_MOC_SUMMARY_LIMIT = 200


def one_line(text: str, limit: int = _MOC_SUMMARY_LIMIT) -> str:
    """Collapse text to a single plain line suitable for a MOC list entry."""
    if not text:
        return ""
    # Drop markdown headings/quotes/bullets outright — a summary should be prose.
    text = re.sub(r"^\s*(#{1,6}|>|[-*+])\s*", "", text.strip(), flags=re.MULTILINE)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    return text


# ── Tag vocabulary ──────────────────────────────────────────────────────────────
# A single canonical tag list, fed into the tagging prompts so the model reuses an
# existing tag when one means the same thing instead of coining a near-duplicate
# ("ml" vs "machine-learning"). New tags the pipeline coins are appended here.
# Seeded by consolidate_tags.py; hand-editable (one "- tag" per line).
TAGS_VOCAB_PATH = INDEX_PATH / "_tags.md"

_VOCAB_ENTRY_RE = re.compile(r"^\s*-\s*(?:#\s*)?(\S.*?)\s*$")

_VOCAB_HEADER = (
    "# Tag Vocabulary\n\n"
    "Canonical tags used across the vault. The tagging prompts read this list and "
    "reuse an existing tag whenever it means the same thing; new tags the agent "
    "coins are appended below. Edit freely — one `- tag` per line, lowercase-hyphenated.\n\n"
    "## Tags\n"
)


def load_tag_vocabulary() -> list[str]:
    """Return the canonical tag list from Index/_tags.md (empty if it doesn't exist)."""
    if not TAGS_VOCAB_PATH.exists():
        return []
    tags: list[str] = []
    for line in TAGS_VOCAB_PATH.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):  # skip headings
            continue
        m = _VOCAB_ENTRY_RE.match(line)
        if not m:
            continue
        t = normalize_tag(m.group(1))
        if t and t not in tags:
            tags.append(t)
    return tags


def format_tag_vocabulary(limit: int = 400) -> str:
    """Render the vocabulary for prompt injection (file order; capped for token safety)."""
    vocab = load_tag_vocabulary()
    if not vocab:
        return "(no existing tags yet — coin clear, specific, lowercase-hyphenated ones)"
    return ", ".join(vocab[:limit])


def register_tags(tags: list[str]) -> None:
    """
    Append any not-yet-known tags to the vocabulary file (Index/_tags.md), creating
    it if missing. Tags are assumed already normalised. All pipeline work is
    serialised through PIPELINE_LOCK upstream, so no extra locking is needed here.
    """
    known = set(load_tag_vocabulary())
    new = [t for t in normalize_tags(tags) if t not in known]
    if not new:
        return
    TAGS_VOCAB_PATH.parent.mkdir(parents=True, exist_ok=True)
    base = TAGS_VOCAB_PATH.read_text(encoding="utf-8").rstrip() if TAGS_VOCAB_PATH.exists() else _VOCAB_HEADER.rstrip()
    TAGS_VOCAB_PATH.write_text(base + "\n" + "\n".join(f"- {t}" for t in new) + "\n", encoding="utf-8")


# Acronyms that should stay uppercase in MOC names (title() would lowercase them).
_ACRONYMS = {"AI", "ML", "LLM", "RL", "NLP", "RAG", "GPU", "IIT", "TDA", "MOC", "CV", "RNN", "CNN"}


def _titlecase_topic(topic: str) -> str:
    """Title-case a topic name while preserving known acronyms (AI, LLM, ML...)."""
    words = []
    for word in topic.split():
        if word.upper() in _ACRONYMS:
            words.append(word.upper())
        else:
            words.append(word.title())
    return " ".join(words)


# Punctuation that legitimately appears in a topic name; everything else is
# stripped. A whitelist (not a blocklist) because the moc-assignment model leaks
# assorted stray tokens — '</S>', a lone '<', a trailing '}' — and a blocklist of
# just filename-illegal chars misses the legal-but-junk ones like '}' (which don't
# crash the write but spawn a near-duplicate MOC, e.g. 'Schizophrenia}').
_TOPIC_ALLOWED = r"\w\s&(),.'-"


def sanitize_moc_topic(topic: str) -> str:
    """
    Clean a MOC topic so it is safe as a filename and free of model artifacts.

    A MOC topic becomes a filename ("MOC - <topic>.md") and a dedup key, so a
    stray character both risks a WinError 123 write crash (on '<', '/', etc.) and
    spawns a junk near-duplicate MOC beside the real one. Strip any HTML-ish tag
    fragment, then keep only whitelisted topic characters, then tidy whitespace.
    """
    topic = re.sub(r"<[^>]*>", "", topic)                 # <s>…</s> or </s> fragments
    topic = re.sub(f"[^{_TOPIC_ALLOWED}]", "", topic)     # drop everything not whitelisted
    return re.sub(r"\s+", " ", topic).strip(" .-")


def get_existing_mocs() -> str:
    """Return a plain-text summary of all existing MOCs for the agent."""
    mocs = list(INDEX_PATH.glob("MOC - *.md"))
    if not mocs:
        return "No MOCs exist yet."
    lines = []
    for moc in sorted(mocs):
        fm, _ = read_note(moc)
        lines.append(
            f"- {moc.stem} | topic: {fm.get('topic', '?')} | "
            f"{fm.get('note_count', 0)} notes"
        )
    return "\n".join(lines)


def assign_to_moc(note_title: str, summary: str, tags: list[str], analysis: str = "") -> str:
    """
    Ask Gemini which MOC this note belongs to.
    Returns the MOC topic name (e.g. 'AI', 'Programming').

    `summary` is the one-line index summary; `analysis` is the full detailed
    write-up (when available). The full analysis gives the model far more to
    work with than a single sentence, so the assignment is more accurate.
    """
    existing = get_existing_mocs()
    response = llm_simple(
        task="moc",
        prompt=load_prompt(
            "moc_assign",
            note_title=note_title,
            summary=summary[:400],
            analysis=(analysis or "").strip()[:3000] or "(no detailed analysis available)",
            tags=", ".join(tags),
            existing=existing,
        ),
        system=load_prompt("moc_assign_system"),
    )
    topic = response.strip().strip("'\".,")
    if len(topic) > 40 or "\n" in topic:
        first_line = topic.splitlines()[0].strip().strip("'\".,*-# ")
        words = first_line.split()
        topic = " ".join(words[:3]) if words else "General"
    topic = sanitize_moc_topic(topic) or "General"
    return _titlecase_topic(topic)


def update_moc(moc_topic: str, note_title: str, note_path: Path, summary: str,
               section: str = "Notes"):
    """
    Add a note entry to the appropriate MOC, under the given `section` heading.
    Creates the MOC if it doesn't exist yet.
    Also updates the master _index.md.

    `section` is the H2 the entry is listed under — "Notes" for clippings and
    research reports, "Concepts" for concept explainers — so a topic MOC keeps its
    sources and its concept notes visually separate. A missing section heading is
    created on demand (appended after the existing body).
    """
    # Defensive: never let a caller build a filename with illegal characters,
    # even if the topic didn't come through assign_to_moc.
    moc_topic = sanitize_moc_topic(moc_topic) or "General"
    moc_path = INDEX_PATH / f"MOC - {moc_topic}.md"

    # Load or initialise the MOC
    if moc_path.exists():
        fm, body = read_note(moc_path)
    else:
        fm = {
            "moc": True,
            "topic": moc_topic,
            "note_count": 0,
            "updated": today(),
        }
        body = (
            f"# {moc_topic} — Knowledge Index\n\n"
            f"## Notes\n"
        )
        print(f"[index] Created new MOC: MOC - {moc_topic}")

    # Add entry if not already present
    link = f"[[{note_path.stem}]]"
    if link not in body:
        entry = f"- {link} — {one_line(summary)}\n"
        heading = f"## {section}\n"

        if heading in body:
            body = body.replace(heading, f"{heading}{entry}")
        else:
            # Section doesn't exist yet (e.g. the first concept in a MOC that only
            # had a ## Notes section) — add it at the end of the body.
            body = body.rstrip() + f"\n\n{heading}{entry}"

        fm["note_count"] = int(fm.get("note_count", 0)) + 1

    fm["updated"] = today()
    write_note(moc_path, fm, body)

    # Update master index
    _update_master_index(moc_topic)

    print(f"[index] '{note_title}' -> MOC - {moc_topic} ({fm['note_count']} notes)")


def _update_master_index(moc_topic: str):
    """Add a MOC link to _index.md if not already there."""
    master_path = INDEX_PATH / "_index.md"
    moc_link = f"[[MOC - {moc_topic}]]"

    if master_path.exists():
        text = master_path.read_text(encoding="utf-8")
    else:
        text = "# Knowledge Base\n\nAll topics indexed by the research agent.\n\n## Topics\n"

    if moc_link not in text:
        text += f"- {moc_link}\n"
        master_path.write_text(text, encoding="utf-8")


def index_note(note_title: str, note_path: Path, summary: str, tags: list[str],
               analysis: str = "", section: str = "Notes"):
    """
    Full indexing pipeline for a single note:
    1. Ask Gemini which MOC it belongs to (using the full analysis when available)
    2. Update that MOC (the one-line `summary` is what gets written to the MOC entry)
    3. Update _index.md
    4. Register the note's tags in the canonical vocabulary

    `section` selects the MOC subsection the entry is listed under ("Notes" by
    default, "Concepts" for concept explainers).

    Callers pass already-normalised tags (frontmatter and the vocabulary must
    agree); this is the single choke point every tagged note flows through, so
    vocabulary registration lives here rather than in each pipeline.
    """
    moc_topic = assign_to_moc(note_title, summary, tags, analysis=analysis)
    update_moc(moc_topic, note_title, note_path, summary, section=section)
    register_tags(tags)


def get_prior_context(topic: str) -> str:
    """
    Search existing MOCs for content relevant to a research topic.
    Returns up to 2000 chars of prior context for the research prompt.
    """
    mocs = list(INDEX_PATH.glob("MOC - *.md"))
    matches = []
    topic_words = set(topic.lower().split())

    for moc in mocs:
        fm, body = read_note(moc)
        moc_topic = fm.get("topic", "").lower()
        # Match if topic words appear in MOC topic or body
        if topic_words & set(moc_topic.split()) or any(w in body.lower() for w in topic_words):
            matches.append(f"### {moc.stem}\n{body[:800]}")

    if not matches:
        return ""

    return "## Relevant content from your knowledge base:\n\n" + "\n\n".join(matches[:3])


def _moc_topic(path: Path, fm: dict) -> str:
    """A MOC's topic name, from its frontmatter or (failing that) its filename."""
    topic = (fm.get("topic") or "").strip()
    return topic or path.stem.removeprefix("MOC - ").strip()


def _moc_topics() -> list[tuple[str, int]]:
    """(topic, note_count) for every MOC — the entry points a lookup starts from."""
    topics: list[tuple[str, int]] = []
    for moc in sorted(INDEX_PATH.glob("MOC - *.md")):
        try:
            fm, _ = read_note(moc)
        except Exception:
            continue
        count = fm.get("note_count")
        topics.append((_moc_topic(moc, fm), count if isinstance(count, int) else 0))
    return topics


def _read_moc_catalog(only: set[str] | None = None) -> list[tuple[str, str]]:
    """
    Read MOCs and return the (title, summary) catalog of the notes they index.
    The MOCs already hold a one-line summary per note, so this is cheap.

    `only` restricts the read to those MOC topics — the second half of the shortlist
    traversal. None reads every MOC, which is what the whole-vault callers still want.
    """
    catalog: list[tuple[str, str]] = []
    seen = set()
    for moc in sorted(INDEX_PATH.glob("MOC - *.md")):
        try:
            fm, body = read_note(moc)
        except Exception:
            continue
        if only is not None and _moc_topic(moc, fm) not in only:
            continue
        for line in body.splitlines():
            m = _MOC_ENTRY_RE.match(line.strip())
            if not m:
                continue
            title = m.group(1).strip()
            summary = (m.group(2) or "").strip()
            if title and title not in seen:
                seen.add(title)
                catalog.append((title, summary))
    return catalog


def _shortlist_mocs(topic: str) -> set[str]:
    """
    Pick the MOCs worth opening for a research topic, from their names alone.

    This is the cheap half of the traversal: ~75 lines of "topic (n notes)" rather
    than every note in the vault. MOCs are deliberately narrow sub-fields, so a name
    is enough to decide whether a topic could live inside one.

    The model leads, because only it judges meaning — "Time-Restricted Eating"
    belongs under Metabolic Health, a name it shares no word with. A deterministic
    word-overlap pass stands behind it as a FALLBACK, used only when the model
    returned nothing usable (empty response, spent quota, provider down), so a bad
    model day costs precision rather than all prior knowledge.

    Deliberately a fallback and not a union: unioned, "Time-Restricted Eating" pulls
    in `MOC - Time Series` on the word "time", and every such false positive spends
    candidate slots on notes that cannot be relevant.
    """
    mocs = _moc_topics()
    if not mocs:
        return set()

    words = {w for w in re.findall(r"[a-z0-9]+", topic.lower()) if len(w) > 3}
    lexical = {t for t, _ in mocs
               if {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 3} & words}

    listing = "\n".join(f"- {t} ({n} notes)" for t, n in mocs)
    try:
        response = llm_simple(
            task="moc",
            prompt=(
                f"Research topic: {topic}\n\n"
                f"Topic indexes (MOCs) in the knowledge base:\n{listing}\n\n"
                f"Return a JSON array of the EXACT index names whose notes could be "
                f"relevant to this topic — the ones worth opening. Prefer few: at most "
                f"{MOC_SHORTLIST_MAX}. Use the names verbatim. Return [] if none fit. "
                f"No prose, JSON array only."
            ),
            system=(
                "You route a research topic to the relevant topic indexes of a personal "
                "knowledge base. Each index is a narrow sub-field. Judge by meaning, not "
                "by shared words: a topic often belongs to an index whose name it does "
                "not contain."
            ),
        )
        picked = parse_json_response(response)
    except Exception as e:
        print(f"[index] MOC shortlist failed ({e}) — falling back to name matching")
        picked = None

    valid = {t for t, _ in mocs}
    chosen = [] if not isinstance(picked, list) else [
        t for t in picked if isinstance(t, str) and t in valid
    ]
    if chosen:
        return set(chosen[:MOC_SHORTLIST_MAX])
    if lexical:
        print(f"[index] MOC shortlist empty for '{topic}' — falling back to "
              f"{len(lexical)} name match(es)")
        return set(sorted(lexical)[:MOC_SHORTLIST_MAX])
    print(f"[index] No MOC matched '{topic}' — no prior knowledge to draw on")
    return set()


def _locate_note(title: str) -> Path | None:
    """Find the note file for a given title in Clippings/ or Sources/."""
    for folder in (INBOX_PATH, SOURCES_PATH):
        candidate = folder / f"{title}.md"
        if candidate.exists():
            return candidate
    return None


def find_relevant_clippings(topic: str) -> list[dict]:
    """
    Find the existing notes relevant to a research topic by walking the MOC graph:
    shortlist the topic indexes worth opening, then pick notes from inside those.
    Returns [{title, analysis}] with the full analysis loaded for each selected note
    (the body before the "## Original Content" section).

    Two small calls rather than one huge one. Handing the model every note in the
    vault made this the largest prompt in the pipeline (~34k tokens at 900 notes, and
    growing without bound) on the tier least able to carry it; see config
    § MOC_SHORTLIST_MAX for the run where it silently returned nothing at all.

    Prior research reports (notes in Research/) are excluded from the candidate
    list — they're handled as related work by find_relevant_research(), not as
    primary sources — so the model never spends a pick on one here. Concept notes
    (Concepts/) are excluded too: they're textbook explainers cross-listed into
    MOCs, not primary sources, and _locate_note() couldn't find them anyway.
    """
    shortlist = _shortlist_mocs(topic)
    if not shortlist:
        return []

    excluded = {p.stem for p in RESEARCH_PATH.glob("*.md")}
    excluded |= {p.stem for p in CONCEPTS_PATH.glob("*.md")}
    catalog = [(t, s) for t, s in _read_moc_catalog(only=shortlist) if t not in excluded]
    if not catalog:
        return []
    catalog = catalog[:MOC_CANDIDATE_MAX]
    print(f"[index] Prior knowledge: {len(shortlist)} MOC(s) -> "
          f"{len(catalog)} candidate note(s) for '{topic}'")

    catalog_text = "\n".join(f"- {title} — {summary}" for title, summary in catalog)
    response = llm_simple(
        task="moc",
        prompt=(
            f"Research topic: {topic}\n\n"
            f"Existing notes in the knowledge base:\n{catalog_text}\n\n"
            "Return a JSON array of the EXACT note titles that are genuinely relevant "
            "to researching this topic. Use the titles verbatim. Include only real "
            "matches — return [] if none are relevant. No prose, JSON array only."
        ),
        system=(
            "You select relevant prior notes from a personal knowledge base. "
            "Be precise: only include notes that materially relate to the topic."
        ),
    )

    titles = parse_json_response(response)
    if not isinstance(titles, list):
        # An unparseable answer here used to look exactly like "nothing is relevant",
        # so a model that returned an empty response cost the run its prior knowledge
        # in total silence. Say so — the run continues either way.
        print(f"[index] Prior-knowledge pick returned no usable JSON "
              f"({len(response or '')} chars) — continuing without prior clippings")
        return []

    valid = {t for t, _ in catalog}
    results = []
    for title in titles:
        if not isinstance(title, str) or title not in valid:
            continue
        note_path = _locate_note(title)
        if not note_path:
            continue
        try:
            _, body = read_note(note_path)
        except Exception:
            continue
        analysis = body.split("## Original Content")[0].strip()
        results.append({"title": title, "analysis": analysis})
    return results


def _research_catalog() -> list[tuple[str, str]]:
    """
    (title, one-line summary) for every prior research report in Research/.
    The summary is the curated MOC entry when the report has been indexed,
    otherwise the report's frontmatter `topic`.
    """
    moc_summaries = dict(_read_moc_catalog())
    catalog: list[tuple[str, str]] = []
    for note in sorted(RESEARCH_PATH.glob("*.md")):
        title = note.stem
        summary = moc_summaries.get(title, "")
        if not summary:
            try:
                fm, _ = read_note(note)
                summary = (fm.get("topic") or "").strip()
            except Exception:
                summary = ""
        catalog.append((title, summary))
    return catalog


def find_relevant_research(topic: str, exclude_title: str | None = None) -> list[dict]:
    """
    Ask the model which *prior research reports* (notes in Research/) are related
    to a new research topic, judging over their titles + one-line summaries.

    Prior research is related work the agent has already written — NOT a primary
    source. Returns [{title, summary, context}] where `context` is an excerpt of
    the prior report, used to ground discovery/synthesis so the new report builds
    on and cross-links to prior work instead of duplicating it. `exclude_title`
    drops the report currently being (re)written so it never references itself.
    """
    catalog = [(t, s) for t, s in _research_catalog() if t != exclude_title]
    if not catalog:
        return []

    catalog_text = "\n".join(f"- {t} — {s}" if s else f"- {t}" for t, s in catalog)
    response = llm_simple(
        task="moc",
        prompt=(
            f"New research topic: {topic}\n\n"
            f"Prior research reports already in the knowledge base:\n{catalog_text}\n\n"
            "Return a JSON array of the EXACT report titles that are genuinely related "
            "to the new topic — reports whose findings give useful context, or that the "
            "new report should build on or cross-reference to avoid duplicating work. Use "
            "the titles verbatim. Return [] if none are related. No prose, JSON array only."
        ),
        system=(
            "You select related prior research reports from a personal knowledge base "
            "so a new report can build on them instead of repeating work already done. "
            "Be precise: only include reports that materially relate to the new topic."
        ),
    )

    titles = parse_json_response(response)
    if not isinstance(titles, list):
        return []

    summaries = dict(catalog)
    valid = set(summaries)
    results = []
    for title in titles:
        if not isinstance(title, str) or title not in valid:
            continue
        note_path = RESEARCH_PATH / f"{title}.md"
        if not note_path.exists():
            continue
        try:
            _, body = read_note(note_path)
        except Exception:
            continue
        results.append({
            "title": title,
            "summary": summaries.get(title, ""),
            "context": body.strip()[:RESEARCH_CONTEXT_EXCERPT],
        })
    return results
