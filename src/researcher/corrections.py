"""
Phase ⑤ — correct the host note in place.

A `> [!research]` callout is often not a question but an objection: "this is wrong
because…". Appending an answer under the claim it refutes leaves the wrong claim
standing, and the note ends up arguing with itself. This module lets the answer
edit the note's own prose.

The mechanism is the one coding agents use — an exact-match, unique-match edit tool
driven in a loop, where a failed match is a loud error the model repairs on the next
iteration. Two properties make it safe to run unattended:

  * **The model never touches disk.** `edit_note` mutates an in-memory working copy.
    The gates in `verify_edits` decide whether that copy is ever written, and a
    rejection discards the whole set — edits are all-or-nothing.
  * **Failure is best-effort.** Any problem here leaves the note unedited and the
    answer is still appended, exactly as before this phase existed.

Prior answer blocks are protected: they are a record of what was said and when, so
the executor refuses an edit landing inside one.
"""

import re
from dataclasses import dataclass

from config import (
    CALLOUT_EDITS_ENABLED, CALLOUT_MIN_LENGTH_RATIO, CORRECTION_DOC_LIMIT,
    MAX_CALLOUT_EDITS, MAX_CORRECTION_ATTEMPTS, load_prompt,
)
from notes import normalize_math_delimiters
from llm import llm_tool_loop
import telemetry


# ── Answer-block sentinels ───────────────────────────────────────────────────────
# Answer blocks are delimited by HTML comments: invisible in Obsidian's reader, and
# an exact anchor for the protected-span check. A heuristic over "> [!done]" plus
# trailing prose has no reliable terminator; this does.
ANSWER_OPEN  = "<!-- kg:answer -->"
ANSWER_CLOSE = "<!-- /kg:answer -->"

_ANSWER_BLOCK_RE = re.compile(
    re.escape(ANSWER_OPEN) + r".*?" + re.escape(ANSWER_CLOSE), re.DOTALL
)
_IN_PROGRESS_RE = re.compile(r"^>\s*\[!info\]\s*Researching.*$", re.MULTILINE)

_WIKILINK_RE = re.compile(r"\[\[(.+?)\]\]")
_HEADING_RE = re.compile(r"^#{1,6} .*$", re.MULTILINE)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges no edit may touch: prior answer blocks and the live marker."""
    spans = [m.span() for m in _ANSWER_BLOCK_RE.finditer(text)]
    spans += [m.span() for m in _IN_PROGRESS_RE.finditer(text)]
    return spans


def _overlaps_protected(text: str, start: int, end: int) -> bool:
    return any(not (end <= s or start >= e) for s, e in _protected_spans(text))


def _wikilinks(text: str) -> set[str]:
    """Link targets in `text`, alias and heading suffixes stripped."""
    return {
        raw.strip().split("|", 1)[0].split("#", 1)[0].strip()
        for raw in _WIKILINK_RE.findall(text)
    }


def _headings(text: str) -> list[str]:
    """The note's heading lines, in order — its structural skeleton."""
    return _HEADING_RE.findall(text)


# ── Run state ────────────────────────────────────────────────────────────────────
# Mirrors web_tools' queue: module-level state for the current run, reset by the
# caller before the loop and read back after it.

_working: str = ""
_edits: list[dict] = []


def reset_edits(body: str) -> None:
    """Start a correction attempt from a pristine copy of `body`."""
    global _working, _edits
    _working, _edits = body, []


def get_edits() -> tuple[str, list[dict]]:
    """The working copy and the changelog for the attempt just run."""
    return _working, list(_edits)


# ── The edit tool ────────────────────────────────────────────────────────────────

