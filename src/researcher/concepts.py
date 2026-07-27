"""
Concept notes — the learning layer.

A third content type: university-textbook-level explainers of the foundational,
reusable concepts a research report leans on. A cheap conceptualizer pass reads a
finished report, links the concepts into it, and queues a `concept: true` trigger
for each one not already in the vault. The watchdog then runs process_concept_trigger
to write each Concepts/Concept - <term>.md once — reusable by every later report.
"""

import re
from pathlib import Path

from config import (
    CONCEPTS_PATH, TRIGGERS_PATH, CLIP_CONTENT_LIMIT, SYNTHESIS_RAW_EXCERPT,
    MAX_CONCEPTS_PER_REPORT, load_prompt,
)
from notes import read_note, write_note, safe_filename, today, normalize_math_delimiters
from llm import llm_simple, llm_tool_loop, parse_json_response
from web_tools import TOOL_SCHEMA, execute_tool, reset_queue, get_queue
from indexer import index_note, find_relevant_clippings, one_line
import telemetry

from .sources import _process_source
from .synthesis import _build_source_block, _repair_wikilinks, _WIKILINK_RE
from .pipeline import _index_entry


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


def _concept_gloss(body: str) -> str:
    """First prose paragraph of a concept note, collapsed to one line — a short gloss
    of what the concept actually covers. Feeding this (not just the bare name) to the
    extractor lets it tell same-named-but-different concepts apart ('Attention' in ML
    vs psychology) and spot synonyms it would miss from the title alone."""
    para: list[str] = []
    for line in body.split("\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            if para:  # blank/heading after we've started = end of first paragraph
                break
            continue
        para.append(s)
    return one_line(" ".join(para))


def _existing_concept_summaries() -> dict[str, str]:
    """Map each already-explained concept's display term (Concepts/Concept - <term>.md)
    to a one-line gloss from its note. The keys are the terms used for dedup snapping;
    the values disambiguate them for the extractor prompt (see _concept_gloss)."""
    out: dict[str, str] = {}
    prefix = "Concept - "
    for p in sorted(CONCEPTS_PATH.glob("Concept - *.md")):
        if not p.stem.startswith(prefix):
            continue
        term = p.stem[len(prefix):]
        try:
            _, body = read_note(p)
        except Exception:
            body = ""
        out[term] = _concept_gloss(body)
    return out


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
    existing_glosses = _existing_concept_summaries()  # built concept term → one-line gloss
    existing = set(existing_glosses)                   # display terms of built concepts
    pending = _pending_concept_terms()                 # display terms of queued-but-unbuilt

    # Canonical title for every concept already known (built or already queued),
    # keyed aggressively (casing/punctuation-insensitive) so a variant the model
    # returns still maps onto it. Built concepts win over merely-pending ones.
    canonical_by_match: dict[str, str] = {}
    for t in list(pending) + sorted(existing):
        canonical_by_match[_match_key(t)] = t
    pending_keys = {_concept_key(t) for t in pending}

    # List existing concepts to the extractor WITH a one-line gloss, so it reuses the
    # note when it means the same thing and doesn't collide two distinct namesakes —
    # a match/conflict judgment the bare name can't support (see _concept_gloss).
    def _fmt(t: str) -> str:
        g = existing_glosses.get(t, "")
        return f"- {t} — {g}" if g else f"- {t}"
    existing_block = "\n".join(_fmt(t) for t in sorted(existing)) or "(none yet)"

    raw = llm_simple(
        task="moc",
        prompt=load_prompt(
            "concept_extract",
            report=report[:SYNTHESIS_RAW_EXCERPT],
            existing_concepts=existing_block,
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
    telemetry.phase("prior-knowledge")
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
    telemetry.phase("discovery")
    llm_tool_loop(
        prompt=discovery_prompt,
        system=load_prompt("concept_system"),
        tool_schema=TOOL_SCHEMA,
        tool_executor=execute_tool,
        task="research",
    )

    queue = get_queue()
    telemetry.phase("sources", progress=(0, len(queue)))
    newly: list[dict] = []
    for i, entry in enumerate(queue, start=1):
        telemetry.set_detail(entry.get("title") or entry.get("url", ""),
                             progress=(i, len(queue)))
        result = _process_source(entry)
        if result:
            newly.append(result)
    if newly:
        print(f"[concept] Processed {len(newly)} new source(s) into the knowledge base.")

    telemetry.phase("synthesis")
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

    with telemetry.run("concept", term, meta={"source": source_title}):
        # Run-time dedup: if the concept already exists, don't regenerate — just add this
        # report as a backlink and finish.
        if concept_path.exists():
            print(f"[concept] Already exists, backlinking only: {term}")
            telemetry.set_detail("already exists — backlinking only")
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
            telemetry.phase("indexing")
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
