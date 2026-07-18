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
from notes import read_note, write_note, safe_filename, today, normalize_tags
from llm import llm_simple, llm_tool_loop, parse_json_response
from web_tools import TOOL_SCHEMA, execute_tool, reset_queue, get_queue
from academic import extract_paper_text
from clipper import process_clipped_note, find_existing_source
from indexer import (
    index_note, get_prior_context, find_relevant_clippings, find_relevant_research,
    format_tag_vocabulary, one_line,
)


# ── Phase 2 helpers — discovery prompt ──────────────────────────────────────────

def _build_discovery_prompt(topic: str, depth: str, seed_urls: list[str],
                            existing: list[dict], context: str = "",
                            context_kind: str = "callout",
                            prior_research: list[dict] | None = None) -> str:
    parts = [f"Research this topic:\n\n**{topic}**", f"Depth: {depth}"]

    if context and context_kind == "brief":
        parts.append(
            "## Research brief — details & acceptance criteria\n"
            "The trigger note that requested this research included the brief below: "
            "extra detail on what the user actually wants, and the acceptance criteria "
            "the finished report must satisfy. Let it steer which sources you look for.\n\n"
            + context[:CLIP_CONTENT_LIMIT]
        )
    elif context:
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

    if prior_research:
        prior = "\n".join(
            f"- [[{p['title']}]] — {p['summary']}" if p.get("summary") else f"- [[{p['title']}]]"
            for p in prior_research
        )
        parts.append(
            "## Prior research reports on related topics (related work, NOT sources to re-fetch):\n"
            "You have already written the reports below. Their findings are given to the "
            "synthesis phase in full. Treat them as prior knowledge: build on what they "
            "already establish and steer your searches toward what is new or missing "
            "relative to them, rather than re-researching ground they already cover.\n\n"
            + prior
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
    """
    Build the source index text for the synthesis prompt and the set of valid titles.

    Deliberately unnumbered: an ordinal next to each title gives the model a
    number to cite instead of the title, and it takes it — a numbered index
    produced a whole report citing [[21]], [[27]] rather than wikilinks.
    """
    lines = []
    valid_titles = set()
    for s in sources:
        valid_titles.add(s["title"])
        block = f"### [[{s['title']}]]\n{s.get('analysis', '')}"
        if s.get("raw"):
            block += f"\n\n_Excerpt from source text:_\n{s['raw']}"
        lines.append(block)
    return "\n\n".join(lines), valid_titles


def _build_prior_research_block(prior_research: list[dict]) -> str:
    """Build the related-work text for the synthesis prompt from prior research reports."""
    lines = []
    for p in prior_research:
        header = f"### [[{p['title']}]]"
        if p.get("summary"):
            header += f" — {p['summary']}"
        lines.append(f"{header}\n{p.get('context', '')}")
    return "\n\n".join(lines)


def _synthesise(topic: str, sources: list[dict], depth: str,
                context: str = "", system_name: str = "research_synthesis",
                context_kind: str = "callout",
                prior_research: list[dict] | None = None) -> str:
    """
    Write the report. Comprehensive depth runs draft → critique → revise.

    When `context` is supplied it is included in the prompt. `context_kind`
    selects the framing: "callout" (the host note an inline [!research] lives in,
    paired with the callout-aware `system_name`) or "brief" (a trigger note's
    details + acceptance criteria the standalone report must satisfy).

    `prior_research` is related work the agent has already written: it is passed
    as a distinct block (not merged into the sources) so the report can build on
    and cross-link to it without treating it as primary evidence.
    """
    prior_research = prior_research or []
    if not sources and not prior_research:
        return (
            f"# {topic}\n\nNo sources were found or available to synthesise a report "
            f"for this topic. Try rephrasing the topic or adding seed URLs."
        )

    source_block, valid_titles = _build_source_block(sources)
    index_titles = "\n".join(f"- [[{s['title']}]]" for s in sources)

    if context and context_kind == "brief":
        base = (
            f"# Research topic\n{topic}\n\n"
            f"# Research brief — details & acceptance criteria the report MUST satisfy\n"
            f"{context[:CLIP_CONTENT_LIMIT]}\n\n"
            f"# Source index (cite using these EXACT wikilink titles)\n{index_titles}\n\n"
            f"# Sources with analysis\n{source_block}"
        )
    elif context:
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

    prior_block = _build_prior_research_block(prior_research)
    if prior_block:
        base += (
            "\n\n# Prior research reports — related work you have already written (NOT primary sources)\n"
            "These are earlier reports in this knowledge base on related topics. Use them for "
            "context and to avoid re-deriving what they already cover. Where this report connects "
            "to, extends, or would otherwise duplicate one, reference it by its exact [[wikilink]] "
            "title (inline, or under a \"## Related research\" heading) and point the reader there "
            "rather than repeating it. Do NOT treat them as primary evidence and do NOT list them "
            "under ## Sources.\n\n"
            + prior_block
        )

    # task="synthesis" routes to OpenRouter ONLY (never Gemini) — see config.ROUTING.
    synthesis_system = load_prompt(system_name)
    draft = llm_simple(prompt=base, system=synthesis_system, task="synthesis")

    if depth != "comprehensive":
        return draft

    print("[research] Comprehensive depth — running critique + revise pass")
    critique = llm_simple(
        prompt=f"{base}\n\n# Draft report\n{draft}",
        system=load_prompt("research_critique"),
        task="synthesis",
    )
    revised = llm_simple(
        task="synthesis",
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


# Non-greedy so it also captures *malformed* links whose inner text contains stray
# brackets — e.g. two citations fused into one pair, `[[A], [B]]`. The old pattern
# `\[\[([^\]|#]+)\]\]` required a bracket-free interior, so it silently skipped
# fused links: they were neither detected nor repaired and rendered dead in Obsidian.
_WIKILINK_RE = re.compile(r"\[\[(.+?)\]\]")

# The exact fusion the model emits: `[[A], [B]]` (and longer) instead of
# `[[A]], [[B]]`. A stray "], [" between the outer brackets, deterministically split.
_FUSED_SEP_RE = re.compile(r"\]\s*,\s*\[")


def _normalize_fused_wikilinks(report: str) -> str:
    """Split fused citations like `[[A], [B]]` into `[[A]], [[B]]` deterministically."""
    def fix(m: re.Match) -> str:
        inner = m.group(1)
        if not _FUSED_SEP_RE.search(inner):
            return m.group(0)
        parts = [p.strip() for p in _FUSED_SEP_RE.split(inner) if p.strip()]
        return ", ".join(f"[[{p}]]" for p in parts)
    return _WIKILINK_RE.sub(fix, report)


def _find_bad_wikilinks(report: str, valid_titles: set[str]) -> list[str]:
    """
    Return the [[wikilinks]] in the report that aren't a real note title.

    A well-formed target has no stray brackets; any captured interior containing
    '[' or ']' is a malformed/fused link and always counts as bad so the repair
    pass sees it. Alias ('|') and heading ('#') suffixes are stripped for the check.
    """
    seen, bad = set(), []
    for raw in _WIKILINK_RE.findall(report):
        raw = raw.strip()
        target = raw.split("|", 1)[0].split("#", 1)[0].strip()
        malformed = "[" in raw or "]" in raw
        if (malformed or target not in valid_titles) and raw not in seen:
            seen.add(raw)
            bad.append(raw)
    return bad


def _repair_wikilinks(report: str, valid_titles: set[str]) -> str:
    """
    Replace citations that don't match a real note with the correct title.

    Synthesis occasionally drifts out of wikilink citation and into the numbered
    style of the papers it is summarising, emitting [[21]] instead of the title.
    Every such link is dead in Obsidian, so rather than warn and write it anyway,
    hand the report back with the valid titles and have the bad links resolved
    from context. Repair is best-effort: anything still unresolved is logged.
    """
    # Deterministically split fused citations (`[[A], [B]]` → `[[A]], [[B]]`) before
    # anything else — no model call needed, and it fixes the common syntax error the
    # LLM repair used to never even see.
    report = _normalize_fused_wikilinks(report)

    bad = _find_bad_wikilinks(report, valid_titles)
    if not bad:
        return report

    print(f"[research] {len(bad)} invalid wikilink(s) in report: {', '.join(bad[:10])}"
          + (" …" if len(bad) > 10 else ""))
    print("[research] Running citation repair pass")

    title_list = "\n".join(f"- [[{t}]]" for t in sorted(valid_titles))
    try:
        repaired = llm_simple(
            task="synthesis",
            system=load_prompt("research_repair_links"),
            prompt=(
                f"# Valid note titles\n{title_list}\n\n"
                f"# Invalid citations to fix\n" + "\n".join(f"- [[{b}]]" for b in bad) + "\n\n"
                f"# Report\n{report}"
            ),
        )
    except Exception as e:
        # A failed/truncated repair call must not discard an otherwise-complete
        # report — keep the deterministically-normalised version, dead links and all.
        print(f"[research] Repair pass failed ({e}); keeping report as-is")
        return report

    # Only accept the repair if it actually improved things — a mangled or
    # truncated response must not clobber a report whose prose is fine.
    if not repaired or len(repaired) < len(report) * 0.8:
        print("[research] Repair pass returned a suspiciously short report — keeping original")
        return report

    still_bad = _find_bad_wikilinks(repaired, valid_titles)
    if len(still_bad) >= len(bad):
        print("[research] Repair pass did not reduce invalid links — keeping original")
        return report

    if still_bad:
        print(f"[research] {len(still_bad)} link(s) still unresolved: {', '.join(still_bad[:10])}")
    else:
        print("[research] All citations resolved to real notes")
    return repaired


class IncompleteReportError(RuntimeError):
    """Synthesis returned a report that looks cut off mid-generation."""


_MIN_REPORT_CHARS = 400
# A complete report ends on sentence/closing punctuation, a wikilink ']', or
# markdown emphasis. It ends *badly* — mid-word or on a dangling connector — when
# synthesis was truncated: the one truncated report we found ended "…difficult for
# Israeli". Flag those so a half-report is never sealed into the vault as complete.
_TRUNCATED_TAIL = tuple(",;:–—-")


def _assert_report_complete(report: str, topic: str) -> None:
    """Raise IncompleteReportError if the report looks truncated mid-generation."""
    text = (report or "").rstrip()
    if len(text) < _MIN_REPORT_CHARS:
        raise IncompleteReportError(
            f"report for '{topic}' is only {len(text)} chars — synthesis likely failed"
        )
    last = text[-1]
    if last.isalnum() or last in _TRUNCATED_TAIL:
        tail = text[-60:].replace("\n", " ")
        raise IncompleteReportError(
            f"report for '{topic}' appears truncated — ends mid-sentence: “…{tail}”"
        )


_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

# Obsidian forbids these in filenames; ':' in particular is near-universal in the
# title/subtitle H1s reports come back with ("Topic: A Critical Examination").
_TITLE_PUNCT_RE = re.compile(r"\s*[:–—]\s*")


def _title_from_report(report: str) -> str | None:
    """
    Derive a note title from the report's own H1.

    Synthesis writes a genuinely good title ("World Models and Their Origins: A
    Critical Examination of…") and it used to be discarded in favour of the
    trigger's `topic`, which is often a bare keyword — or literally "Untitled"
    when the trigger omits it. Prefer the H1, trimmed of subtitle and clamped to
    a sane filename length.
    """
    match = _H1_RE.search(report)
    if not match:
        return None

    title = match.group(1).strip().strip("*_`")
    # Keep the part before the first ':' / dash — the subtitle is usually the
    # long half, and the head alone reads better as a note name.
    head = _TITLE_PUNCT_RE.split(title)[0].strip()
    if len(head) >= 15:
        title = head

    title = safe_filename(title).strip()
    if len(title) > 90:
        title = title[:90].rsplit(" ", 1)[0].strip()
    return title or None


def _research_note_name(fm: dict, topic: str, report: str) -> str:
    """
    Pick the filename for a research note.

    Precedence: an explicit `output:` in the trigger (the user asked for it by
    name) → the report's own H1 → the trigger's topic.
    """
    explicit = fm.get("output")
    if explicit:
        return explicit if explicit.endswith(".md") else f"{explicit}.md"

    derived = _title_from_report(report)
    if derived:
        return f"Research - {derived}.md"

    return f"Research - {safe_filename(topic)}.md"


def _renamed_trigger_path(path: Path, output_name: str) -> Path | None:
    """
    Where to move a completed trigger so it reads as the report it generated,
    instead of the ad-hoc title typed on the phone ("Untitled", "research - x").

    Named after the report with the "Research - " prefix stripped, so the trigger
    never *shares* a basename with the report — a duplicate basename makes
    [[wikilinks]] ambiguous in Obsidian. Returns None when renaming isn't safe:
    already named that, the report kept no prefix (so the names would collide), or
    a note with the target name already exists in the folder.
    """
    report_stem = output_name[:-3] if output_name.endswith(".md") else output_name
    trigger_stem = report_stem
    prefix = "Research - "
    if trigger_stem.startswith(prefix):
        trigger_stem = trigger_stem[len(prefix):].strip()
    trigger_stem = safe_filename(trigger_stem)

    if not trigger_stem or trigger_stem == report_stem:
        return None
    dest = path.parent / f"{trigger_stem}.md"
    if dest == path or dest.exists():
        return None
    return dest


# ── Core pipeline ────────────────────────────────────────────────────────────────

def _run_research(topic: str, depth: str, seed_urls: list[str],
                  context: str = "", context_kind: str = "callout",
                  synthesis_system_name: str = "research_synthesis",
                  exclude_research_title: str | None = None) -> tuple[str, list[dict]]:
    """
    Run phases ①–④ for a topic. Returns (report_markdown, all_sources).
    Shared by both trigger notes and inline callouts.

    `context` grounds discovery and synthesis in extra text. `context_kind`
    labels it: "callout" (the host note an inline [!research] lives in, with a
    callout-aware `synthesis_system_name`) or "brief" (a trigger note's details +
    acceptance criteria). `synthesis_system_name` selects the synthesis prompt.
    `exclude_research_title` drops that report from the prior-research lane so a
    re-run doesn't feed a report its own previous version.
    """
    reset_queue()

    # ① Relevant existing clippings (primary sources) and prior research reports
    #    (related work). These are kept in separate lanes: clippings become cited
    #    sources; prior research is context the report builds on and cross-links to.
    existing = find_relevant_clippings(topic)
    if existing:
        print(f"[research] Found {len(existing)} relevant existing clipping(s).")
    prior_research = find_relevant_research(topic, exclude_title=exclude_research_title)
    if prior_research:
        print(f"[research] Found {len(prior_research)} related prior research report(s).")

    # ② Discovery tool loop
    prompt = _build_discovery_prompt(topic, depth, seed_urls, existing,
                                     context=context, context_kind=context_kind,
                                     prior_research=prior_research)
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
                         context=context, system_name=synthesis_system_name,
                         context_kind=context_kind, prior_research=prior_research)
    _, valid_titles = _build_source_block(all_sources)
    valid_titles |= {p["title"] for p in prior_research}
    report = _repair_wikilinks(report, valid_titles)

    # Gate: never write/index a report that was cut off mid-generation. Raising here
    # aborts before the trigger is marked done, so it stays pending and retries (the
    # already-vaulted sources are reused). Skipped for the no-sources stub, which is
    # legitimately short and would otherwise retry forever.
    if all_sources or prior_research:
        _assert_report_complete(report, topic)

    return report, all_sources


# Leading completion banner stripped before re-preserving the brief on completion,
# so re-completing a trigger never stacks banners. Matches the current callout form
# and the legacy "Research completed. See [[…]]." line for back-compat.
_COMPLETION_BANNER_RE = re.compile(
    r"\A\s*(?:> \[!done\] )?Research completed[^\n]*\n*", re.IGNORECASE
)


# A trigger can gate itself behind a '## Ready' checkbox so a half-written brief
# is never picked up mid-edit: while the section exists with an unticked box the
# trigger is skipped, and every rescan re-checks it (the note stays research: true)
# until the box is ticked. No '## Ready' section at all = ready, so hand-written
# triggers without the template still fire immediately.
_READY_SECTION_RE = re.compile(
    r"^#{1,6}\s+Ready\b.*?(?=^#{1,6}\s|\Z)", re.MULTILINE | re.DOTALL | re.IGNORECASE
)
_TICKED_BOX_RE = re.compile(r"^\s*[-*]\s*\[[xX]\]", re.MULTILINE)


def _is_ready(body: str) -> bool:
    """True unless a '## Ready' section exists with no ticked checkbox."""
    section = _READY_SECTION_RE.search(body)
    if not section:
        return True
    return bool(_TICKED_BOX_RE.search(section.group(0)))


def find_pending_triggers(triggers_path: Path) -> list[Path]:
    """Find trigger notes not yet processed (research: true, not marked done)."""
    pending = []
    if not triggers_path.exists():
        return pending
    for md_file in triggers_path.glob("*.md"):
        try:
            fm, body = read_note(md_file)
            if fm.get("research") is True and _is_ready(body):
                pending.append(md_file)
        except Exception:
            continue
    return pending


# Depth in a trigger can be picked with a body checklist (plugin-free "multiple
# choice" that works by tapping on mobile) instead of a frontmatter key. We only
# look inside a `## Depth` section, and only accept the three known values, so an
# Acceptance-Criteria checkbox can never be mistaken for a depth choice.
_DEPTH_CHOICES = ("standard", "deep", "comprehensive")
_DEPTH_SECTION_RE = re.compile(
    r"^#{1,6}\s+Depth\b.*?(?=^#{1,6}\s|\Z)", re.MULTILINE | re.DOTALL | re.IGNORECASE
)
_DEPTH_CHECKBOX_RE = re.compile(
    r"^\s*[-*]\s*\[[xX]\]\s*(standard|deep|comprehensive)\b", re.MULTILINE | re.IGNORECASE
)


def _depth_from_body(body: str) -> str | None:
    """Return the depth ticked in a '## Depth' checklist, or None if none is."""
    section = _DEPTH_SECTION_RE.search(body)
    scope = section.group(0) if section else body
    box = _DEPTH_CHECKBOX_RE.search(scope)
    return box.group(1).lower() if box else None


def _index_entry(topic: str, report: str) -> tuple[list[str], str]:
    """
    Ask the cheap ('moc') model for a research report's tags and index summary.

    Both come from one call because both need the model to have read the report.

    Historically tags were just the topic's first four words lowercased, which
    yielded junk like ["quantum", "computing", "breakthroughs", "2025"]. This reads
    the report and returns real subject/method tags, reusing the existing tag
    vocabulary (Index/_tags.md) where it fits so the vault doesn't accumulate
    near-duplicates. All results pass through normalize_tags (lowercase-hyphenated).

    The summary is the one-liner written into the note's MOC entry. It used to be
    `report[:300]` — a raw prefix that dragged the report's H1 and opening
    paragraphs into what must be a single list item, wrecking the MOC's formatting.

    Falls back to the normalised word-split and a trimmed first line on any
    failure, so a completed research run is never lost over its index entry.
    """
    fallback_tags = normalize_tags(topic.split()[:4])
    fallback_summary = _fallback_summary(report)
    try:
        prompt = load_prompt("research_tags", topic=topic,
                             report=report[:SYNTHESIS_RAW_EXCERPT],
                             vocabulary=format_tag_vocabulary())
        data = parse_json_response(llm_simple(prompt=prompt, task="moc"))
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        tags = normalize_tags(data.get("tags") or [])[:6] or fallback_tags
        summary = one_line(data.get("summary") or "") or fallback_summary
        return tags, summary
    except Exception as e:
        print(f"[research] Index entry extraction failed ({e}); using fallbacks")
    return fallback_tags, fallback_summary


def _fallback_summary(report: str) -> str:
    """First real sentence of the report, for when the model can't be reached."""
    for line in report.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ">", "-", "|", "$")):
            continue
        return one_line(line)
    return ""


