"""Real Sonnet edit-tool smoke test, including the existing structural gate."""
import pytest
from researcher.corrections import apply_corrections

pytestmark = pytest.mark.llm


def test_causal_overclaim_is_corrected_without_rewriting_structure(require_openrouter):
    note = ("# Observational results\n\n"
            "An observed correlation proves that X causes Y.\n\n"
            "The study measures X and Y in a cross-sectional survey, with no randomisation. "
            "It does not rule out confounding or reverse causality.\n\n"
            "## Limitations\n\nThe design measures association in this sample. "
            "Generalisation requires additional evidence.\n")
    answer = ("The causal statement is incorrect: an observational correlation does not "
              "establish that X causes Y. Confounding and reverse causality remain possible. "
              "The study supports association only [[Survey Study]].")
    body, edits, rejection = apply_corrections(
        "Does the correlation establish causation?", answer, note, {"Survey Study"})
    assert rejection is None and edits
    assert "An observed correlation proves that X causes Y." not in body
    assert "# Observational results" in body and "## Limitations" in body
    assert "association" in body.lower() or "correlation" in body.lower()
