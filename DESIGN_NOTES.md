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

## Full-text recovery — the open-access ladder

The `usable` gate above keeps garbage *out*. This is the opposite problem: real,
freely-available articles that never got *in*.

**The measurement.** 299 of 650 research-gathered clips (46%) were `full_text:
false`. That is not a paywall story. The failed-source domains were led by
`pmc.ncbi.nlm.nih.gov` (24) — PubMed Central, which is 100% open access — plus 17
bare `doi.org` links and 6 arXiv URLs. A random 30-clip sample was probed against
nine candidate retrieval strategies and each result *graded*, not counted:

| route | good | thin | wrong | precision |
|---|---|---|---|---|
| Europe PMC `fullTextXML` | 5 | 0 | 0 | **100%** |
| NCBI BioC (PMC OA) | 5 | 0 | 0 | **100%** |
| browser User-Agent retry | 4 | 1 | 2 | 57% |
| Unpaywall → OA copy | 2 | 2 | 0 | 50% |
| OpenAlex `locations[]` | 3 | 4 | 0 | 43% |
| title → identifier | 1 | 0 | 3 | 25% |
| reader proxy (Jina) | 2 | 3 | 4 | 22% |

Grading mattered enormously. Counting bytes would have scored the bottom two rows
as successes: they returned a 404 page rendered as prose, a publisher homepage, and
— for an L-theanine meta-analysis — a PubMed page about *splanchnic nerve anatomy*.

**Four causes, in order of how much they cost.**

1. **The pipeline threw away identifiers it already held.** `queue_source()` kept
   only the URL, while the search result that produced it knew the DOI. 55% of the
   failed clips carry no identifier in their URL at all, so they can only be
   re-resolved by title search — the 25%-precision route. `queue_source` now takes
   `doi` / `landing_url`, `_format_academic_results` shows the DOI so the agent can
   pass it on, and `_clip_source` persists whatever was resolved into frontmatter.
   Every clip written from here on stays cheaply recoverable, *including* the
   abstract-only ones.
2. **The landing-page fallback was unreachable.** `_process_source` called
   `extract_paper_text(url, landing_url=url)`, and the guard added for the
   byte-dump bug (§ Rejecting non-content, layer 2) is `landing_url != pdf_url` —
   always false. Every `kind="pdf"` source whose download failed had *no* fallback.
3. **PMC is bot-walled at the HTML door and wide open at the API door.** Both
   100%-precision routes are free and key-less.
4. **A bot User-Agent gets 403'd** by many publishers. Left alone deliberately —
   that is a separate call about how hard to push on publishers, not part of this
   ladder.

**Why precision is the binding constraint.** The `usable` gate asks "is this
content?", not "is this *THE* content." A wrong-but-plausible paper passes it
cleanly and ends up cited in a report — a failure far worse than the thin clip it
replaced, because it is invisible. So the ladder is ordered by *measured* precision
and the bottom two rows are simply not implemented. Recall is cheap to buy here and
not worth what it costs.

**Two gates, and the trust rule.** `retrieve()` walks the routes in table order and
each candidate must clear:

- **Identity** — the fraction of the abstract's key terms present in the retrieved
  text. Over 38 graded retrievals: genuine full text scored min 80% / median 95%,
  wrong documents min 0% / median 33%. At the 0.75 threshold every genuine full text
  survived and 7 of 8 wrong documents were rejected. Lexical and free, so it can sit
  in front of every route without a cost argument.
- **Structure** — at least 3 distinct article section names. A repository landing
  page quotes the abstract verbatim, so it scores ~100% on identity while being
  nothing but that abstract wrapped in institutional chrome. Accepting one would
  *downgrade* the clip: a clean abstract-only note replaced by the same abstract
  plus a cookie banner. Genuine full texts carried 5-9 sections, landing pages 0-2.
  Checked inside the per-location loops too, so a landing page listed first can't
  mask a real PDF listed second.

A route keyed by an identifier read off the URL (`Candidate.trusted`) skips both:
Europe PMC's full text for a PMCID *is* the right document and *is* full text by
construction, and gating it could only add false rejections. The one wrong document
that survives the gate is a topically near-identical paper, which lexical matching
cannot separate — that is the honest residual, and it is why the untrusted routes
sit below the trusted ones rather than beside them.

