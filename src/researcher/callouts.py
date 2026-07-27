"""
Inline research callouts.

`> [!research] question` written into any note in the vault is answered in place:
the findings are appended to the same note like a review comment, no separate
note created. Depth is encoded in the callout type ([!research-deep] /
[!research-comprehensive]). Includes the concurrency guards (quiet gate,
tolerant write-back, stale-callout crash recovery) that keep the watchdog from
fighting the user's live editing.
"""

import re
import time
from pathlib import Path

from config import CALLOUT_QUIET_SECS
from notes import read_note, write_note, today

from .pipeline import _run_research


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
