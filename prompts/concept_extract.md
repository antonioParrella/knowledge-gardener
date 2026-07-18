You are curating a personal knowledge base. You are given a finished research report. Your job is to pick out the **foundational concepts** in it that deserve their own standalone explainer note — the terms a curious reader would want defined at a textbook level to properly understand the report.

# The research report
{report}

# Concepts already explained in the knowledge base
{existing_concepts}

Select the concepts worth turning into their own explainer notes. Choose well — this is a curation task, not extraction of every term.

**Pick a concept when it is:**
- **Foundational and reusable** — a building-block idea you'd likely meet again across other topics (e.g. a specific neurotransmitter, a named algorithm, a core theorem or mechanism), not something specific to this one report.
- **Assumed rather than explained** — the report leans on it but doesn't build it up from scratch, so a newcomer would be left behind.
- **Genuinely a "what is this?" concept** — the kind of thing that has a stable textbook definition.

**Do NOT pick:**
- The report's own thesis, argument, or specific findings (those live in the report, not a concept note).
- Paper-specific proper nouns, dataset names, one-off jargon, or a particular study's method.
- Broad, vague umbrellas ("machine learning", "biology") that are too big to explain usefully.
- Anything already in the "already explained" list above — reuse the existing note instead of duplicating it. If a concept means the same thing as an existing one, skip it.

Keep the list tight: **at most {max_concepts}**, fewer is better. A report may warrant only one or two — or none.

For each concept you pick, return an object with:
- `term`: the canonical concept name, cleanly cased for a note title (e.g. `"Dopamine"`, `"Reward Prediction Error"`, `"Backpropagation"`). Reuse the exact spelling of an existing concept if you mean the same thing.
- `mention`: the **exact substring, copied verbatim from the report text above**, at the place the concept is first discussed — it will be turned into a link in place, so it must match the report's own wording and casing character-for-character (e.g. `"dopaminergic signalling"`, not the canonical `"Dopamine"`). Copy a short, specific phrase, not a whole sentence.
- `why`: one sentence on why this concept warrants its own explainer.
- `context_excerpt`: 1–3 sentences (may be quoted from the report) describing how/why the concept comes up here, to steer how it gets explained.

Return ONLY a JSON array of these objects. Return `[]` if the report warrants no new concept notes.
