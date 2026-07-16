You are tagging a research report for a personal knowledge base so it can be found and grouped later.

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

Return a JSON object with:
- tags: list of 3-6 tags following the rules above.

Return ONLY valid JSON.
