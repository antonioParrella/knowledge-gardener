# Design Notes — why the system is built the way it is

`AGENTS.md` describes **what** the system does and **how** to operate it. This
file holds the **why**: the bugs that motivated each defensive mechanism, the
history behind non-obvious choices, and the residual edge cases that remain. It
is not loaded every session — reach for it when you're changing one of these
subsystems and need to know what a guard is protecting against before you touch it.

A recurring pattern runs through most of these: **a prompt instruction alone does
not hold against a strong stylistic prior in the model, so it needs a
deterministic backstop.** Citation format, math delimiters, and MOC granularity
are all instances of it.

---

## Sync — why the vault must not live on a file-level sync service

The vault was originally on iCloud Drive, and iCloud conflict-copies any file two
devices write concurrently. Obsidian rewrites `workspace.json` on nearly every
pane change and `community-plugins.json` on every plugin toggle, so those files
were renamed away constantly. The end state: **1,239 conflict copies** inside
`.obsidian/` (1,225 of them `workspace N.json`), and **no canonical
`workspace.json` or `community-plugins.json` at all**. With the enabled-plugins
file permanently destroyed, every community plugin silently reverted to disabled
on each launch — Templater could never stay on — and the constant sync churn made
the iPhone crawl.

This is not only a two-device race: with Obsidian fully closed on both devices, a
locally written `community-plugins.json` was still renamed to
`community-plugins 10.json` within about a second.

Obsidian Sync is used instead because it understands Obsidian's write patterns.
When syncing settings, **"Active community plugin list" and "Installed community
plugins" must both be enabled** — either one alone leaves the phone knowing a
plugin should be on but not having it installed.

Consequences that live on in the operational doc: Obsidian Sync only runs while
Obsidian is **open**, so Obsidian must stay running on the Surface for
phone-written triggers to reach the watchdog; and `PDF_INBOX_PATH` is deliberately
still on iCloud because it is outside the vault, a low-churn drop folder, so none
of the above applies.

The vault used to be slow enough on iCloud that a recursive grep/glob over it
could time out. On local disk that is no longer true — so a slow scan now means
something is actually wrong rather than being expected.

---

## Citation integrity — keeping `[[wikilinks]]` real

Reports cite sources as `[[wikilinks]]` that must match a note title **exactly**;
anything else is a dead link in Obsidian. Synthesis can drift out of that
convention and into the numbered-reference style of the papers it is summarising —
one report came back citing `[[21]]`, `[[27]]` throughout, every link dead.
Notably it was *not* the report with the most sources (a 39-source report cited
cleanly); the trigger appears to be the genre of the corpus, which makes it a
matter of luck rather than load.

Four mechanisms defend against it (`synthesis.py`):

1. **No ordinals in the prompt.** `_build_source_block` /
   `_build_prior_research_block` list sources as `- [[Title]]`, never
   `1. [[Title]]`. If the model is never shown a number, it has none to substitute
   for the title.
2. **An explicit ban.** `research_synthesis` and `research_callout` both state that
   citing by number is always wrong, however strongly the sources' own style
   suggests it.
3. **A repair pass, not a warning.** `_repair_wikilinks()` collects every link that
   doesn't match a real title and, if any exist, hands the report back to the model
   with the valid titles (`research_repair_links`) to resolve them from context;
   unresolvable ones are dropped rather than left dead. The repair is only accepted
   if it actually reduces the invalid-link count and doesn't shrink the report — a
   mangled repair must never clobber good prose. This replaced `_validate_wikilinks()`,
   which only printed a warning and wrote the broken report to the vault anyway.
4. **Malformed-link normalisation.** Synthesis also fuses adjacent citations into
   one pair — `[[A], [B]]` instead of `[[A]], [[B]]` — which the old detector's
   regex couldn't even see (it required a bracket-free interior).
   `_normalize_fused_wikilinks()` splits them deterministically before the repair
   pass, and `_find_bad_wikilinks()` now flags any link with a stray interior
   bracket, so nothing malformed slips through.

### Keeping a truncated report out of the vault