def edit_note(old_string: str, new_string: str, why: str) -> str:
    """
    Replace one passage in the working copy.

    Every rejection returns a message rather than raising: these strings are the
    model's only feedback channel, and they are what turns a mismatched character
    from a design problem into a loop iteration.
    """
    global _working

    if len(_edits) >= MAX_CALLOUT_EDITS:
        return (
            f"Edit limit reached ({MAX_CALLOUT_EDITS}). Make no further edits — "
            "reply with a one-line summary of what you changed."
        )
    if not old_string:
        return "old_string is empty. It must be the exact text you want replaced."
    if old_string == new_string:
        return "No change: old_string and new_string are identical."

    count = _working.count(old_string)
    if count == 0:
        return (
            "Not found. Copy the text to replace *exactly* from the document, "
            "character for character — including punctuation and any unusual "
            "dashes, hyphens, or quote marks."
        )
    if count > 1:
        return (
            f"Ambiguous: that text appears {count} times. Include more of the "
            "surrounding sentence so the match is unique."
        )

    start = _working.index(old_string)
    if _overlaps_protected(_working, start, start + len(old_string)):
        return (
            "That text is inside a previous research answer. Those are a record of "
            "what was said and must not be edited — correct the note's own prose "
            "instead."
        )

    # Scoped to the replacement, so pre-existing prose is never rewritten as a
    # side effect of one edit.
    new_string = normalize_math_delimiters(new_string, escape_currency=True)

    _working = _working.replace(old_string, new_string, 1)
    _edits.append({"old": old_string, "new": new_string, "why": why})
    return f"Replaced. ({len(_edits)}/{MAX_CALLOUT_EDITS} edits used.)"


TOOL_SCHEMA = [
    {
        "name": "edit_note",
        "description": (
            "Replace a passage of the document with a corrected version. "
            "old_string must appear EXACTLY ONCE in the document — copy it verbatim. "
            "Use this ONLY for claims your answer contradicts, refutes, or supersedes. "
            "Do not edit for style, do not add claims your answer does not support, and "
            "do not touch text inside a previous research answer. Making no edits at "
            "all is the correct outcome for most questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "old_string": {
                    "type": "string",
                    "description": (
                        "The exact text to replace, copied verbatim from the document "
                        "and long enough to be unique."
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": (
                        "The corrected text. Preserve any [[wikilinks]] and $math$ it "
                        "contains, and keep it close in length to the original."
                    ),
                },
                "why": {
                    "type": "string",
                    "description": "One sentence: what was wrong with the original.",
                },
            },
            "required": ["old_string", "new_string", "why"],
        },
    },
]


def execute_tool(name: str, args: dict) -> str:
    """Dispatch a tool call by name. Called by the correction loop."""
    if name == "edit_note":
        return edit_note(
            args.get("old_string", ""),
            args.get("new_string", ""),
            args.get("why", ""),
        )
    return f"Unknown tool: {name}"


# ── Gates ────────────────────────────────────────────────────────────────────────

@dataclass
class Rejection:
    """Why an edit set was refused. `fatal` means retrying will not help."""
    reason: str
    fatal: bool = False


def verify_edits(original: str, edited: str, allowed_titles: set[str]) -> Rejection | None:
    """
    Whole-document invariants, checked before anything is written.

    These stand in for the compiler and test suite a coding agent leans on. They
    cannot tell you a corrected paragraph is *wrong* — nothing can, cheaply — but
    they reliably catch the model regenerating the document instead of patching it.

    The two structural failures are fatal: a model that rewrote the note's skeleton
    has misread the task, and feedback does not fix a misread. An invented link is
    mechanical, and telling the model which link is dead does fix it.
    """
    if _headings(original) != _headings(edited):
        return Rejection(
            "the edits changed the note's heading structure — correct claims inside "
            "paragraphs, never add, remove, or reword a heading",
            fatal=True,
        )
    if len(edited) < len(original) * CALLOUT_MIN_LENGTH_RATIO:
        return Rejection(
            f"the edits removed too much of the note ({len(original)} → {len(edited)} "
            "characters) — a correction rewrites claims, it does not delete sections",
            fatal=True,
        )
    # Only *newly introduced* links are checked. The note is full of pre-existing
    # citations that have nothing to do with this run's sources; validating those
    # against `allowed_titles` would flag every one of them.
    invented = _wikilinks(edited) - _wikilinks(original) - allowed_titles
    if invented:
        listed = ", ".join(f"[[{t}]]" for t in sorted(invented)[:5])
        return Rejection(
            f"these links do not exist as notes: {listed}. Cite only titles from the "
            "source index, or links already present in the document."
        )
    return None


