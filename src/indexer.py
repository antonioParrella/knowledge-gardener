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
from notes import read_note, write_note, today
from llm import llm_simple, parse_json_response
from config import INDEX_PATH, INBOX_PATH, SOURCES_PATH, load_prompt

# Matches a MOC note entry: "- [[Title]] — summary" (em-dash or hyphen separator).
_MOC_ENTRY_RE = re.compile(r"-\s*\[\[([^\]|#]+)\]\]\s*(?:[—-]\s*(.*))?$")


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


def assign_to_moc(note_title: str, summary: str, tags: list[str]) -> str:
    """
    Ask Gemini which MOC this note belongs to.
    Returns the MOC topic name (e.g. 'AI', 'Programming').
    """
    existing = get_existing_mocs()
    response = llm_simple(
        task="moc",
        prompt=load_prompt(
            "moc_assign",
            note_title=note_title,
            summary=summary[:400],
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
    return _titlecase_topic(topic)


def update_moc(moc_topic: str, note_title: str, note_path: Path, summary: str):
    """
    Add a note entry to the appropriate MOC.
    Creates the MOC if it doesn't exist yet.
    Also updates the master _index.md.
    """
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
        entry = f"- {link} — {summary}\n"

        if "## Notes\n" in body:
            body = body.replace("## Notes\n", f"## Notes\n{entry}")
        else:
            body += f"\n{entry}"

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


def index_note(note_title: str, note_path: Path, summary: str, tags: list[str]):
    """
    Full indexing pipeline for a single note:
    1. Ask Gemini which MOC it belongs to
    2. Update that MOC
    3. Update _index.md
    """
    moc_topic = assign_to_moc(note_title, summary, tags)
    update_moc(moc_topic, note_title, note_path, summary)


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


def _read_moc_catalog() -> list[tuple[str, str]]:
    """
    Read every MOC and return the full (title, summary) catalog of indexed notes.
    The MOCs already hold a one-line summary per note, so this is cheap.
    """
    catalog: list[tuple[str, str]] = []
    seen = set()
    for moc in INDEX_PATH.glob("MOC - *.md"):
        try:
            _, body = read_note(moc)
        except Exception:
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


def _locate_note(title: str) -> Path | None:
    """Find the note file for a given title in Clippings/ or Sources/."""
    for folder in (INBOX_PATH, SOURCES_PATH):
        candidate = folder / f"{title}.md"
        if candidate.exists():
            return candidate
    return None


def find_relevant_clippings(topic: str) -> list[dict]:
    """
    Ask Gemini which existing notes are relevant to a research topic, using the
    MOC catalog (title + one-line summary) as the candidate list — no keyword
    prefilter. Returns [{title, analysis}] with the full analysis loaded for each
    selected note (the body before the "## Original Content" section).
    """
    catalog = _read_moc_catalog()
    if not catalog:
        return []

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
