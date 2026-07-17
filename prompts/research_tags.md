You are indexing a research report into a personal knowledge base so it can be found and grouped later. Produce two things: a one-line summary and a set of tags.

Topic: {topic}

Report excerpt:
{report}

Existing tags already used in this knowledge base:
{vocabulary}

Produce 3-6 useful topical tags that capture the specific subject matter, methods, and key concepts of this report — the kind of tags that would let someone rediscover it or group it with related notes.

Tag rules:
- **Reuse an existing tag from the list above whenever it means the same thing**, even if you would have phrased it differently (e.g. use the existing "machine-learning" rather than coining "ml"). This is what keeps the vault's tags connected. Only coin a new tag when nothing in the list fits.
- Still be specific: prefer precise technical terms over broad domains, and add a specific new tag when the report genuinely warrants one that isn't in the list. Don't force everything onto a few broad tags.
- Style: lowercase, words joined by hyphens (e.g. "logical-qubits", "error-correction", "diffusion-models"). No spaces, no leading "#".
- Skip generic filler ("research", "overview", "notes") and stopwords.

Also write a **one-line summary** of the report for its index entry. It is rendered as a single markdown list item — `- [[Note Title]] — your summary` — so it must be one line.

Summary rules:
- One sentence, roughly 10-25 words, plain text on a single line. No line breaks, no markdown, no bullet points, no headings, no `[[wikilinks]]`.
- Say what the report actually concludes or covers, specifically enough to tell it apart from neighbouring notes on the same shelf. "Traces the Schmidhuber-LeCun world models priority dispute; finds the concept predates both" — not "A report about world models."
- Don't restate the title, and don't open with filler ("This report examines…"). Lead with the substance.

Return a JSON object with:
- summary: the one-line summary described above.
- tags: list of 3-6 tags following the rules above.

Return ONLY valid JSON.
