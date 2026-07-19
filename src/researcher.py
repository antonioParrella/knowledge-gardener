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
import time
from pathlib import Path

from config import (
    RESEARCH_PATH, INBOX_PATH, CONCEPTS_PATH, TRIGGERS_PATH,
    CLIP_CONTENT_LIMIT, PAPER_CONTENT_LIMIT, SYNTHESIS_RAW_EXCERPT,
    MAX_CONCEPTS_PER_REPORT, CALLOUT_QUIET_SECS,
    load_prompt,
)
from notes import (read_note, write_note, safe_filename, today, normalize_tags,
                   normalize_math_delimiters)
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
            "## Already in the knowledge base — current coverage of this topic\n"
            "These sources are already indexed. Use them as a picture of current "
            "coverage: aim your searches at what's missing, newer, or better, and "
            "don't queue a source that just restates what one of these already covers "
            "as well. The more this list already covers the topic, the less new "
            "material there is to add.\n\n" + known
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


def _clip_source(url: str, title: str, kind: str, body: str, full_text: bool) -> dict | None:
    """
    Write a queued source's text as a clip stub and run it through the clipper
    pipeline, returning {title, analysis, raw} — or None if the clipper discarded it
    (e.g. its analyzer judged the content not to be the real document, or a dup race).
    """
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
            "full_text": full_text,
        },
        body=body,
    )

    limit = PAPER_CONTENT_LIMIT if (kind == "pdf" and full_text) else CLIP_CONTENT_LIMIT
    final_path = process_clipped_note(clip_path, content_limit=limit)
    if not final_path or not final_path.exists():
        # Never leave the stub behind. The clipper unlinks on its own discard paths
        # (duplicate, usable:false), but a JSON-parse failure returns None with the
        # stub still on disk, and the full-text→abstract fallback abandons the base
        # file. Either way the watchdog's backlog scan would re-ingest it as a second
        # clip for the same source — the exact origin of the overnight duplicates.
        clip_path.unlink(missing_ok=True)
        return None

    _, out = read_note(final_path)
    analysis = out.split("## Original Content")[0].strip()
    raw = ""
    if "## Original Content" in out:
        raw = out.split("## Original Content", 1)[1].strip()[:SYNTHESIS_RAW_EXCERPT]
    return {"title": final_path.stem, "analysis": analysis, "raw": raw}


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
    full_text_ok = bool(text) and not text.startswith("Failed to fetch")

    # Try the full text first. The clipper's analyzer judges whether the retrieved
    # text is the real document; if it isn't (raw PDF bytes, a bot-wall / CAPTCHA /
    # paywall interstitial), process_clipped_note discards the stub and _clip_source
    # returns None — so we fall through to the abstract. A clean, thin, citable clip
    # beats a garbage one.
    if full_text_ok:
        result = _clip_source(url, title, kind, text, full_text=True)
        if result:
            return result
        print(f"[research] Full text unusable — falling back to abstract: {title}")

    # Abstract-only fallback: full text couldn't be retrieved (e.g. paywalled PDF) or
    # was judged not to be the real document above. Still kept and citable, just thinner.
    if abstract:
        body = (
            "> [!warning] Abstract only — full text could not be retrieved.\n\n"
            f"## Abstract\n{abstract}"
        )
        return _clip_source(url, title, kind, body, full_text=False)

    print(f"[research] No usable source and no abstract available: {url}")
    return None


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

    # Deterministic math backstop: synthesis habitually emits \( \) / \[ \] despite
    # the prompt (Obsidian's MathJax only renders $…$ / $$…$$), and a currency $ on
    # a converted line would mispair with the new math $. Convert delimiters + escape
    # currency here — after link repair (link-only) and before the completeness gate.
    report = normalize_math_delimiters(report, escape_currency=True)

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

    # The trigger note's TITLE is its research topic. The old `topic:` frontmatter
    # field was removed from the template — it was redundant with the Details
    # brief, so the note name now carries the focused phrase and the brief the
    # detail. A note left at Obsidian's "Untitled" default (or with an empty name)
    # carries no topic in its title: fall back to the brief, where the real
    # question is, rather than researching the literal string "Untitled".
    topic = path.stem.replace("research - ", "").strip()
    if (not topic or topic.lower().startswith("untitled")) and brief:
        topic = " ".join(brief.split())[:300]
        print(f"[research] Trigger has no topic in its title; using the brief: '{topic[:80]}…'")

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

    # Extract foundational concepts from the finished report: link them into the
    # report body and queue concept-explainer triggers for the ones not already in
    # the vault. Best-effort — concepts are additive, so a failure here never blocks
    # completing the research run.
    try:
        _conceptualize(output_path, report, output_name.replace(".md", ""))
    except Exception as e:
        print(f"[concept] Conceptualization failed for {output_name}: {e}")

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

