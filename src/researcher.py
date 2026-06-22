"""
researcher.py — Agentic research pipeline.

Triggered when the watchdog detects a note in _triggers/ with research: true,
or when a "> [!research] question" callout is found in any note.

Three phases:
  ① Find relevant existing clippings (Gemini-judged over the MOC catalog)
  ② Discovery — agentic tool loop (arXiv / OpenAlex / web search) that queues sources
  ③ Process each queued source through the clipper pipeline → indexed clipping
  ④ Synthesise a long report (draft, or draft→critique→revise for comprehensive)
"""

import re
from pathlib import Path

from config import (
    RESEARCH_PATH, INBOX_PATH,
    CLIP_CONTENT_LIMIT, PAPER_CONTENT_LIMIT, SYNTHESIS_RAW_EXCERPT,
    load_prompt,
)
from notes import read_note, write_note, safe_filename, today
from llm import llm_simple, llm_tool_loop
from web_tools import TOOL_SCHEMA, execute_tool, reset_queue, get_queue
from academic import extract_paper_text
from clipper import process_clipped_note, find_existing_source
from indexer import index_note, get_prior_context, find_relevant_clippings


# ── Phase 2 helpers — discovery prompt ──────────────────────────────────────────

def _build_discovery_prompt(topic: str, depth: str, seed_urls: list[str],
                            existing: list[dict], context: str = "") -> str:
    parts = [f"Research this topic:\n\n**{topic}**", f"Depth: {depth}"]

    if context:
        parts.append(
            "## The note this question appears in (for context)\n"
            "The question above was written as an inline callout inside this note. "
            "Use it to understand what the user is really asking and to guide your "
            "searches; resolve any references in the question against it.\n\n"
            + context[:CLIP_CONTENT_LIMIT]
        )

    if existing:
        known = "\n".join(f"- [[{c['title']}]]" for c in existing)
        parts.append(
            "## Already in the knowledge base (do not re-discover these):\n" + known
        )

    if seed_urls:
        urls = "\n".join(f"- {u}" for u in seed_urls)
        parts.append("## Seed URLs to consider:\n" + urls)

    parts.append(
        "Use the search tools to find the best new sources, then queue_source the "
        "ones worth keeping. When done, reply with a brief confirmation."
    )
    return "\n\n".join(parts)


# ── Phase 3 helpers — process a queued source into an indexed clipping ───────────

def _load_existing(path: Path) -> dict | None:
    """Load an already-vaulted note's analysis for reuse in synthesis."""
    try:
        _, body = read_note(path)
    except Exception:
        return None
    analysis = body.split("## Original Content")[0].strip()
    return {"title": path.stem, "analysis": analysis}


def _process_source(entry: dict) -> dict | None:
    """
    Fetch a queued source's full text, write it as a clip, and run the clipper
    analysis pipeline so it becomes an indexed clipping.
    Returns {title, analysis, raw} or None on failure.
    """
    url = entry["url"]
    kind = entry.get("kind", "web")
    title = entry.get("title") or url
    abstract = (entry.get("abstract") or "").strip()

    # Safety-net dedup (queue_source already checked, but iCloud sync can race).
    existing = find_existing_source(url)
    if existing:
        print(f"[research] Source already in vault: {existing.stem}")
        return _load_existing(existing)

    # Retrieve full text
    if kind == "pdf":
        text = extract_paper_text(url, landing_url=url)
    else:
        from web_tools import fetch_url
        text = fetch_url(url)

    # Fall back to an abstract-only clipping when full text can't be retrieved
    # (e.g. paywalled PDFs). The source is still kept and citable, just thinner.
    full_text_ok = bool(text) and not text.startswith("Failed to fetch")
    if not full_text_ok:
        if not abstract:
            print(f"[research] Could not retrieve source and no abstract available: {url}")
            return None
        print(f"[research] Full text unavailable — keeping abstract-only: {title}")
        text = (
            "> [!warning] Abstract only — full text could not be retrieved.\n\n"
            f"## Abstract\n{abstract}"
        )

    # Write the clip stub, then process it through the clipper pipeline
    clip_path = INBOX_PATH / f"{safe_filename(title)}.md"
    if clip_path.exists():
        # name collision with an unrelated note — disambiguate
        clip_path = INBOX_PATH / f"{safe_filename(title)} ({today()}).md"

    write_note(
        clip_path,
        frontmatter={
            "clipped": True,
            "source": url,
            "date": today(),
            "processed": False,
            "source_type": "research_found",
            "full_text": full_text_ok,
        },
        body=text,
    )

    limit = PAPER_CONTENT_LIMIT if (kind == "pdf" and full_text_ok) else CLIP_CONTENT_LIMIT
    final_path = process_clipped_note(clip_path, content_limit=limit)
    if not final_path or not final_path.exists():
        print(f"[research] Clipper processing failed for: {title}")
        return None

    _, body = read_note(final_path)
    analysis = body.split("## Original Content")[0].strip()
    raw = ""
    if "## Original Content" in body:
        raw = body.split("## Original Content", 1)[1].strip()[:SYNTHESIS_RAW_EXCERPT]
    return {"title": final_path.stem, "analysis": analysis, "raw": raw}


