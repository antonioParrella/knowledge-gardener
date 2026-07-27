"""
Shared research orchestration — the four-phase core plus the MOC index entry.

`_run_research` runs phases ①–④ for a topic and is shared by both trigger notes
(triggers.py) and inline callouts (callouts.py); `_index_entry` derives a note's
tags + one-line MOC summary and is shared by research and concept triggers.
"""

import re

from config import CLIP_CONTENT_LIMIT, SYNTHESIS_RAW_EXCERPT, load_prompt
from notes import normalize_tags, normalize_math_delimiters
from llm import llm_simple, llm_tool_loop, parse_json_response
from web_tools import TOOL_SCHEMA, execute_tool, reset_queue, get_queue
from indexer import (
    find_relevant_clippings, find_relevant_research, format_tag_vocabulary, one_line,
)
import telemetry

from .sources import _process_source
from .synthesis import (
    _synthesise, _build_source_block, _repair_wikilinks, _assert_report_complete,
)


# ── Phase ② helper — discovery prompt ────────────────────────────────────────────

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


# ── Best-effort prior knowledge ──────────────────────────────────────────────────

def _best_effort(label: str, fn, *args, **kwargs) -> list:
    """
    Run a phase ① lookup, degrading to "found nothing" instead of failing the run.

    Phase ① is an optimisation: it surfaces what the vault already knows so the
    report can build on it rather than re-derive it. Losing that costs quality;
    losing the whole run costs everything. On 2026-07-27 a truncated
    MOC-assignment call on the OpenRouter fallback (see config § CHEAP_MAX_OUTPUT_TOKENS)
    raised out of find_relevant_clippings and killed a comprehensive callout nine
    minutes in, before it had gathered a single source.

    Every downstream consumer already handles an empty list — that's the no-prior-
    knowledge case a fresh vault hits on its first run.
    """
    try:
        return fn(*args, **kwargs) or []
    except Exception as e:
        print(f"[research] Prior-knowledge lookup failed ({label}): {e} — continuing without it")
        return []


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
    telemetry.phase("prior-knowledge")
    existing = _best_effort("clippings", find_relevant_clippings, topic)
    if existing:
        print(f"[research] Found {len(existing)} relevant existing clipping(s).")
    prior_research = _best_effort("prior research", find_relevant_research, topic,
                                  exclude_title=exclude_research_title)
    if prior_research:
        print(f"[research] Found {len(prior_research)} related prior research report(s).")

    # ② Discovery tool loop
    telemetry.phase("discovery")
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
    queue = get_queue()
    telemetry.phase("sources", progress=(0, len(queue)))
    newly: list[dict] = []
    for i, entry in enumerate(queue, start=1):
        telemetry.set_detail(entry.get("title") or entry.get("url", ""),
                             progress=(i, len(queue)))
        # One bad source must not discard the other twenty-two. A fetch, an
        # analysis call, or a MOC assignment can fail for reasons specific to
        # this one document; the run continues with the sources that did work.
        try:
            result = _process_source(entry)
        except Exception as e:
            print(f"[research] Source failed, skipping ({entry.get('url', '')}): {e}")
            continue
        if result:
            newly.append(result)
    print(f"[research] Processed {len(newly)} new source(s) into the knowledge base.")

    # ④ Synthesis
    telemetry.phase("synthesis")
    all_sources = existing + newly
    report = _synthesise(topic, all_sources, depth,
                         context=context, system_name=synthesis_system_name,
                         context_kind=context_kind, prior_research=prior_research)
    telemetry.phase("citations")
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


# ── MOC index entry (tags + one-line summary) ────────────────────────────────────

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