_DEPTHS = ("standard", "deep", "comprehensive")

# The callout marker. Depth is encoded in the callout *type* so each depth is a
# distinct, one-tap-insertable marker that still renders as a normal callout (the
# question stays the visible title): [!research] / [!research-deep] /
# [!research-comprehensive]. Both '-' and '|' separators are accepted so a
# hand-typed [!research|deep] works too. Group 1 = depth (None → standard),
# group 2 = the question.
_CALLOUT_RE = re.compile(
    r"^>\s*\[!research(?:[-|](standard|deep|comprehensive))?\]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)

# In-progress marker left while a run is underway. It carries the depth for
# non-standard runs (as "(deep)"/"(comprehensive)") so revert_stale_callouts can
# restore the original depth after a crash instead of downgrading to standard.
_STALE_RE = re.compile(
    r"^>\s*\[!info\]\s*Researching(?:\s*\((standard|deep|comprehensive)\))?:\s*(.+?)\s*[.…]*\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Folders skipped when scanning the vault for callouts: the trigger pipeline owns
# _triggers/, Templates/ holds callout *templates* (whose "> [!research] <% … %>"
# marker must never be run as a live callout — doing so rewrites the template with a
# done-block), and these are config/agent dirs a user wouldn't annotate.
_CALLOUT_SKIP_DIRS = {"_triggers", "Templates", ".obsidian", ".trash"}
# iCloud/Obsidian conflict copies are not real notes — a "Note (conflicted).md"
# holding a callout must not be processed as if the user wrote it.
_CONFLICT_NAME_RE = re.compile(r"\(conflicted|sync-conflict", re.IGNORECASE)


def _norm_depth(depth: str | None) -> str:
    d = (depth or "standard").lower()
    return d if d in _DEPTHS else "standard"


def _skip_callout_path(path: Path) -> bool:
    """True if a vault path should never be scanned/edited for callouts."""
    if any(part in _CALLOUT_SKIP_DIRS for part in path.parts):
        return True
    return bool(_CONFLICT_NAME_RE.search(path.name))


def _in_progress_marker(topic: str, depth: str) -> str:
    label = "" if depth == "standard" else f" ({depth})"
    return f"> [!info] Researching{label}: {topic}…"


def _research_line_re(topic: str) -> re.Pattern:
    """Matches the specific '> [!research…] <topic>' line for one topic (any depth),
    so a note with several callouts marks the right one."""
    return re.compile(
        rf"^>\s*\[!research(?:[-|](?:standard|deep|comprehensive))?\]\s*{re.escape(topic)}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )


def _in_progress_line_re(topic: str) -> re.Pattern:
    """Tolerant match of the in-progress marker for one topic: any depth label, and
    any trailing '.'/'…' form, so ellipsis/whitespace drift doesn't miss it."""
    return re.compile(
        rf"^>\s*\[!info\]\s*Researching(?:\s*\([^)]*\))?:\s*{re.escape(topic)}\s*[.…]*\s*$",
        re.IGNORECASE | re.MULTILINE,
    )


def find_research_callouts(
    folders: list[Path], quiet_secs: float = CALLOUT_QUIET_SECS
) -> list[tuple[Path, str, str]]:
    """
    Scan note folders **recursively** for active research callouts.

    Callouts are meant to be written into whatever note you're working in, anywhere
    in the vault — the findings are appended in place (see process_research_callout),
    like a review comment. The watchdog therefore passes the vault root so any note
    can host a callout, not just the agent-managed folders.

    Returns (path, question, depth) tuples. A note modified within `quiet_secs` is
    skipped — it may be mid-edit, and the watchdog writes the note from a separate
    process, so acting on a note you're actively typing in would clobber it.
    """
    results = []
    seen = set()
    cutoff = time.time() - quiet_secs
    for folder in folders:
        if not folder.exists():
            continue
        for path in folder.rglob("*.md"):
            if path in seen or _skip_callout_path(path):
                continue
            seen.add(path)
            try:
                if path.stat().st_mtime > cutoff:
                    continue  # touched too recently — leave it alone
            except OSError:
                continue
            try:
                _, body = read_note(path)
            except Exception:
                continue
            for m in _CALLOUT_RE.finditer(body):
                topic = m.group(2).strip()
                # An unrendered Templater tag (e.g. a template inserted without
                # Templater processing it) is not a real question — never research it.
                if not topic or "<%" in topic:
                    continue
                results.append((path, topic, _norm_depth(m.group(1))))
    return results


def revert_stale_callouts(folders: list[Path], older_than_secs: float) -> None:
    """
    A callout stuck at '> [!info] Researching…' on a note untouched for
    `older_than_secs` means its run crashed or OpenRouter was down (the in-progress
    marker is the only thing stopping re-processing, so it would otherwise strand
    forever). Revert it to '> [!research…]', preserving the original depth, so the
    normal scan retries it — the crash-recovery analogue of pending triggers.
    """
    cutoff = time.time() - older_than_secs
    seen = set()

    def _revert(m: re.Match) -> str:
        depth = _norm_depth(m.group(1))
        topic = m.group(2).strip()
        suffix = "" if depth == "standard" else f"-{depth}"
        return f"> [!research{suffix}] {topic}"

    for folder in folders:
        if not folder.exists():
            continue
        for path in folder.rglob("*.md"):
            if path in seen or _skip_callout_path(path):
                continue
            seen.add(path)
            try:
                if path.stat().st_mtime > cutoff:
                    continue  # recently touched — a run may still be writing it
            except OSError:
                continue
            try:
                fm, body = read_note(path)
            except Exception:
                continue
            new_body, n = _STALE_RE.subn(_revert, body)
            if n:
                try:
                    write_note(path, fm, new_body)
                    print(f"[research] Reverted {n} stale callout(s) in {path.name} for retry.")
                except Exception as e:
                    print(f"[research] Could not revert stale callout in {path.name}: {e}")


def process_research_callout(path: Path, topic: str, depth: str = "standard"):
    """
    Run research for a research callout and append the findings inline, replacing
    the callout in the same note. `depth` (standard/deep/comprehensive) comes from
    the callout type and drives how thorough discovery + synthesis are.
    """
    depth = _norm_depth(depth)
    try:
        fm, body = read_note(path)
    except Exception as e:
        print(f"[research] Could not read callout note {path.name}: {e}")
        return

    in_progress = _in_progress_marker(topic, depth)

    # Capture the rest of the note (with the callout line removed) as document
    # context, so research is grounded in whatever the user is writing about.
    context = _CALLOUT_RE.sub("", body, count=1).strip()

    # Mark in-progress immediately so the next rescan won't double-process. Target
    # the specific callout for this topic (a note may hold several); fall back to
    # the first research callout if the topic-specific match somehow misses.
    # A lambda replacement avoids interpreting backslashes in the marker text.
    body, n = _research_line_re(topic).subn(lambda m: in_progress, body, count=1)
    if n == 0:
        body, n = _CALLOUT_RE.subn(lambda m: in_progress, body, count=1)
    write_note(path, fm, body)

    print(f"[research] Callout ({depth}): '{topic}' in {path.name}")
    report, _ = _run_research(
        topic, depth, [],
        context=context,
        synthesis_system_name="research_callout",
    )

    inline_block = (
        f"> [!done] Researched: {topic}\n\n"
        f"---\n\n### {topic}\n*{today()}*\n\n{report}\n\n---\n"
    )

    # Re-read (the note may have changed during the multi-minute run) and swap the
    # in-progress marker for the result. Match tolerantly so ellipsis/whitespace
    # drift doesn't miss it; if the marker is gone entirely (user edited/removed it),
    # append the result rather than silently dropping a finished report.
    fm2, body2 = read_note(path)
    body2, n2 = _in_progress_line_re(topic).subn(lambda m: inline_block, body2, count=1)
    if n2 == 0:
        print(f"[research] In-progress marker missing in {path.name}; appending result.")
        body2 = body2.rstrip() + "\n\n" + inline_block
    write_note(path, fm2, body2)
    print(f"[research] Callout complete: '{topic}' in {path.name}")


# ── Concept notes ─────────────────────────────────────────────────────────────────
# A third content type: university-textbook-level explainers of the foundational,
# reusable concepts a research report leans on. A cheap conceptualizer pass reads a
# finished report, links the concepts into it, and queues a `concept: true` trigger
# for each one not already in the vault. The watchdog then runs process_concept_trigger
# to write each Concepts/Concept - <term>.md once — reusable by every later report.


def _concept_path(term: str) -> Path:
    """Vault path for a concept note, named `Concept - <term>` (like Research -/MOC -)."""
    return CONCEPTS_PATH / f"Concept - {safe_filename(term)}.md"


def _concept_key(term: str) -> str:
    """Dedup key for a concept term (filename-safe, case-folded)."""
    return safe_filename(term).strip().lower()


_MATCH_KEY_RE = re.compile(r"[^a-z0-9]+")


def _match_key(term: str) -> str:
    """
    Aggressive key for deciding whether two names refer to the SAME concept,
    regardless of casing, spacing, or punctuation style — so 'Chamley-Judd Theorem',
    'Chamley–Judd Theorem' (en-dash) and 'chamley  judd  theorem' all collapse to one.

    Used to snap an LLM-returned term onto an existing concept's canonical title, so a
    link from a *new* report maps to the existing note instead of a near-duplicate.
    (Distinct from _concept_key, which is filename-based and so keeps hyphen vs en-dash
    apart — exactly the drift this key is meant to absorb.)
    """
    return _MATCH_KEY_RE.sub(" ", term.lower()).strip()


def _existing_concept_terms() -> set[str]:
    """Display terms of concepts already explained (Concepts/Concept - <term>.md)."""
    terms = set()
    prefix = "Concept - "
    for p in CONCEPTS_PATH.glob("Concept - *.md"):
        if p.stem.startswith(prefix):
            terms.add(p.stem[len(prefix):])
    return terms


def _pending_concept_terms() -> set[str]:
    """Display terms of every not-yet-processed concept trigger in _triggers/."""
    terms = set()
    if not TRIGGERS_PATH.exists():
        return terms
    for md in TRIGGERS_PATH.glob("*.md"):
        try:
            fm, _ = read_note(md)
        except Exception:
            continue
        if fm.get("concept") is True:
            t = (fm.get("term") or "").strip()
            if t:
                terms.add(t)
    return terms


# ── Inline concept linking (deterministic; no report regeneration) ────────────────

# Headings that mark a report's trailing apparatus — inline concept linking must not
# reach into them: ## Sources lists exact source titles, ## Related research links
# prior reports, and ## Concepts is the backstop list we append just below.
_TRAILING_SECTION_RE = re.compile(
    r"^#{1,6}\s+(?:Sources|Related research|Concepts)\b", re.MULTILINE | re.IGNORECASE
)


def _protected_spans(line: str) -> list[tuple[int, int]]:
    """Char ranges in a line that inline linking must not touch: existing wikilinks
    and inline code spans."""
    spans = [m.span() for m in _WIKILINK_RE.finditer(line)]
    spans += [m.span() for m in re.finditer(r"`[^`]*`", line)]
    return spans


def _link_first_mention(line: str, mention: str, target: str) -> str | None:
    """
    Wrap the first clean occurrence of `mention` in `line` with an aliased
    `[[target|matched text]]`, preserving the report's real casing as the alias.
    Returns the modified line, or None if no clean occurrence exists (in code, in an
    existing link, or absent).
    """
    if not mention:
        return None
    lowered, needle = line.lower(), mention.lower()
    protected = _protected_spans(line)
    start = 0
    while True:
        idx = lowered.find(needle, start)
        if idx == -1:
            return None
        end = idx + len(mention)
        if any(a < end and idx < b for a, b in protected):
            start = idx + 1
            continue
        matched = line[idx:end]  # keep the report's own wording for the display text
        return f"{line[:idx]}[[{target}|{matched}]]{line[end:]}"


def _link_concepts_inline(report: str, concepts: list[dict]) -> str:
    """
    Wrap the first clean occurrence of each concept's `mention` in an aliased
    wikilink to its `Concept - <term>` note, in place — deterministic string surgery
    in the spirit of _normalize_fused_wikilinks, with no LLM call and no regeneration
    (so it can never drift prose or truncate).

    Skips markdown headings, fenced/inline code, existing wikilinks, and the report's
    trailing ## Sources / ## Related research / ## Concepts apparatus. Each concept is
    linked at most once; a concept whose mention can't be placed cleanly is simply
    left out of the inline pass and survives via the trailing ## Concepts list.
    """
    tail_match = _TRAILING_SECTION_RE.search(report)
    cut = tail_match.start() if tail_match else len(report)
    head, tail = report[:cut], report[cut:]

    lines = head.split("\n")
    in_fence = False
    pending = {c["term"]: c for c in concepts if c.get("mention")}
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or stripped.startswith("#"):
            continue
        for term, c in list(pending.items()):
            new_line = _link_first_mention(line, c["mention"], f"Concept - {term}")
            if new_line is not None:
                lines[i] = line = new_line
                del pending[term]
        if not pending:
            break

    return "\n".join(lines) + tail


def _append_concepts_section(report: str, concepts: list[dict]) -> str:
    """Append a trailing '## Concepts' list linking every picked concept — the
    backstop so a concept whose inline mention couldn't be placed is never lost."""
    entries = "\n".join(f"- [[Concept - {c['term']}]]" for c in concepts)
    return report.rstrip() + f"\n\n## Concepts\n{entries}\n"


_APPEARS_IN_HEADING = "## Appears in"


def _append_backlink(concept_path: Path, source_title: str) -> None:
    """
    Idempotently record that `source_title` references this concept, under the
    note's '## Appears in' section. Safe to call repeatedly — a concept is reused
    across many reports, and each new one that references it lands here.
    """
    if not source_title:
        return
    try:
        fm, body = read_note(concept_path)
    except Exception:
        return
    link = f"[[{source_title}]]"
    if link in body:
        return
    if _APPEARS_IN_HEADING in body:
        body = body.replace(_APPEARS_IN_HEADING, f"{_APPEARS_IN_HEADING}\n- {link}", 1)
    else:
        body = body.rstrip() + f"\n\n{_APPEARS_IN_HEADING}\n- {link}\n"
    write_note(concept_path, fm, body)


def _write_concept_trigger(term: str, context: str, source_title: str) -> None:
    """Write a _triggers/ note the watchdog picks up to generate a concept note."""
    trigger_path = TRIGGERS_PATH / f"concept - {safe_filename(term)}.md"
    if trigger_path.exists():
        return
    write_note(
        trigger_path,
        frontmatter={"concept": True, "term": term, "source": source_title},
        body=context or "",
    )
    print(f"[concept] Queued concept trigger: {term}")


def _conceptualize(report_path: Path, report: str, report_title: str) -> None:
    """
    Read a finished research report, pick the foundational concepts worth their own
    explainer note (one cheap moc-tier call), link them into the report body, and
    queue concept triggers for the ones not already covered.

    Best-effort throughout: bad/empty model output or a write hiccup leaves the
    report as-is — concepts are additive and must never block a research run.
    """
    existing = _existing_concept_terms()          # display terms of built concepts
    pending = _pending_concept_terms()            # display terms of queued-but-unbuilt

    # Canonical title for every concept already known (built or already queued),
    # keyed aggressively (casing/punctuation-insensitive) so a variant the model
    # returns still maps onto it. Built concepts win over merely-pending ones.
    canonical_by_match: dict[str, str] = {}
    for t in list(pending) + sorted(existing):
        canonical_by_match[_match_key(t)] = t
    pending_keys = {_concept_key(t) for t in pending}

    raw = llm_simple(
        task="moc",
        prompt=load_prompt(
            "concept_extract",
            report=report[:SYNTHESIS_RAW_EXCERPT],
            existing_concepts="\n".join(f"- {t}" for t in sorted(existing)) or "(none yet)",
            max_concepts=str(MAX_CONCEPTS_PER_REPORT),
        ),
    )
    data = parse_json_response(raw)
    if not isinstance(data, list) or not data:
        print("[concept] No concepts extracted")
        return

    # Clean, dedup, and cap the picks. Snap each pick onto an existing concept's
    # canonical title when they name the same concept under different casing or
    # punctuation — so a link from THIS report maps to the existing note rather than
    # spawning a near-duplicate (the guarantee: one concept, one note, linked everywhere).
    concepts: list[dict] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        term = (item.get("term") or "").strip()
        if not term:
            continue
        canonical = canonical_by_match.get(_match_key(term))
        if canonical and canonical != term:
            print(f"[concept] Mapped '{term}' → existing concept '{canonical}'")
            term = canonical
        key = _concept_key(term)
        if not key or key in seen:
            continue
        seen.add(key)
        concepts.append({
            "term": term,
            "mention": (item.get("mention") or "").strip(),
            "context": (item.get("context_excerpt") or "").strip(),
        })
        if len(concepts) >= MAX_CONCEPTS_PER_REPORT:
            break
    if not concepts:
        return

    # Link every picked concept into the report (inline where the mention can be
    # placed cleanly; all of them into the trailing ## Concepts backstop list).
    linked = _append_concepts_section(_link_concepts_inline(report, concepts), concepts)
    fm, _ = read_note(report_path)
    fm["concepts_extracted"] = True
    write_note(report_path, fm, linked)
    print(f"[concept] Linked {len(concepts)} concept(s) into {report_path.name}")

    # Queue a trigger for each genuinely new concept; for ones already in the vault,
    # just record this report as a backlink (no regeneration).
    for c in concepts:
        term, key = c["term"], _concept_key(c["term"])
        cpath = _concept_path(term)
        if cpath.exists():
            _append_backlink(cpath, report_title)
            print(f"[concept] Already exists, backlinked: {term}")
        elif key in pending_keys:
            print(f"[concept] Trigger already pending: {term}")
        else:
            _write_concept_trigger(term, c["context"], report_title)
            pending_keys.add(key)


def _run_concept(term: str, context: str, source_title: str) -> str:
    """
    Generate a concept explainer: a restrained discovery loop (often queuing nothing)
    followed by textbook-level synthesis on the top model. Unlike _run_research, zero
    sources is a normal, valid outcome — the explainer is written from established
    knowledge grounded in `context` regardless. Returns the note body (starts '# ...').
    """
    reset_queue()

    # Existing clippings can seed the explainer (reused, never re-fetched).
    existing = find_relevant_clippings(term)
    if existing:
        print(f"[concept] Found {len(existing)} relevant existing clipping(s).")

    known = ""
    if existing:
        known = "\n\n## Already in the knowledge base (do not re-discover these):\n" + \
                "\n".join(f"- [[{c['title']}]]" for c in existing)
    discovery_prompt = (
        f"Concept to explain at a university-textbook level:\n\n**{term}**\n\n"
        f"## How this concept came up (context)\n{context[:CLIP_CONTENT_LIMIT]}"
        f"{known}\n\n"
        "Queue sources ONLY if you genuinely need them to explain this concept "
        "accurately. For a well-settled concept, queue nothing and confirm you're done."
    )
    llm_tool_loop(
        prompt=discovery_prompt,
        system=load_prompt("concept_system"),
        tool_schema=TOOL_SCHEMA,
        tool_executor=execute_tool,
        task="research",
    )

    newly: list[dict] = []
    for entry in get_queue():
        result = _process_source(entry)
        if result:
            newly.append(result)
    if newly:
        print(f"[concept] Processed {len(newly)} new source(s) into the knowledge base.")

    all_sources = existing + newly
    source_block, valid_titles = _build_source_block(all_sources)

    if all_sources:
        index_titles = "\n".join(f"- [[{s['title']}]]" for s in all_sources)
        base = (
            f"# Concept to explain\n{term}\n\n"
            f"# Context — how this concept came up\n{context[:CLIP_CONTENT_LIMIT]}\n\n"
            f"# Source index (cite using these EXACT wikilink titles, only where useful)\n{index_titles}\n\n"
            f"# Sources with analysis\n{source_block}"
        )
    else:
        base = (
            f"# Concept to explain\n{term}\n\n"
            f"# Context — how this concept came up\n{context[:CLIP_CONTENT_LIMIT]}\n\n"
            "(No sources were gathered — write the explainer from established knowledge; "
            "include no citations and no ## Sources section.)"
        )

    concept_note = llm_simple(
        prompt=base,
        system=load_prompt("concept_synthesis"),
        task="synthesis",
    )

    # Repair citations only when there is a source set to repair against; a
    # source-free explainer should carry no wikilinks, so there's nothing to fix.
    if valid_titles:
        concept_note = _repair_wikilinks(concept_note, valid_titles)
    # Same math-delimiter backstop as research reports (see _run_research).
    concept_note = normalize_math_delimiters(concept_note, escape_currency=True)
    return concept_note


def find_pending_concept_triggers(triggers_path: Path) -> list[Path]:
    """Find concept trigger notes not yet processed (concept: true, not marked done)."""
    pending = []
    if not triggers_path.exists():
        return pending
    for md_file in triggers_path.glob("*.md"):
        try:
            fm, _ = read_note(md_file)
            if fm.get("concept") is True:
                pending.append(md_file)
        except Exception:
            continue
    return pending


def process_concept_trigger(path: Path):
    """
    Generate a concept explainer for a _triggers/ note with concept: true. Writes
    Concepts/Concept - <term>.md, indexes it into a MOC's ## Concepts subsection, and
    marks the trigger concept: done. Skips silently if not a valid concept trigger.
    """
    try:
        fm, body = read_note(path)
    except Exception as e:
        print(f"[concept] Could not read {path.name}: {e}")
        return

    if fm.get("concept") is not True:
        return

    term = (fm.get("term") or "").strip()
    if not term:
        print(f"[concept] Trigger has no term, skipping: {path.name}")
        return
    source_title = (fm.get("source") or "").strip()
    context = body.strip()
    concept_path = _concept_path(term)

    # Run-time dedup: if the concept already exists, don't regenerate — just add this
    # report as a backlink and finish.
    if concept_path.exists():
        print(f"[concept] Already exists, backlinking only: {term}")
        _append_backlink(concept_path, source_title)
    else:
        print(f"[concept] Generating: '{term}'")
        concept_note = _run_concept(term, context, source_title)
        if not concept_note or len(concept_note.strip()) < 100:
            # Leave the trigger pending so the next rescan retries (any queued
            # sources are already vaulted and will be reused).
            print(f"[concept] Empty/failed synthesis for '{term}'; leaving trigger pending")
            return

        note_body = concept_note.rstrip()
        if source_title:
            note_body += f"\n\n{_APPEARS_IN_HEADING}\n- [[{source_title}]]\n"
        write_note(concept_path, {"concept_note": True, "term": term, "date": today()}, note_body)
        print(f"[concept] Written: {concept_path.name}")

        # Index into a MOC under the ## Concepts subsection (cross-list).
        tags, summary = _index_entry(term, concept_note)
        index_note(
            note_title=concept_path.stem,
            note_path=concept_path,
            summary=summary,
            tags=tags,
            analysis=concept_note,
            section="Concepts",
        )

    fm["concept"] = "done"
    fm["completed"] = today()
    write_note(path, fm, f"Concept generated. See [[{concept_path.stem}]].")
    print(f"[concept] Done: '{term}'")
