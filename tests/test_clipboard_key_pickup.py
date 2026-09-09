#!/usr/bin/env python3
"""Setup's clipboard key pickup, and its refusals.

The most annoying moment in setup is the hand-off: create a key on the
provider's site, copy it, alt-tab back, find the field, paste. The copy has
already happened, so the app can simply notice and fill the field in.

This reads the user's clipboard, so the important tests here are the ones that
prove it CANNOT return anything that is not obviously an API key. Ordinary
clipboard contents, drafts, passwords and prose must never be lifted into the
UI. Provider attribution matters too: a Cerebras key starts "csk-", which
contains "sk-", so a naive OpenAI pattern claims it.
"""

import ast
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _patterns():
    """Read _KEY_PATTERNS out of app.py (importing app.py needs pywebview)."""
    s = (ROOT / "app.py").read_text(encoding="utf-8")
    i = s.index("_KEY_PATTERNS = (")
    end = s.index("\n    )", i) + len("\n    )")
    block = s[i + len("_KEY_PATTERNS = "):end]
    block = "\n".join(l for l in block.splitlines() if not l.strip().startswith("#"))
    return ast.literal_eval(block)


PATTERNS = _patterns()


def peek(text: str):
    """Mirror of the app's matching logic."""
    if not text or len(text) > 300:
        return None
    for provider, pattern in PATTERNS:
        m = re.search(pattern, text)
        if m:
            return provider, m.group(0)
    return None


# ── Real keys are picked up, and attributed to the right provider ───────────

@pytest.mark.parametrize("provider,key", [
    ("groq",     "gsk_" + "A" * 48),
    ("openai",   "sk-proj-" + "X" * 40),
    ("openai",   "sk-" + "Q" * 44),
    ("cerebras", "csk-" + "z" * 44),
])
def test_real_keys_are_detected_and_attributed(provider, key):
    got = peek(key)
    assert got is not None, f"{provider} key was not detected"
    assert got[0] == provider, f"{key[:8]}... attributed to {got[0]}, not {provider}"
    assert got[1] == key


def test_cerebras_key_is_not_mistaken_for_openai():
    """'csk-' contains 'sk-'. Without anchoring, the OpenAI pattern claims it
    and the key lands in the wrong field."""
    provider, _ = peek("csk-" + "q" * 44)
    assert provider == "cerebras"


def test_key_surrounded_by_whitespace_is_found():
    """Copying from a web page often brings padding with it."""
    got = peek("  \n gsk_" + "b" * 40 + " \n")
    assert got and got[0] == "groq"


# ── Everything else must be refused ─────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Can we move the design review to Thursday at ten thirty?",
    "Hi Darren, thanks for your email, can you send the powerpoint?",
    "hunter2!Correct-Horse-Battery",
    "https://console.groq.com/keys",
    "sk-short",                       # right prefix, too short to be a key
    "gsk_tooshort",
    "",
    "   ",
])
def test_ordinary_clipboard_contents_are_never_returned(text):
    assert peek(text) is None, f"clipboard text was wrongly treated as a key: {text!r}"


def test_oversized_clipboard_is_ignored():
    """A whole document that happens to contain a key-like run is not a paste
    the user just made on a provider's site; refusing it keeps the feature
    narrow and predictable."""
    assert peek("gsk_" + "A" * 400) is None


def test_a_dictation_mentioning_a_key_is_not_lifted():
    assert peek("Remember to put the groq key in the settings page later.") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