**What it actually recovers.** Re-run against the same 30 real URLs, the shipped
ladder recovers 10 — exactly the subset the graded exploration attributed to these
routes, with nothing lost between prototype and implementation. The remaining
misses are dominated by `no identifiers`: legacy clips from before cause 1 was
fixed. New runs start from a DOI, so they begin further up the ladder.

Everything is best-effort. `retrieve()` never raises, `_process_source` wraps it
anyway, and any failure falls through to the abstract-only clip that would have
been written regardless. The ladder can only add recoveries; it can never take a
working path away.

### The backfill

`backfill_fulltext.py` is the one-off pass over clips gathered *before* the ladder
existed. Dry-run by default like the other backfills — and its dry run does the
whole retrieval half for real, because retrieval is the uncertain step, so what it
prints is exactly what `--apply` will act on.

Recovered text replaces the abstract stub and the clip is **re-analysed**. That is
the actual point: these notes already exist and are already cited, they were just
written from 200 words of abstract.

Rewriting notes a live vault is citing needs three properties, and each one is a
bug that would otherwise be silent:

- **The filename never changes** (`preserve_title`). Reports cite clips by
  `[[title]]`, and a better analysis usually implies a better title — so a backfill
  that let the clipper rename would quietly break the citation graph it set out to
  enrich.
- **MOCs are never re-assigned** (`reindex=False`, added to `process_clipped_note`
  for this). The note is already indexed; re-running `assign_to_moc` on a richer
  analysis can legitimately pick a *different* MOC, leaving one note listed in two
  with both `note_count`s wrong. Only the existing entry's one-line gloss is
  refreshed, in place — no entry added, moved, or counted.
- **Any failure restores the clip byte-for-byte**, including the analyser rejecting
  the recovered text as `usable: false`. Worst case is the clip you already had.

It calls `clipper._analyse_clip` directly rather than `process_clipped_note`, so
`processed` never goes `False` on disk. The watchdog runs while the backfill does,
and a clip parked at `processed: false` for the length of an analysis call is
exactly what the 60s rescan looks for — it would pick the note up and analyse it a
second time, concurrently.

`--resolve-titles` opts into resolving an identifier from the title for the 55% of
the backlog whose URL carries none. It is off by default at 25% measured precision;
what makes it usable at all is that its candidates still face both gates. On a
25-clip sample it added one recovery and doubled the runtime, so it is worth
reaching for on a targeted `--only` slice rather than a whole-vault pass.

**A note on the test suite.** Writing these tests spent real money: the mocks bind
by call-site name, the code moved from `process_clipped_note` to `_analyse_clip`,
and the tests kept passing while quietly making live API calls (one run took 106
seconds instead of 6). `tests/conftest.py` now fails any test not marked `llm` that
reaches `llm._provider` — the single choke point, patched there rather than at
`llm_simple` because modules bind that name at import and hold their own reference.

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

### The extractor must read the whole report

`_conceptualize` originally passed `report[:SYNTHESIS_RAW_EXCERPT]` to the
`concept_extract` prompt. That constant is a **per-source** budget — it caps each of
the dozen-odd sources stuffed into one synthesis prompt — and nothing about it
suited a single finished report. At 15,000 chars it cut a comprehensive report
roughly in half.

The damage was invisible because the output looked reasonable: an audit of seven
conceptualized reports (30k–55k chars of prose) found **not one** concept drawn from
past ~15k. `Research - Stimulant Use in ADHD…` is the clearest case — its four picks
(liability-threshold model, heritability, proportional hazards, Bayesian updating)
all come from §1–2, the statistical sections, while the extractor never saw §4.3's
dopamine hypothesis of schizophrenia, §4.4's sensitisation, or the whole of §5's
pharmacology. It read like a topical bias in the model; it was a slice.

Truncation also silently broke inline linking, since the prompt requires `mention`
to be copied verbatim from the report text it was shown — a section the extractor
never read can't yield a placeable mention.

