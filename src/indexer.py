"""
indexer.py — Manages the Obsidian knowledge base.

Responsible for:
  - Assigning notes to MOCs (Maps of Content)
  - Creating new MOCs when needed
  - Updating existing MOCs with new entries
  - Maintaining the master _index.md
"""

import time
from pathlib import Path
from notes import read_note, write_note, today
from gemini_client import gemini_simple
from config import INDEX_PATH


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
    response = gemini_simple(
        prompt=(
            f"Note title: {note_title}\n"
            f"Summary: {summary[:400]}\n"
            f"Tags: {', '.join(tags)}\n\n"
            f"Existing MOCs:\n{existing}\n\n"
            "Which MOC should this note go in?\n"
            "Reply with ONLY the topic name — one or two words, title case.\n"
            "Use an existing MOC name if it fits, or suggest a new short name.\n"
            "Examples: AI, Programming, Health, Finance, Productivity"
        ),
        system=(
            "You manage a personal knowledge base. "
            "Assign notes to topic MOCs. Be consistent with existing names."
        )
    )
    return response.strip().strip("'\".,").title()


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
        short_summary = summary[:250].strip().rstrip(".")
        entry = f"- {link} — {short_summary}\n"

        if "## Notes\n" in body:
            body = body.replace("## Notes\n", f"## Notes\n{entry}")
        else:
            body += f"\n{entry}"

        fm["note_count"] = int(fm.get("note_count", 0)) + 1

    fm["updated"] = today()
    write_note(moc_path, fm, body)

    # Update master index
    _update_master_index(moc_topic)

    print(f"[index] '{note_title}' → MOC - {moc_topic} ({fm['note_count']} notes)")


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