# ── Phase 4 helpers — synthesis ──────────────────────────────────────────────────

def _build_source_block(sources: list[dict]) -> tuple[str, set[str]]:
    """Build the source index text for the synthesis prompt and the set of valid titles."""
    lines = []
    valid_titles = set()
    for i, s in enumerate(sources, 1):
        valid_titles.add(s["title"])
        block = f"### {i}. [[{s['title']}]]\n{s.get('analysis', '')}"
        if s.get("raw"):
            block += f"\n\n_Excerpt from source text:_\n{s['raw']}"
        lines.append(block)
    return "\n\n".join(lines), valid_titles


def _synthesise(topic: str, sources: list[dict], depth: str,
                context: str = "", system_name: str = "research_synthesis") -> str:
    """
    Write the report. Comprehensive depth runs draft → critique → revise.

    When `context` is supplied (inline callouts), the host note is included in the
    prompt and `system_name` selects the callout-aware synthesis prompt so the
    model answers the question as it applies to that note.
    """
    if not sources:
        return (
            f"# {topic}\n\nNo sources were found or available to synthesise a report "
            f"for this topic. Try rephrasing the topic or adding seed URLs."
        )

    source_block, valid_titles = _build_source_block(sources)
    index_titles = "\n".join(f"{i}. [[{s['title']}]]" for i, s in enumerate(sources, 1))

    if context:
        base = (
            f"# Question\n{topic}\n\n"
            f"# The note this question appears in (full context — tailor your answer to it)\n"
            f"{context[:CLIP_CONTENT_LIMIT]}\n\n"
            f"# Source index (cite using these EXACT wikilink titles)\n{index_titles}\n\n"
            f"# Sources with analysis\n{source_block}"
        )
    else:
        base = (
            f"# Research topic\n{topic}\n\n"
            f"# Source index (cite using these EXACT wikilink titles)\n{index_titles}\n\n"
            f"# Sources with analysis\n{source_block}"
        )

    synthesis_system = load_prompt(system_name)
    draft = llm_simple(prompt=base, system=synthesis_system, task="research")

    if depth != "comprehensive":
        return draft

    print("[research] Comprehensive depth — running critique + revise pass")
    critique = llm_simple(
        prompt=f"{base}\n\n# Draft report\n{draft}",
        system=load_prompt("research_critique"),
        task="research",
    )
    revised = llm_simple(
        task="research",
        prompt=(
            f"{base}\n\n# Current draft\n{draft}\n\n"
            f"# Reviewer critique to address\n{critique}\n\n"
            "Revise the report to address every point in the critique. Keep what works, "
            "deepen what is thin, add missing material and counterarguments, and keep all "
            "citations to the exact wikilink titles from the source index."
        ),
        system=synthesis_system,
    )
    return revised


def _validate_wikilinks(report: str, valid_titles: set[str]):
    """Log any [[wikilink]] in the report that doesn't match a known source title."""
    for link in re.findall(r"\[\[([^\]|#]+)\]\]", report):
        if link.strip() not in valid_titles:
            print(f"[research] Warning: wikilink [[{link}]] not in source index — may be broken")


# ── Core pipeline ────────────────────────────────────────────────────────────────

def _run_research(topic: str, depth: str, seed_urls: list[str],
                  context: str = "",
                  synthesis_system_name: str = "research_synthesis") -> tuple[str, list[dict]]:
    """
    Run phases ①–④ for a topic. Returns (report_markdown, all_sources).
    Shared by both trigger notes and inline callouts.

    `context` (the host note's full text) and `synthesis_system_name` are set by
    inline callouts so discovery and synthesis are grounded in the document the
    callout lives in.
    """
    reset_queue()

    # ① Relevant existing clippings (Gemini-judged)
    existing = find_relevant_clippings(topic)
    if existing:
        print(f"[research] Found {len(existing)} relevant existing clipping(s).")

    # ② Discovery tool loop
    prompt = _build_discovery_prompt(topic, depth, seed_urls, existing, context=context)
    llm_tool_loop(
        prompt=prompt,
        system=load_prompt("research_system"),
        tool_schema=TOOL_SCHEMA,
        tool_executor=execute_tool,
        task="research",
    )

    # ③ Process queued sources into indexed clippings
    newly: list[dict] = []
    for entry in get_queue():
        result = _process_source(entry)
        if result:
            newly.append(result)
    print(f"[research] Processed {len(newly)} new source(s) into the knowledge base.")

    # ④ Synthesis
    all_sources = existing + newly
    report = _synthesise(topic, all_sources, depth,
                         context=context, system_name=synthesis_system_name)
    _, valid_titles = _build_source_block(all_sources)
    _validate_wikilinks(report, valid_titles)

    return report, all_sources