`_report_prose` now cuts at the trailing-apparatus boundary (the same
`_TRAILING_SECTION_RE` inline linking uses) and applies its own
`CONCEPT_REPORT_LIMIT` (60k). Dropping `## Sources` matters on its own: it is 7k+
chars of paper titles in a comprehensive report — no concepts in it, and it is a
dense list of precisely the paper-specific proper nouns the prompt tells the model
not to pick. The remaining prose is ~8–14k tokens, which the cheap `moc`-tier model
takes whole, so the limit is a runaway guard rather than a working ceiling.

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
  `CALLOUT_QUIET_SECS` (120s) — a note you're actively typing in is left alone and
  picked up on a later rescan once you pause.
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

## Callout corrections — letting the answer edit the note

A callout is often not a question but an objection. On the L-theanine report the
callout read *"you need to reevaluate this and consider that caffeine might be the
only thing doing anything…"* — a correct catch: the positive trials compared
combination against placebo, never against caffeine alone. The answer agreed and
demolished the claim. But the original claim stayed exactly where it was, three
lines above, and was restated confidently thirty lines below. The report now
contradicted itself twice, and a reader met the error, the correction, and the
error again.

Appending can't fix that. `corrections.py` runs a phase ⑤ that edits the note's own
prose.

### Why an exact-match edit tool, and not regeneration

Handing the model the report and asking for a corrected version is the obvious
approach and the wrong one here. It fails *silently and destructively*: a drifted
sentence, a dropped `[[wikilink]]`, a truncation mid-document. This project already
learned that lesson — `_assert_report_complete()` exists because synthesis silently
truncated (§ Citation integrity), and regeneration reintroduces that risk on every
correction while adding a new one: faithfully rewriting sections nobody asked about.

Exact-match patching fails *loudly and safely*: no match, skip, report it. That
matters more than expressive power because nobody is watching when this runs.

So the mechanism is the one coding agents use — `edit_note(old_string, new_string,
why)`, unique-match required, driven through the existing `llm_tool_loop`. The
error strings are the whole design: a mismatched character becomes a loop iteration
the model repairs, exactly as a coding agent recovers from a failed edit. This is
also a heavily post-trained idiom, which an ad-hoc "replace block 14" format is not.

### The model never touches disk

`edit_note` mutates an in-memory working copy. `verify_edits` decides whether that
copy is ever written, and a rejection discards the **whole set** — edits are
all-or-nothing, so the note is never left half-corrected.

### The gates are a weak substitute for a compiler, and that's the honest limit

A coding agent leans on tests, a type checker, and a human reviewing the diff.
None of those exist for prose. Nothing cheap can tell you a corrected paragraph is
*worse* than the original — it will be fluent and well-cited either way. What the
gates do catch is the model regenerating instead of patching:

| Gate | Fatal? | Why |
|---|---|---|
| Heading structure changed | yes | It rewrote the skeleton — a misread of the task, and feedback doesn't fix a misread. |
| Length below `CALLOUT_MIN_LENGTH_RATIO` | yes | Same failure, different symptom. |
| Introduced an unresolvable `[[link]]` | no | Mechanical. Naming the dead link fixes it. |

Only **newly introduced** links are validated. An early version checked every link
in the note against the run's source titles, which flags every pre-existing citation
in the report — the note is full of links that have nothing to do with this run.
Dropping an existing link is allowed: removing a refuted claim legitimately removes
its citation.

`MAX_CORRECTION_ATTEMPTS` (2) bounds the retry after a *retryable* rejection, and
each attempt restarts from a pristine copy so damage never compounds. The fatal
kinds skip the retry entirely rather than buy a second expensive DeepSeek pass that
was never going to differ. Everything is best-effort: any failure leaves the note
unedited and the answer is appended as before, because correcting the note is an
improvement on answering it, never a precondition.

### Sentinels, and why the block format changed

Prior answers are protected — they're a dated record of what was said. Finding them
needs an exact terminator, and the old format (`> [!done]` line, loose prose, `---`
rules) had none. Answer blocks are now wrapped in `<!-- kg:answer -->` /
`<!-- /kg:answer -->`, invisible in Obsidian, making `_overlaps_protected` a regex
instead of a heuristic.

The same change fixed a second problem. Answers used to emit `## Sources` and `###`
headings *inside* a host note, so the Israel–Palestine answer injected twelve
`##`/`###` headings into the middle of section 5 — "What happened on 7 October" and
"Synthesis" read as top-level report sections. Blocks now title at `####`, subhead
at `#####`, and end with a one-line `**Sources:** [[A]], [[B]]`.

