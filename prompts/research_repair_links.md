You are fixing broken citations in a finished research report destined for an Obsidian vault.

Citations in this vault are `[[wikilinks]]` that must match a note title **exactly** — anything else is a dead link. The report you are given has drifted: some citations are not real note titles. The most common failure is numbered-reference style borrowed from academic papers (`[[21]]`, `[[12]]`), but a paraphrased, abbreviated, or invented title is equally broken.

You are given:
- **Valid note titles** — the complete set of notes that may be cited. Nothing else exists.
- **Invalid citations to fix** — the exact broken links found in the report.
- **The report** itself.

Your job:
- Replace every invalid citation with the correct `[[Exact Note Title]]` from the valid list, chosen from the surrounding context: what claim is being supported, which paper or argument the sentence is about, and how the same source is cited elsewhere in the report. A number like `[[20]]` next to a discussion of a 1990 recurrent controller resolves to the note about that work.
- If a citation appears several times, resolve each occurrence independently — the same number may have been used consistently, but verify against each sentence rather than assuming.
- If you genuinely cannot tell which note an invalid citation refers to, **delete the citation** and leave the sentence reading naturally. A clean sentence with no citation is better than a dead link or a wrong attribution. Never guess an attribution you don't have evidence for.
- If the report has a `## Sources` section, make it list every valid `[[wikilink]]` actually cited in the finished body — no dead links, no sources you removed.

Critically:
- **Change nothing else.** Do not rewrite, shorten, re-order, improve, or re-title any prose. Do not touch headings, LaTeX, tables, or valid citations. This is a link-fixing pass, not an editing pass.
- Return the **complete** report, start to finish. Do not truncate or abbreviate any section, and never replace content with a placeholder or a note about what you changed.

Output only the corrected report in Markdown.
