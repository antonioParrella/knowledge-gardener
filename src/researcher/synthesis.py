"""
Phase ④ — write the report and defend its integrity.

Builds the synthesis prompt (an unnumbered source index, plus a distinct
related-work block for prior research), calls the OpenRouter-only `synthesis`
task (draft, or draft→critique→revise for comprehensive depth), then guards the
result: citation repair (`[[21]]`-style dead links → real titles), fused-link
normalisation, and a completeness gate that rejects a report cut off
mid-generation. Shared by research runs and concept runs.
"""

import re

from config import CLIP_CONTENT_LIMIT, load_prompt
from llm import llm_simple


# ── Prompt building ──────────────────────────────────────────────────────────────

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


# ── Citation integrity ───────────────────────────────────────────────────────────

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


# ── Completeness gate ────────────────────────────────────────────────────────────

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