# ── Attempt loop ─────────────────────────────────────────────────────────────────

def _build_correction_prompt(question: str, answer: str, body: str,
                             feedback: str = "") -> str:
    parts = [
        f"# The question asked in the callout\n{question}",
        f"# The answer that was just researched\n{answer}",
        (
            "# The document to correct\n"
            "This is the full note the callout was written into. Regions between "
            f"`{ANSWER_OPEN}` and `{ANSWER_CLOSE}` are previous answers — read them "
            "for context, but they are a record and cannot be edited.\n\n"
            + body
        ),
    ]
    if feedback:
        parts.append(
            "# Your previous attempt was rejected\n"
            f"{feedback}\n\n"
            "The document above is back to its original state. Try again, making "
            "smaller and more targeted edits."
        )
    parts.append(
        "Decide whether the answer contradicts or supersedes anything the document "
        "claims. If it does, call edit_note for each affected passage. If it does "
        "not, make no edits and say so."
    )
    return "\n\n".join(parts)


def apply_corrections(question: str, answer: str, body: str,
                      allowed_titles: set[str]) -> tuple[str, list[dict], str | None]:
    """
    Offer the note for in-place correction. Returns (body, edits, rejection_reason).

    On any refusal — disabled, oversized, no edits wanted, gate rejection, or an
    outright failure — the original `body` comes back unchanged with an empty
    changelog, and the caller appends the answer as normal. Correcting the note is
    an improvement on top of answering it, never a precondition for it.
    """
    if not CALLOUT_EDITS_ENABLED:
        return body, [], None
    if len(body) > CORRECTION_DOC_LIMIT:
        print(f"[research] Note is {len(body)} chars (limit {CORRECTION_DOC_LIMIT}); "
              "skipping in-place correction.")
        return body, [], None

    feedback = ""
    for attempt in range(1, MAX_CORRECTION_ATTEMPTS + 1):
        reset_edits(body)
        try:
            llm_tool_loop(
                prompt=_build_correction_prompt(question, answer, body, feedback),
                system=load_prompt("research_correct"),
                tool_schema=TOOL_SCHEMA,
                tool_executor=execute_tool,
                task="synthesis",          # OpenRouter-only: never let a fallback edit the vault
                max_iterations=MAX_CALLOUT_EDITS + 3,
            )
        except Exception as e:
            print(f"[research] Correction pass failed ({e}); leaving the note unedited.")
            return body, [], None

        working, edits = get_edits()
        if not edits:
            print("[research] No corrections needed.")
            return body, [], None

        rejection = verify_edits(body, working, allowed_titles)
        if rejection is None:
            print(f"[research] Corrected {len(edits)} passage(s) in place.")
            return working, edits, None

        print(f"[research] Correction attempt {attempt}/{MAX_CORRECTION_ATTEMPTS} "
              f"rejected: {rejection.reason}")
        if rejection.fatal:
            print("[research] Rejection is not retryable; leaving the note unedited.")
            return body, [], rejection.reason
        if attempt == MAX_CORRECTION_ATTEMPTS:
            return body, [], rejection.reason
        feedback = rejection.reason
        telemetry.set_detail(f"retrying corrections ({attempt + 1}/{MAX_CORRECTION_ATTEMPTS})")

    return body, [], None  # unreachable; MAX_CORRECTION_ATTEMPTS >= 1