def process_research_trigger(path: Path):
    """
    Full research pipeline for a _triggers/ note. Writes a report to Research/.
    Skips silently if not a valid unprocessed research trigger.
    """
    try:
        fm, body = read_note(path)
    except Exception as e:
        print(f"[research] Could not read {path.name}: {e}")
        return

    if fm.get("research") is not True:
        return

    # Unticked '## Ready' box → still being written. The rescan re-checks it
    # every cycle, so ticking the box (even remotely) starts the run.
    if not _is_ready(body):
        print(f"[research] Not ready (unticked '## Ready' box), waiting: {path.name}")
        return

    # `or`-fallbacks (not .get defaults) so a present-but-empty YAML key — common
    # when a trigger is hand-edited from the template on a phone — falls back
    # instead of yielding None and crashing downstream.
    seed_urls = fm.get("urls") or []

    # Depth precedence: a ticked '## Depth' checklist box (the template's
    # plugin-free "multiple choice") wins, then a frontmatter `depth:`, then
    # standard. The checklist is the template default; the YAML key still works.
    depth = _depth_from_body(body) or fm.get("depth") or "standard"

    # The note body (e.g. Details + Acceptance Criteria from the trigger template)
    # is passed through as a research brief so discovery and synthesis are steered
    # by what the user actually wants. HTML comments (the template's usage notes)
    # and the '## Depth' / '## Ready' control sections are stripped so only real
    # content reaches the agent. Empty bodies (after stripping) leave behaviour
    # unchanged.
    brief = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    brief = _DEPTH_SECTION_RE.sub("", brief)
    brief = _READY_SECTION_RE.sub("", brief).strip()

    # `or`-fallbacks (not .get defaults) so a present-but-empty YAML key — common
    # when a trigger is hand-edited from the template on a phone — falls back
    # instead of yielding None and crashing downstream. A trigger whose `topic:`
    # is empty and whose filename is the Obsidian default ("Untitled") carries no
    # topic at all: fall back to the brief, which is where the real question is,
    # rather than researching the literal string "Untitled".
    topic = fm.get("topic") or path.stem.replace("research - ", "").strip()
    if topic.lower().startswith("untitled") and brief:
        topic = " ".join(brief.split())[:300]
        print(f"[research] Trigger has no topic; using the brief: '{topic[:80]}…'")

    print(f"[research] Starting: '{topic}' (depth: {depth})")

    # Provisional name, used only to keep a re-run from feeding a report its own
    # previous version. The real name is derived from the finished report below.
    provisional = fm.get("output") or f"Research - {safe_filename(topic)}.md"

    report, _ = _run_research(topic, depth, seed_urls,
                              context=brief, context_kind="brief",
                              exclude_research_title=provisional.replace(".md", ""))

    # Derive real topical tags and a one-line index summary from the report
    # (not the topic's first few words / the report's first 300 characters).
    tags, summary = _index_entry(topic, report)

    # Name the note from the report's own H1 unless the trigger asked for a
    # specific `output:` — a bare/absent topic still yields a proper title.
    output_name = _research_note_name(fm, topic, report)

    # Write the research note
    output_path = RESEARCH_PATH / output_name
    write_note(
        output_path,
        frontmatter={
            "generated": True,
            "topic": topic,
            "depth": depth,
            "date": today(),
            "tags": tags,
        },
        body=report,
    )
    print(f"[research] Written: {output_name}")

    # Index the research note itself
    index_note(
        note_title=output_name.replace(".md", ""),
        note_path=output_path,
        summary=summary,
        tags=tags,
        analysis=report,
    )

    # Mark trigger as done so watchdog doesn't reprocess (only the `research` flag
    # gates reprocessing, not the body). Preserve the original brief below a
    # completion banner rather than overwriting it — otherwise re-running a trigger
    # (flip research: done → true) would feed the completion note in as the brief.
    # Strip any prior banner so re-completion doesn't stack them.
    fm["research"] = "done"
    fm["completed"] = today()
    banner = f"> [!done] Research completed — see [[{output_name.replace('.md', '')}]]."
    preserved = _COMPLETION_BANNER_RE.sub("", body).lstrip()
    done_body = f"{banner}\n\n{preserved}" if preserved else banner

    # Mark done in place first (so the trigger can never be reprocessed), then move
    # it to match the report name — a best-effort, purely cosmetic rename so the
    # leftover triggers read as their reports rather than phone-typed titles.
    write_note(path, fm, done_body)
    dest = _renamed_trigger_path(path, output_name)
    if dest is None:
        print(f"[research] Done: '{topic}'")
    else:
        try:
            path.rename(dest)
            print(f"[research] Done: '{topic}' — trigger renamed → {dest.name}")
        except OSError as e:
            print(f"[research] Done: '{topic}' (trigger rename skipped: {e})")


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
