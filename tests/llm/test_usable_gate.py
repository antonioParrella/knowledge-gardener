"""
Tier 3 (real LLM) — the `usable` gate.

The single most important LLM judgment in the pipeline: it decides whether a
fetched page is real content or a bot-wall/CAPTCHA/binary dump. Getting it wrong
means either hallucinated summaries in the vault (false-positive) or lost sources
(false-negative). Can't be mocked meaningfully — the model's judgment IS the
thing under test. Run after any change to clip_analysis / clip_system or a model
swap:  `pytest -m llm tests/llm/test_usable_gate.py`
"""

import pytest

from config import load_prompt
from llm import llm_simple, parse_json_response

pytestmark = pytest.mark.llm


def _judge(content: str, url: str) -> dict:
    user = load_prompt("clip_analysis", source_url=url, content=content,
                       vocabulary="(none yet)")
    system = load_prompt("clip_system")
    data = parse_json_response(llm_simple(prompt=user, system=system, task="clip"))
    assert isinstance(data, dict), f"analyzer did not return JSON: {data!r}"
    return data


def test_botwall_is_rejected(require_gemini_or_openrouter, fixture_text):
    data = _judge(fixture_text("botwall.txt"), "https://blocked.example.com/paper")
    assert data.get("usable") is False, \
        f"Cloudflare interstitial should be usable:false, got {data.get('usable')!r}"


def test_real_article_is_accepted(require_gemini_or_openrouter, fixture_text):
    data = _judge(fixture_text("article.txt"), "https://example.com/diffusion-intro")
    # Absent or true both mean "process it" per clipper.py; only explicit false discards.
    assert data.get("usable") is not False, \
        "a genuine article must not be discarded as unusable"