Two backstops. Both providers raise on a cut-off generation — OpenRouter on
`finish_reason == "length"`, Gemini on `MAX_TOKENS` (`_extract_text`) — so a
truncated call errors / fails over instead of returning half a report. Because a
fail-over target could truncate too, `_assert_report_complete()` is the final
gate: a report still ending mid-sentence is rejected before it is written or
indexed, and the trigger stays pending to retry. The one truncated report we found
ended "…difficult for Israeli", so the gate judges completeness by the **last
character** (mid-word or a dangling connector), not by length. Synthesis is also
routed OpenRouter-only (`synthesis` task), so reports are never written by Gemini.

An interrupted/crashed run writes no report and leaves the trigger `research: true`
(retried next rescan). A report that *exists* but ends mid-sentence with its
trigger `research: done` was the silent-truncation failure that slipped through the
success path — that is what `_assert_report_complete()` now blocks.

---

## Math rendering — `$…$` vs LaTeX brackets

Obsidian's MathJax only renders `$…$` / `$$…$$`; the synthesis model, trained on
papers, habitually emits standard LaTeX `\( … \)` / `\[ … \]` despite the prompt
asking for the dollar form — so math-heavy reports render as literal brackets.
Same shape of problem as citation integrity: a prompt instruction alone doesn't
hold against a strong stylistic prior, so it needs a deterministic backstop.