The three pre-existing blocks were converted rather than supported alongside the new
format: three blocks in two files is far less code than a compatibility path, and
a one-shot throwaway script beat both. This is deliberately *not* in
`src/` — the backfill scripts there exist for vault-wide migrations over hundreds of
notes, and this was neither.

### The question is restated, not echoed

Callouts are typed on a phone, mid-thought. Rendering that verbatim as the answer's
heading made the least-considered text on the page the loudest. `clean_question()`
spends one cheap `moc`-tier call to restate it, keeps the original as an `*Asked:*`
subtitle, and falls back to the raw text on any failure — including the no-call
fast path when the question is already well formed.

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
waste. `llm.research_preflight()` polls live spending power before a run and
blocks it if credit is below `RESEARCH_MIN_KEY_CREDITS`. It blocks **only** on a
confident low reading — an uncapped key, an unset key, or an unreachable endpoint
all fail *open*, because a transient blip must not wedge all research (a real
outage is still caught by the cooldown at call time). The reading is cached
`PREFLIGHT_CACHE_SECS` so a rescan batch polls once, not per note.

**Fix 3 — check the pool that actually pays.** Fix 2 shipped reading only
`limit_remaining` from `GET /api/v1/key`. That is the key's *monthly spend cap* —
and as the top of this section already noted, the cap is **independent of the
account balance**. The gate was written knowing there were two pools and then
watched only one of them.

The failure it was built to prevent therefore happened anyway (2026-07-30). The
key read a comfortable `limit: 40, limit_remaining: 27.84`, so preflight passed
with confidence. Underneath, `GET /api/v1/credits` said
`total_credits: 20, total_usage: 20.106` — a balance of **-$0.11**. A
comprehensive L-theanine run gathered 22 sources over 28 minutes and died at
synthesis on `402 – "You requested up to 32000 tokens, but can only afford 8774"`.
The dashboard made it worse by rendering that same cap figure as "OpenRouter
credit left: $28.10", so every surface agreed there was money when there wasn't.

`account_balance()` now polls `/credits` and **both** pools must clear the floor.
Each is judged only when its own reading is conclusive, so the fail-open property
survives an outage of either endpoint; the cooldown clears only when every
conclusive reading is solvent. The dashboard leads with the balance and labels the
cap as a cap, because the two were indistinguishable on the page and that is what
made the wrong number believable.

*Lesson worth keeping:* a guard that reads a plausible-looking number from the
wrong source is more dangerous than no guard. It converts "this might fail" into
"this has been checked", and the run proceeds with more confidence, not less.

**The Gemini free tier is much smaller than it looks.** The fallback that was
supposed to absorb the OpenRouter outage was already spent. The real quota is
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, **20 requests per day per
model** — not the 1,500 that `GEMINI_DAILY_LIMIT` claimed (that is the paid tier).
A single comprehensive run exhausts it outright. The dashboard meter was drawn
against 1,500, so it read `38 / 1500 — 2%` at the exact moment every Gemini model
was returning 429 `RESOURCE_EXHAUSTED`. Two independent budget dials, both
reporting healthy headroom, both wrong, at the same time. The counter aggregates all
Gemini models while the quota is per model, so treat the meter as a floor: "at
least this fraction of some model's daily quota is gone".

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

## Telemetry — why the dashboard needed a ledger, not a log parser

Before this, the pipeline's only observable surfaces were `watchdog.log` (raw
redirected stdout) and trigger frontmatter (`research: true|done`). Both answer
"did it finish"; neither answers "what is it doing right now, how far in, and
what has it cost". Two facts made a log-scraping dashboard the wrong build:

- **Phase state is derivable from the log, but fragile.** The `[research]` prints
  do mark the real boundaries, so a regex reader *works* — until a message is
  reworded, at which point the dashboard silently reports the wrong stage. The
  prints are diagnostics; treating them as an API freezes them.
- **Cost is not in the log at all, and cannot be added to it.** No call site
  ever saw a price. The only trustworthy per-call figure is the provider's own:
  a token estimate can't know the reasoning surcharge or the cache discount.

So `telemetry.py` records both directly, and the two presentations
(`dashboard.py`: a local web page and a vault note) render `snapshot()` without
deriving anything. Adding a number to the dashboard means recording it in
telemetry, never computing it in a renderer.

