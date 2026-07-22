"""
Tier 1 — notes.normalize_math_delimiters and its helpers.

The most edge-case-dense function in the repo: it rewrites LaTeX math to
Obsidian's dollar delimiters without touching code, escaped-literal brackets, or
currency. AGENTS.md documents each of these hazards; these tests pin them down so
a vibe-coded tweak that reintroduces one fails loudly instead of silently
corrupting every future report.
"""

import notes
from notes import normalize_math_delimiters as norm, _is_display_line


class TestInlineMath:
    def test_inline_parens_become_single_dollars(self):
        assert norm(r"the loss \(\mathcal{L}\) is minimised") == r"the loss $\mathcal{L}$ is minimised"

    def test_multiple_inline_on_one_line(self):
        assert norm(r"\(a\) and \(b\)") == r"$a$ and $b$"

    def test_plain_prose_untouched(self):
        assert norm("no math here at all") == "no math here at all"

    def test_existing_dollar_math_untouched(self):
        # A confidence level already in correct dollar form must survive verbatim.
        assert norm(r"the level $1 - \alpha$ holds") == r"the level $1 - \alpha$ holds"


class TestDisplayMath:
    def test_bare_delimiters_on_own_lines(self):
        src = "\\[\nE = mc^2\n\\]"
        assert norm(src) == "$$\nE = mc^2\n$$"

    def test_whole_line_wrapped_display(self):
        assert norm(r"\[ E = mc^2 \]") == r"$$ E = mc^2 $$"

    def test_escaped_literal_bracket_midprose_preserved(self):
        # `\[a, b\]` mid-sentence is a markdown escaped bracket, NOT display math.
        line = r"consider the interval \[a, b\] carefully"
        assert norm(line) == line

    def test_is_display_line_helper(self):
        assert _is_display_line(r"\[")
        assert _is_display_line(r"\]")
        assert _is_display_line(r"\[ E = mc^2 \]")
        assert not _is_display_line(r"interval \[a, b\] here")
        assert not _is_display_line(r"\[a")  # opens but doesn't close


class TestCodeIsSkipped:
    def test_fenced_block_untouched(self):
        src = "```\narr\\[i\\] = \\(x\\)\n```"
        assert norm(src) == src

    def test_inline_code_span_untouched(self):
        assert norm(r"call `arr\[i\]` then \(y\)") == r"call `arr\[i\]` then $y$"

    def test_tilde_fence_untouched(self):
        src = "~~~\n\\(x\\)\n~~~"
        assert norm(src) == src


class TestCurrencyEscaping:
    def test_currency_escaped_only_when_bracket_on_line(self):
        # The collision case: real math introduced next to a currency amount.
        out = norm(r"bet \$? no — you bet $1 then lose \(\alpha\)", escape_currency=True)
        assert r"\$1" in out
        assert r"$\alpha$" in out

    def test_currency_left_alone_when_no_bracket_on_line(self):
        # No new math '$' is introduced here, so nothing should be escaped.
        line = "it cost $5 yesterday"
        assert norm(line, escape_currency=True) == line

    def test_currency_disabled_by_default(self):
        # Default escape_currency=False: currency is never touched.
        out = norm(r"you bet $1 then lose \(\alpha\)")
        assert r"\$1" not in out
        assert "$1 then lose $\\alpha$" in out

    def test_existing_dollar_math_not_reescaped_with_currency_on(self):
        # $2^n$ starts with a digit but is real math with no bracket to convert —
        # must not be corrupted even with currency escaping on.
        line = r"complexity is $2^n$ overall"
        assert norm(line, escape_currency=True) == line


class TestIdempotence:
    def test_running_twice_is_stable(self):
        src = "the loss \\(\\mathcal{L}\\) with\n\\[\nx = 1\n\\]\ndone"
        once = norm(src)
        assert norm(once) == once