`notes.normalize_math_delimiters()` is that pass, run in `_run_research` and
`_run_concept` right after `_repair_wikilinks` (link-only) and before the
completeness gate, so every research / callout / concept report is normalised
before it is written. It converts `\(…\)` → `$…$` and — only on a **display-shaped
line** (the delimiter alone on its line, or the whole line wrapped,
`_is_display_line`) — `\[…\]` → `$$…$$`. Display conversion is line-anchored
because `\[` is ambiguous: in LaTeX it opens display math, in markdown it's an
*escaped literal bracket* (`interval \[a, b\]`), and only a display-shaped line is
unambiguously the former. Fenced (``` / ~~~) and inline `` `code` `` spans are
skipped, so `arr\[i\]` in code is never touched.

### The currency subtlety

A `$5` next to freshly-written math `$…$` makes MathJax pair the two and swallow
the prose between them (the real trigger: a Conformal report with "bet $1 … lose
\(\alpha\)" on one line). So the pass also escapes a currency `$` (signature: `$`
immediately followed by a digit) to `\$` — but **only on a line where it is also
converting a bracket**, i.e. the one place it introduces a new `$` to collide with.
A line already in correct dollar form (no brackets) is left completely untouched,
so an existing `$1 - \alpha$` (a confidence level) or `$2^n$` is never corrupted —
escaping where there's nothing to collide with could only do harm. Escaping runs
*before* the bracket→dollar conversion on that line, so the delimiters it just
wrote are never re-escaped.

The prompt side is hardened too (belt to the deterministic suspenders):
`research_synthesis`, `research_callout` and `concept_synthesis` explicitly ban
`\(…\)` / `\[…\]` and require escaped `\$` amounts, and the Research Trigger
template carries a standing formatting acceptance-criterion. `lint.py`'s
`unrendered_math` check (and `--fix`) flags any Research/Concepts note the
normaliser would still change — the detector *is* the fixer, so lint and the fix
can't disagree, and a correct dollar-only note (even with currency) is never a
false positive. `fix_math_delimiters.py` is the reviewable one-off backfill
(dry-run, then `--apply`) for reports written before the guard existed.

**Residual:** a single line that mixes *both* `$…$` and `\(…\)` styles where the
dollar-span starts with a digit — its opening `$` gets escaped. It needs the model
to use two delimiter conventions on one line, which the prompt ban pushes against;
the `lint` check surfaces whatever slips through.

---

## Rejecting non-content — the `usable` gate

A research fetch doesn't always return the article. A PDF can download fine yet
extract to nothing (subsetted fonts, scanned images); an anti-scraping page
(Cloudflare, reCAPTCHA, Anubis) or a paywall/login wall returns HTTP 200 with an
interstitial instead of the paper. Left unchecked, that garbage became a real,
indexed, citable clip — and worse, feeding a bot-wall page to the analyzer made it
*hallucinate* a plausible-looking summary from the title alone. Three layers now
stop this, cheapest first:

1. **`fetch_url` refuses non-HTML/binary responses.** It checks `Content-Type` and
   sniffs `%PDF-` magic bytes; a PDF (or other binary) served at a URL is not web
   text, so it returns the `Failed to fetch` sentinel rather than decoding raw
   bytes into "text". Deterministic — no false positives on real articles.
2. **`extract_paper_text` never re-fetches the PDF as its own landing page.** The
   landing-page fallback is skipped when the landing URL equals the PDF URL or is
   itself a `.pdf` — re-fetching it could only ever return bytes. (This was the
   exact bug behind the `%PDF-…endobj…stream` byte-dump clips.)
3. **The clip analyzer decides the semantic cases.** `clip_analysis` returns a
   `usable` boolean; the model — which already reads the content — sets it `false`
   for binary dumps, CAPTCHA/paywall/JS interstitials, and error pages, and `true`
   for genuine content (even a thin abstract). This is the only reliable test for
   interstitials: keyword-matching "reCAPTCHA"/"proof-of-work" would false-positive
   on a real article *about* those topics. `clipper.py` discards a `usable: false`
   stub (`pdf_processor.py` archives it), and `_process_source` falls back to the
   abstract.

**Backfill.** The gate only prevents *future* junk; clips saved before it were
removed with `clean_junk_clips.py`, a one-off reviewable pass. It classifies each
clip from its captured `## Original Content` (raw-PDF tokens, mojibake ratio, short
pages carrying bot-wall markers) and the analyzer's own failure admissions, prints
every candidate for review, and — with `--apply` — deletes them and reuses
`reset_clips`' MOC surgery so each `[[link]]` is stripped, `note_count`
decremented, and emptied MOCs deleted. Abstract-only clips (a real abstract under a
warning callout) are never flagged. Report/concept notes that cite a removed clip
are reported, not auto-edited (report prose is the user's).

---

## Duplicate prevention — the overnight-duplicates origin

`clipper.py` checks for existing notes with the same `source` URL before processing
a new clip (`find_existing_source`). If found, the new clip is deleted and no LLM
call is wasted. The check passes `exclude=path` so it can't match the note against
itself — the first glob match is otherwise often the file being processed, and a
self-match let real duplicates through (a research stub whose title sorted before
its twin sailed past the `existing != path` guard — the overnight-duplicate origin).

Research source clips get a second layer: `sources._clip_source` deletes its stub
whenever `process_clipped_note` returns `None` (discard, JSON-parse failure, or
full-text→abstract fallback). An abandoned `processed: false` stub would otherwise
be re-ingested by the watchdog's backlog scan as a second clip for the same source.

The linter's duplicate-source `--fix` chooses the keeper by `_clip_quality`
(**full_text over abstract-only, processed over unprocessed, larger body as
tiebreaker**), not file age. The old "delete newest" rule dropped the good copy of
a research duplicate — the abstract stub is written first, the full text indexed
later.

---

## MOC granularity — why "be consistent" is deliberately absent

Each MOC should be a **specific sub-field, not a broad domain** — `MOC - Generative
Models`, not `MOC - AI`. The assignment prompt (`assign_to_moc()` in `indexer.py`)
gives Gemini sub-field examples and explicitly calls out broad labels as too
coarse. Critically, it does **not** tell Gemini to "be consistent with existing
names" — that instruction used to create a feedback loop where every new note piled
into whichever big MOC already existed (e.g. a single 40-note `MOC - AI`). Acronyms
(AI, LLM, ML, RL…) are kept uppercase by `_titlecase_topic()`.

This is the exact opposite of the tag rule below, and the contrast is intentional:
for MOCs we *avoid* consistency pressure to prevent one giant catch-all; for tags
we *want* cross-cutting reuse.

**Entry summaries are one line, always.** An entry is a single markdown list item,
so a multi-line summary splits the list and breaks the MOC. Research notes used to
pass `report[:300]` — a raw prefix that dragged the report's H1 and opening
paragraphs into the list item. `indexer.one_line()` now flattens and clamps
whatever a caller passes, at the point the entry is written, so no future caller can
reintroduce this.

**Why the linter counts `## Concepts` too.** MOC checks count entries under both
`## Notes` and `## Concepts` (`_moc_entry_links`), since `indexer` increments
`note_count` for concept explainers too; counting only `## Notes` made every MOC
with a concept note read as a false mismatch.

---

## Tags — fighting vocabulary fragmentation

Left unmanaged, an LLM tags freely and the vault fragments into near-duplicates
that don't connect when you filter — real examples from this vault:
`machinelearning` / `machine-learning`, `llm` / `llms` / `languagemodels`,
`sports-betting` / `sportsbetting` / `sportsbook`, `wealth-tax` / `wealthtax`. Two
mechanisms keep tags consistent:

1. **Deterministic normalisation** (`notes.normalize_tag` / `normalize_tags`) —
   every tag is canonicalised to lowercase-hyphenated: casing, a leading `#`, and
   separators (spaces / underscores / slashes → hyphen). Pure string hygiene; it
   does *not* merge synonyms or split concatenations.
2. **A canonical vocabulary** (`Index/_tags.md`) — a hand-editable list fed into the
   tagging prompts via `indexer.format_tag_vocabulary()`. The prompts instruct the
   model to reuse an existing tag whenever it means the same thing (use
   `machine-learning`, don't coin `ml`) and only coin a new one when nothing fits,
   while still keeping tags specific. New tags are appended by
   `indexer.register_tags()`, which every note flows through inside `index_note()`.

`consolidate_tags.py` is the two-step backfill (dry-run proposes `raw→canonical` in
`tag_map.json` for review; `--apply` rewrites frontmatter from the reviewed map
without re-calling the model). Any tag missing from the map keeps its normalised
self, so nothing is silently lost.

---

## Naming — the "Untitled" story

`output:` in a trigger is optional and only pins an exact filename. Without it the
note is named from the finished report's own H1 — synthesis writes a far better
title than a trigger keyword ("Research - World Models and Their Origins" rather
than "Research - World Models"), and that title used to be discarded. A subtitle
after `:` / `–` is trimmed; only if the report has no H1 does the topic (the note's
title) become the name.

The trigger note's **title is the topic** — the old `topic:` frontmatter field was
removed as redundant with the Details brief. A trigger created from the phone's +
button and left at Obsidian's default carries no topic in its title, which named a
run `Research - Untitled` that also searched prior knowledge for the literal string
"Untitled". An empty or `Untitled*` title now falls back to the brief, where the
actual question is.

A completed trigger is also renamed (`_renamed_trigger_path`) to read as the report
it generated, with the `Research - ` prefix stripped so it never *shares* a basename
with the report — a duplicate basename makes `[[wikilinks]]` ambiguous in Obsidian.

---

## Concept dedup — one concept, one note, linked everywhere

The dedup that makes concepts cumulative rather than duplicative has three guards:

1. The conceptualizer skips terms already built (Concepts/ glob) or already queued
   (pending triggers).
2. A returned term is **snapped onto the canonical title** of an existing/pending
   concept via `_match_key` — a casing- *and* punctuation-insensitive key, so
   `Chamley-Judd Theorem` (hyphen) maps onto an existing `Chamley–Judd Theorem`
   (en-dash) note instead of minting a dead link and a duplicate.
3. `process_concept_trigger` re-checks at run time, adding a backlink instead of
   regenerating.

Mechanical drift (casing, spacing, punctuation) is caught deterministically. A
genuine *synonym* — different words for the same idea — relies on the model, but it
now decides from each existing concept's one-line gloss rather than its bare name
(`_existing_concept_summaries` → `_concept_gloss` feeds the `concept_extract`
prompt), so it can recognise a same-meaning note under a different name and,
conversely, keep two different concepts that share a name apart by disambiguating
the new one's `term` (e.g. `Attention (machine learning)`). The residual is a true
synonym the model still fails to connect from the gloss.

Concept generation deliberately uses the best model (`synthesis`, OpenRouter-only,
no free-tier fallback) because the explainer's quality is what matters; the dedup
guarantee is what stops this compounding, since each concept is paid for exactly
once and thereafter only linked. Only standalone Research/ reports are
conceptualized — inline `[!research]` callouts are not (they annotate arbitrary
notes in place).

---

## Callout concurrency — not fighting the user's editor

The watchdog writes the host note from a separate process, so several guards keep
it from fighting live editing:

- **Quiet gate.** `find_research_callouts` skips any note modified within
  `CALLOUT_QUIET_SECS` (45s, < the 60s rescan) — a note you're actively typing in
  is left alone and picked up once you pause.
- **Robust write-back.** The final marker swap matches the in-progress line by topic
  with a tolerant regex (any depth label, any `.`/`…` form), so ellipsis or
  whitespace drift during the run can't miss it. If the marker was removed/edited
  away entirely, the `> [!done]` result is **appended** rather than silently
  dropped — a finished report is never lost.
- **Crash recovery.** A callout stranded at `> [!info] Researching…` on a note
  untouched for `STALE_CALLOUT_SECS` (30 min) — a crashed run, or OpenRouter down —
  is reverted to `> [!research…]` (original depth preserved) by
  `revert_stale_callouts()` each rescan, so it retries. This is the callout analogue
  of pending-trigger recovery.

Depth is encoded in the callout *type* (`[!research-deep]` /
`[!research-comprehensive]`) so it stays one-tap insertable and still renders as a
normal callout (the question is the visible title). `_CALLOUT_RE` captures the
suffix (`-` or `|` separator both accepted).

---

## Pipeline serialization — the rescan/run race

All pipeline work is serialized through one lock (`PIPELINE_LOCK` in
`obsidian_watchdog.py`). Without it, a research run writing source clips to
Clippings/ races the 60s periodic rescan, which can process-and-rename the same
clip first and crash the research run mid-flight (`FileNotFoundError` on rename).

A trigger stays `research: true` until a run completes, so missed file events and
crashed runs are retried automatically by the rescan and the startup backlog drain.

## Provider exhaustion — cooldowns and the research preflight gate

Two different "we're out of budget" failures used to behave very differently, and
one of them wedged the system.

**The bug.** An OpenRouter key can carry its own spend cap, independent of the
account balance. When it's hit, every call returns `403 – Key limit exceeded
(total limit)`. The provider wrapped *all* OpenRouter errors as `ProviderError`,
which the router treats as "fall through, no cooldown". So a research trigger —
which never gets marked done on failure and is re-checked every 60s rescan —
retried the doomed key forever, and each attempt also spent a Gemini request,
pinning the free tier so it could never recover. One comprehensive run
(`Stimulants Use`, 2026-07-23) looped ~30 times in 25 minutes doing nothing.
Gemini's 429 was quiet the whole time because it correctly raised
`QuotaExhausted` and went on the circuit-breaker cooldown; OpenRouter never did.

**Fix 1 — classify the cap as quota, not error.** `openrouter._classify()` maps a
402 (out of credits) or a 403 "Key limit exceeded" to `QuotaExhausted`, so the
existing cooldown in `llm.py` parks the model for `QUOTA_COOLDOWN_SECS` instead of
hammering it. A non-cap 403 (bad key) stays `ProviderError` — we don't want to
park a genuinely broken key as if a top-up would fix it.

**Fix 2 — preflight the OpenRouter-only runs.** Research/concept/callout synthesis
has *no* Gemini fallback, so gathering sources then dying at synthesis is pure
waste. `llm.research_preflight()` polls the key's live `limit_remaining`
(`GET /api/v1/key`) before a run and blocks it if credit is below
`RESEARCH_MIN_KEY_CREDITS`. It blocks **only** on a confident low reading — an
uncapped key, an unset key, or an unreachable `/key` all fail *open*, because a
transient blip must not wedge all research (a real outage is still caught by the
cooldown at call time). The reading is cached `PREFLIGHT_CACHE_SECS` so a rescan
batch polls once, not per note.

**How the two reconcile, and how a run resumes.** The cooldown is a blind 30-min
timer set on an actual failure; the preflight is a live reading. If they
disagreed, a just-raised limit would still be ignored until the timer expired — so
a *healthy* preflight reading clears the OpenRouter cooldown (positive live
evidence beats the stale timer). Because a blocked trigger is left **pending**
(never marked done/failed), resume needs no manual step: the next rescan re-polls
`/key`, sees credit restored, clears the cooldown, and runs the same trigger —
automatic, within one rescan interval.

## Logging — why `print()` goes through the logger

The whole pipeline diagnoses itself through `print()` (provider fall-through, the
`[research]` phase trace, tool calls, errors). A plain `FileHandler` only captured
the watchdog's own `log.*` calls, so the persistent log was near-useless for
diagnosing a run. The watchdog therefore (1) attaches rotating file + console
handlers bound to the *real* stdout, then (2) redirects `sys.stdout`/`stderr` into
the logger so every `print()` is persisted, timestamped, and flushed per line — the
log is complete even if the process is killed mid-run. The log is
`VAULT_PATH.parent/watchdog.log`, beside the vault, **not** in the repo.
