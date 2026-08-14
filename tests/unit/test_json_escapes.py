r"""
Tier 1 — JSON escape repair in providers/base.py.

A clip analysis of a paper carries maths, and `$\alpha$` inside a JSON string is an
invalid escape: json.loads rejects the entire response, parse_json_response returns
{}, and the caller drops the document on the floor. That cost 45 silently-lost
sources in one month of the live log and 9 of 29 attempts in the first full-text
backfill run — full-text papers being the worst case precisely because they are the
ones with maths in them.

The ordering is the safety property: strict parse first, repair only on failure. A
response that already parses must never be rewritten.
"""

import json

from providers.base import parse_json_response, repair_invalid_escapes


class TestLatexNowParses:
    def test_greek_command(self):
        assert parse_json_response(r'{"c": "threshold $\alpha$ = 0.05"}') == {
            "c": r"threshold $\alpha$ = 0.05"}

    def test_display_math_brackets(self):
        assert parse_json_response(r'{"c": "\[ E = mc^2 \]"}') == {"c": r"\[ E = mc^2 \]"}

    def test_inline_math_parens(self):
        assert parse_json_response(r'{"c": "\(x\)"}') == {"c": r"\(x\)"}

    def test_windows_path(self):
        assert parse_json_response(r'{"c": "C:\Users\parre"}') == {"c": r"C:\Users\parre"}

    def test_realistic_analysis_block(self):
        raw = r'{"usable": true, "title": "A Paper", "content": "The estimator $\hat{\theta}$ converges when $\alpha < \sigma$.", "tags": ["stats"]}'
        out = parse_json_response(raw)
        assert out["usable"] is True and r"\alpha" in out["content"]


class TestValidResponsesUntouched:
    """Strict parse runs first, so nothing already-valid is ever rewritten."""

    def test_real_newline_escape_survives(self):
        assert parse_json_response(r'{"c": "a\nb"}') == {"c": "a\nb"}

    def test_tab_and_quote_escapes_survive(self):
        assert parse_json_response(r'{"c": "a\tb \"q\""}') == {"c": 'a\tb "q"'}

    def test_unicode_escape_survives(self):
        assert parse_json_response(r'{"c": "\u00e9"}') == {"c": "é"}

    def test_escaped_backslash_survives(self):
        assert parse_json_response(r'{"c": "a\b"}') == {"c": "a\b"}

    def test_markdown_fences_still_stripped(self):
        assert parse_json_response('```json\n{"c": 1}\n```') == {"c": 1}


class TestStillRejectsGenuineGarbage:
    """Repair fixes malformed escapes, not missing text."""

    def test_truncated_json_stays_rejected(self):
        assert parse_json_response('{"content": "abc') == {}

    def test_prose_is_rejected(self):
        assert parse_json_response("I'm sorry, I cannot do that.") == {}

    def test_empty_input(self):
        assert parse_json_response("") == {}
        assert parse_json_response(None) == {}


class TestRepairFunction:
    def test_leaves_valid_escapes_alone(self):
        s = r'"a\nb\tc\\d\"e"'          # \n \t \\ \" are all valid JSON escapes
        assert repair_invalid_escapes(s) == s

    def test_lone_invalid_backslash_is_doubled(self):
        # `\d` is not a JSON escape, so it has to become a literal backslash.
        assert repair_invalid_escapes(r'"\d"') == r'"\\d"'

    def test_escapes_invalid_ones(self):
        assert json.loads(repair_invalid_escapes(r'{"c":"\alpha"}'))["c"] == r"\alpha"

    def test_bare_u_without_hex_is_repaired(self):
        # `\underline` is not a unicode escape, but starts with u.
        assert json.loads(repair_invalid_escapes(r'{"c":"\underline{x}"}'))["c"] == r"\underline{x}"

    def test_real_unicode_escape_preserved(self):
        assert json.loads(repair_invalid_escapes(r'{"c":"\u00e9"}'))["c"] == "é"

    def test_trailing_backslash_does_not_crash(self):
        repair_invalid_escapes("abc\\")
