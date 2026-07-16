You are consolidating the tag vocabulary of a personal Obsidian knowledge base. Over time, tags drifted into near-duplicates — different spellings, hyphenation, and synonyms for the same concept (e.g. "machinelearning" / "machine-learning", "ml", or "sports-betting" / "sportsbetting" / "sportsbook"). They no longer connect when the user filters by tag. Your job is to map every existing tag to a single **canonical** tag.

Here are all the tags currently in use, with how many notes use each:

{tag_list}

Return a JSON object mapping **every** tag above to its canonical form. Rules:

- **Canonical style:** lowercase, words joined by hyphens ("machine-learning", "wealth-tax", "sports-betting"). No spaces, no leading "#".
- **Merge variants and synonyms** to one canonical tag: spelling/hyphenation variants ("machinelearning" -> "machine-learning"), singular/plural ("sportsbooks" -> "sportsbook"... choose one), abbreviations vs full forms ("ml" -> "machine-learning", "rl" -> "reinforcement-learning", "llms" -> "llm"). When choosing which spelling wins, prefer the clearer, more readable hyphenated form, and lean toward higher-count spellings when they're equally clear.
- **Preserve specificity — do NOT over-merge.** Distinct concepts stay distinct ("wealth-tax" and "taxation" are different; "diffusion" and "flow-matching" are different). Only merge tags that genuinely mean the same thing. It is fine — expected — for many tags to map to themselves (just normalised).
- **Drop junk** by mapping it to an empty string "": stopword fragments ("the", "into", "how", "to", "what", "are", "look", "effects"), bare years ("2024"), and broken/placeholder markers ("unreadable", "corrupted", "unavailable", "model", "data").
- The keys of your returned object must be **exactly** the tags listed above, verbatim. Every listed tag must appear as a key.

Return ONLY the JSON object, nothing else.
