"""
Research trigger notes (_triggers/ with research: true).

Drives a standalone report into Research/: reads the trigger's topic (its title)
and brief (its body), runs the shared pipeline, names the note from the report's
own H1, indexes it, and extracts concepts. Also the naming, readiness-gate, and
depth-checklist helpers specific to trigger notes.
"""

import re
from pathlib import Path

from config import RESEARCH_PATH
from notes import read_note, write_note, safe_filename, today
from indexer import index_note
import telemetry

from .pipeline import _run_research, _index_entry
from .concepts import _conceptualize


# ── Naming a research note ───────────────────────────────────────────────────────

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


# ── Readiness gate & depth checklist ─────────────────────────────────────────────

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


# ── The trigger pipeline ─────────────────────────────────────────────────────────

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

    # Everything below is one dashboard "run": the phases are emitted from inside
    # _run_research, and an exception anywhere marks the run failed (the trigger
    # itself stays pending, so a failed run is retried on the next rescan).
    with telemetry.run("research", topic, meta={"depth": depth}):
        # Provisional name, used only to keep a re-run from feeding a report its own
        # previous version. The real name is derived from the finished report below.
        provisional = fm.get("output") or f"Research - {safe_filename(topic)}.md"

        report, _ = _run_research(topic, depth, seed_urls,
                                  context=brief, context_kind="brief",
                                  exclude_research_title=provisional.replace(".md", ""))

        # Derive real topical tags and a one-line index summary from the report
        # (not the topic's first few words / the report's first 300 characters).
        telemetry.phase("indexing")
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
        telemetry.phase("concepts")
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