def process_research_trigger(path: Path):
    """
    Full research pipeline for a _triggers/ note. Writes a report to Research/.
    Skips silently if not a valid unprocessed research trigger.
    """
    try:
        fm, _ = read_note(path)
    except Exception as e:
        print(f"[research] Could not read {path.name}: {e}")
        return

    if fm.get("research") is not True:
        return

    topic = fm.get("topic", path.stem.replace("research - ", "").strip())
    depth = fm.get("depth", "standard")
    seed_urls = fm.get("urls", []) or []
    output_name = fm.get("output", f"Research - {topic}.md")

    print(f"[research] Starting: '{topic}' (depth: {depth})")

    report, _ = _run_research(topic, depth, seed_urls)

    # Write the research note
    output_path = RESEARCH_PATH / output_name
    write_note(
        output_path,
        frontmatter={
            "generated": True,
            "topic": topic,
            "depth": depth,
            "date": today(),
            "tags": [w.lower() for w in topic.split()[:4]],
        },
        body=report,
    )
    print(f"[research] Written: {output_name}")

    # Index the research note itself
    index_note(
        note_title=output_name.replace(".md", ""),
        note_path=output_path,
        summary=report[:300],
        tags=[w.lower() for w in topic.split()[:4]],
    )

    # Mark trigger as done so watchdog doesn't reprocess
    fm["research"] = "done"
    fm["completed"] = today()
    write_note(path, fm, f"Research completed. See [[{output_name.replace('.md', '')}]].")
    print(f"[research] Done: '{topic}'")


# ── Inline research callouts ─────────────────────────────────────────────────────

_CALLOUT_RE = re.compile(r"^>\s*\[!research\]\s*(.+)$", re.IGNORECASE | re.MULTILINE)

# Folders skipped when scanning the vault for callouts: the trigger pipeline owns
# _triggers/, and these are config/agent dirs a user wouldn't annotate.
_CALLOUT_SKIP_DIRS = {"_triggers", ".obsidian", ".trash"}


def find_research_callouts(folders: list[Path]) -> list[tuple[Path, str]]:
    """
    Scan note folders **recursively** for active '> [!research] question' callouts.

    Callouts are meant to be written into whatever note you're working in, anywhere
    in the vault — the findings are appended in place (see process_research_callout),
    like a review comment. The watchdog therefore passes the vault root so any note
    can host a callout, not just the agent-managed folders.
    """
    results = []
    seen = set()
    for folder in folders:
        if not folder.exists():
            continue
        for path in folder.rglob("*.md"):
            if path in seen or any(part in _CALLOUT_SKIP_DIRS for part in path.parts):
                continue
            seen.add(path)
            try:
                _, body = read_note(path)
            except Exception:
                continue
            for m in _CALLOUT_RE.finditer(body):
                results.append((path, m.group(1).strip()))
    return results


def process_research_callout(path: Path, topic: str):
    """
    Run research for a '> [!research]' callout and append the findings inline,
    replacing the callout in the same note.
    """
    try:
        fm, body = read_note(path)
    except Exception as e:
        print(f"[research] Could not read callout note {path.name}: {e}")
        return

    callout_line = f"> [!research] {topic}"
    in_progress = f"> [!info] Researching: {topic}…"

    # Capture the rest of the note (with the callout line removed) as document
    # context, so research is grounded in whatever the user is writing about.
    context = _CALLOUT_RE.sub("", body, count=1).strip()

    # Mark in-progress immediately so the next rescan won't double-process.
    if callout_line.lower() not in body.lower():
        # Fall back to a regex replace of the first matching callout line
        body = _CALLOUT_RE.sub(in_progress, body, count=1)
    else:
        body = re.sub(re.escape(callout_line), in_progress, body, count=1, flags=re.IGNORECASE)
    write_note(path, fm, body)

    print(f"[research] Callout: '{topic}' in {path.name}")
    report, _ = _run_research(
        topic, "standard", [],
        context=context,
        synthesis_system_name="research_callout",
    )

    inline_block = (
        f"> [!done] Researched: {topic}\n\n"
        f"---\n\n### {topic}\n*{today()}*\n\n{report}\n\n---\n"
    )

    fm2, body2 = read_note(path)
    body2 = body2.replace(in_progress, inline_block, 1)
    write_note(path, fm2, body2)
    print(f"[research] Callout complete: '{topic}' in {path.name}")
