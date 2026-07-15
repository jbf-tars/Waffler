#!/usr/bin/env python3
"""Tests for exotic-hyphen normalization in _strip_em_dashes.

The bug: LLMs sometimes emit typographic hyphen codepoints in place of the
ASCII hyphen the speaker's text implies. Observed live (corpus case L6):
Cerebras output "rate‑limit" with U+2011 NON-BREAKING HYPHEN for spoken
"rate-limit". Visually identical, but the pasted text then breaks search,
diffs, grep, and downstream tooling. These word-joining codepoints must map
to ASCII "-", while the em/en-dash -> comma rule keeps working unchanged.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from style_openai import OpenAIStyler  # noqa: E402

S = object.__new__(OpenAIStyler)  # pure string method — no client state needed


def test_nonbreaking_hyphen_becomes_ascii():
    # U+2011 — the exact live failure
    assert S._strip_em_dashes("the rate‑limit pressure") == "the rate-limit pressure"


def test_hyphen_and_minus_codepoints_become_ascii():
    # U+2010 HYPHEN and U+2212 MINUS SIGN
    assert S._strip_em_dashes("a‐b and c−d") == "a-b and c-d"


def test_em_dash_still_becomes_comma():
    """Normalizing hyphens must not weaken the em-dash rule."""
    assert S._strip_em_dashes("It's slow — really slow.") == "It's slow, really slow."


def test_ascii_compounds_untouched():
    assert S._strip_em_dashes("voice-to-text and gpt-4.1-mini stay") == \
        "voice-to-text and gpt-4.1-mini stay"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all hyphen tests passed")
