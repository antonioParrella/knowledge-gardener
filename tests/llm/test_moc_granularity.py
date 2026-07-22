"""
Tier 3 (real LLM) — MOC granularity.

MOCs must be specific sub-fields, not broad domains — `Generative Models`, never
`AI`. The assignment prompt deliberately avoids a "be consistent" instruction to
stop everything piling into one mega-MOC (AGENTS.md). This asserts a diffusion
paper lands in a specific MOC, not a catch-all. Read-only (assign_to_moc doesn't
write); uses tmp_vault so the empty-MOC context is deterministic.

Run: `pytest -m llm tests/llm/test_moc_granularity.py`
"""

import pytest

import indexer

pytestmark = pytest.mark.llm

# Broad domain labels the assignment is supposed to avoid.
TOO_BROAD = {
    "AI", "Artificial Intelligence", "Machine Learning", "ML", "Deep Learning",
    "Technology", "Science", "Computer Science", "General", "Health",
}


def test_diffusion_paper_gets_a_specific_moc(require_gemini_or_openrouter, tmp_vault):
    topic = indexer.assign_to_moc(
        note_title="Denoising Diffusion Probabilistic Models",
        summary="Generative models that produce images by learning to reverse a "
                "stepwise Gaussian noising process.",
        tags=["diffusion-models", "generative-models", "image-generation"],
        analysis="A U-Net is trained to predict the noise added at each timestep; "
                 "generation denoises random noise into a sample over many steps.",
    )
    assert topic, "assignment returned an empty topic"
    assert topic not in TOO_BROAD, \
        f"MOC '{topic}' is a broad domain — expected a specific sub-field"
