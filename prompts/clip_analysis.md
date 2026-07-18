Source URL: {source_url}

Full clipped content:
{content}

Existing tags already used in this knowledge base:
{vocabulary}

First, decide whether the content above is the actual article/document, or whether
the real content never loaded. Set "usable": false ONLY when what you were given is
not readable content — e.g. raw binary or PDF bytes, a bot-detection / CAPTCHA /
"enable JavaScript" / cookie / paywall / login interstitial, or an error page (access
denied, 404, "content unavailable"). If it is genuine content — even if it is short,
partial, or only an abstract — set "usable": true and analyse it normally. When
"usable" is false, give a title that names what the page actually was and keep the
analysis to one line; the note will be discarded, not saved.

Analyse this article thoroughly. Consider:
- What are the core arguments and how strong is the evidence?
- What are the key ideas that are introduced — describe them specifically, with mathematical detail where applicable
- Make sure that the argument from the text can be clearly reconstructed from the summary you write, don't be afraid of being long.  

Write your detailed analysis freely in markdown. Format mathematics using LaTeX with $...$ for inline and $$...$$ for display math — it will render with MathJax in Obsidian.

Return a JSON object with:
- usable: boolean — false if the content is not the real document (binary/PDF bytes, CAPTCHA/paywall/JS interstitial, error page), true otherwise
- title: clean descriptive title
- content: your thorough analysis in markdown
- moc_summary: one-sentence summary (max 120 chars) suitable for indexing in a knowledge base. Be specific — mention the key finding, not generic words like "article discusses".
- tags: list of 3-6 tags (lowercase, words joined by hyphens, e.g. "machine-learning"; no spaces, no leading "#"). Reuse an existing tag from the list above whenever it means the same thing — even if you'd phrase it differently (use "machine-learning", don't coin "ml") — so tags stay connected across notes. Only coin a new tag when nothing in the list fits, and keep it specific.

Return ONLY valid JSON.