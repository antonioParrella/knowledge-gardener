"""
Tier 3 setup — real-LLM tests.

Every test here is marked `llm` (deselected by default; run with `-m llm`). On top
of the marker, `require_keys` skips gracefully when the needed API key is absent,
so `pytest -m llm` on a machine without keys reports clean skips instead of opaque
provider errors.
"""

import os

import pytest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def fixture_text():
    return _load


@pytest.fixture
def require_gemini_or_openrouter():
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
        pytest.skip("needs GEMINI_API_KEY or OPENROUTER_API_KEY")


@pytest.fixture
def require_openrouter():
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("needs OPENROUTER_API_KEY (synthesis task is OpenRouter-only)")