### Where cost actually comes from

`usage: {include: true}` on every OpenRouter request makes the response carry
`usage.cost` in real US dollars. That is the number the ledger books. Three
consequences shaped the design:

- **The provider knows the price; only the router knows the task.** A provider
  can't label its own spend `synthesis` vs `research`. So providers *buffer* each
  call's usage per-thread (`push_usage`) and `llm._run` drains it with the task
  attached (`flush_usage`). The drain is in a `finally`, so a call that failed —
  a truncated synthesis, a response the completeness gate rejected — still books
  its cost. A failed run that spent $0.40 must not display as free.
- **Gemini is booked at $0.00 but still counted.** The free tier costs nothing;
  what constrains it is the request cap, so the meter tracks calls. That cap is
  **20/day per model**, not 1,500 — see § Provider exhaustion for the day both
  budget meters read healthy while both budgets were gone.
- **Lifetime spend is free.** `research_preflight` already polls `GET /api/v1/key`
  and `GET /api/v1/credits` before every research run (§ Provider exhaustion), so
  the lifetime charge, the key's remaining cap, and the account balance all cost
  no extra API call. Show the **balance** first: it is the pool that stops
  research, and displaying only the cap is what made an overdrawn account read as
  "$28.10 credit left".

### Nested runs

Phase ③ of a research run calls `clipper.process_clipped_note` for each queued
source, and the clip pipeline opens its own `telemetry.run`. Naively that would
evict the research run from the dashboard and replace it with a clip — the run
you care about would vanish for most of its duration. `telemetry.run` therefore
keeps a stack: an inner run only annotates the outer one's detail line and
restores it on exit. Standalone clips, with an empty stack, get their own run.

### Everything is best-effort

Every public entry point in `telemetry.py` swallows its own exceptions, the web
server runs in a daemon thread that logs and drops start-up failures, and a
failed vault-note write is caught. Observability that can break the thing it
observes is worse than none. The tests pin this down (`test_telemetry_failures_never_propagate`).

### Two presentations, because neither covers the whole case

The web page is live (2s polling) and rich, but needs the Surface reachable —
home Wi-Fi, or Tailscale from anywhere. The vault note needs no networking at
all: Obsidian Sync already carries notes to the phone. It is ~60s stale and plain
markdown, which is exactly right for "is it done yet" from a train. The note is
only rewritten when its rendered body actually changes — rewriting an identical
note every rescan would have Sync pushing a file to the phone every minute
forever.

**Both must start before any pipeline work, and the note must not be rendered by
the main loop.** Originally `start_dashboard()` was called *after* the start-up
backlog, and `write_vault_note()` only from the 60s rescan. Both presentations
therefore went dark in exactly the situation you most want to watch: a restart
that picks up a pending trigger drains the backlog before the loop ever begins, so
nothing listens on 8765 and the note sits at whatever the *previous* process last
wrote. Observed on 2026-07-30 as a 24-source, 28-minute run during which the page
refused connections and the note still showed the aborted run that preceded it.

Ordering alone isn't enough. Rescans don't happen while the main thread is inside
a run, so a loop-driven note freezes through *any* long run, backlog or not. So
`start_note_writer()` renders from its own daemon thread and `drain_backlog()` is
called after both presentations are up. The unchanged-content skip means an idle
pipeline still writes nothing, so decoupling the cadence costs no extra Sync
traffic.

*Corollary for anything that reads the ledger out-of-band:* `telemetry.load()`
retires a `current` run whose status is `running` as `interrupted`, which is right
at watchdog start-up and wrong from a second process. A helper that imports
`telemetry` while the watchdog is mid-run will report the live run as dead — read
`STATE_PATH` directly instead.

### State lives outside the vault

`dashboard_state.json` and `events.jsonl` sit beside `watchdog.log`, not in the
vault: they are the pipeline's bookkeeping, and inside the vault they would be
synced, indexed, linted, and shown in search. The vault note is the *rendered*
view, and it is the only telemetry artefact that belongs there. The ledger
survives restarts (spend history would be meaningless otherwise), and a run
still marked `running` in the file means the process was killed mid-flight — it
is retired as `interrupted` on load rather than shown as live forever.
