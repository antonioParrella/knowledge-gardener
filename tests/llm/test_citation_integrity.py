"""
Tier 3 (real LLM) — the citation repair pass.

_repair_wikilinks is the model half of the "[[21]] instead of the real title"
defense. This feeds it a report that cites sources by number and a set of valid
titles, then asserts the repaired report resolves them to real notes. Runs the
real synthesis-task model (OpenRouter). Run after prompt/model changes to
research_repair_links or synthesis routing.
"""

import pytest

import researcher

pytestmark = pytest.mark.llm

VALID_TITLES = {
    "Attention Is All You Need",
    "Denoising Diffusion Probabilistic Models",
    "Deep Residual Learning for Image Recognition",
}

# A report that drifted into numbered-citation style — every [[N]] is a dead link.
REPORT = """# Foundations of Modern Deep Learning

The transformer architecture introduced self-attention as its core primitive [[1]],
replacing recurrence entirely. Image generation was later transformed by iterative
denoising [[2]], which learns to reverse a noising process. Very deep networks were
made trainable by residual connections [[3]], which ease optimisation by letting
gradients flow through identity shortcuts.

These three ideas — attention [[1]], diffusion [[2]], and residual learning [[3]] —
underpin most of what followed, and together they define the modern toolkit that
practitioners reach for when building large-scale models today.

## Sources
- [[Attention Is All You Need]]
- [[Denoising Diffusion Probabilistic Models]]
- [[Deep Residual Learning for Image Recognition]]
"""


def test_numbered_citations_are_repaired(require_openrouter):
    before = researcher._find_bad_wikilinks(REPORT, VALID_TITLES)
    assert before, "fixture precondition: report should start with bad links"

    repaired = researcher._repair_wikilinks(REPORT, VALID_TITLES)

    after = researcher._find_bad_wikilinks(repaired, VALID_TITLES)
    assert len(after) < len(before), \
        f"repair should reduce dead links; before={before} after={after}"
    # The prose must survive — repair must never shrink a good report.
    assert len(repaired) >= len(REPORT) * 0.8
