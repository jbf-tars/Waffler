#!/usr/bin/env python3
"""Tests for the deterministic email-layout post-pass in OpenAIStyler.

Why this exists (the bug it guards against):
Line-break placement in styled emails was left entirely to the LLM. Different
providers apply the "greeting / sign-off on its own line" convention with
different reliability, so the SAME dictation came out perfectly formatted on
one machine (PC, landed on Cerebras) and run-on on another (Mac, landed on a
different provider) — e.g. "...at 10.30am? Thank you, James." glued onto one
line instead of the sign-off sitting on its own.

`_format_email_layout` makes the layout deterministic: it runs on the final
styled text regardless of which provider produced it, so every machine gets
identical output. These tests pin that behaviour AND its safety guards (it must
NOT reflow ordinary notes that merely end with a "thanks").

The methods are pure string ops that only use class-level regexes, so we test
them on a bare instance (no API clients / keys needed).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from style_openai import OpenAIStyler  # noqa: E402

S = object.__new__(OpenAIStyler)  # bypass __init__ — methods need no instance state


# ── The core fix: a glued sign-off is promoted to its own paragraph ──────────

def test_glued_signoff_split_to_own_paragraph():
    """The exact user failure: sign-off glued to the last sentence by a space."""
    text = (
        "Hi Jamie,\n\n"
        "Thanks for sending through the slides.\n\n"
        "Can we please schedule a meeting for Tuesday at 10.30am? Thank you, James."
    )
    out = S._format_email_layout(text)
    assert out.endswith("10.30am?\n\nThank you, James."), repr(out)


def test_user_real_example_matches_pc_output():
    """Mac (glued) input must produce byte-identical output to the PC version."""
    mac_glued = (
        "Hi Jamie,\n\n"
        "Thanks for sending through the slides.\n\n"
        "Can we please schedule a meeting for Tuesday at 10.30am? Thank you, James."
    )
    expected_pc = (
        "Hi Jamie,\n\n"
        "Thanks for sending through the slides.\n\n"
        "Can we please schedule a meeting for Tuesday at 10.30am?\n\n"
        "Thank you, James."
    )
    assert S._format_email_layout(mac_glued) == expected_pc


def test_signoff_without_name_split():
    """A bare sign-off with no name ('... attached. Thanks!') still gets its
    own line."""
    text = "Hi Jo,\n\nThe report is attached. Thanks!"
    out = S._format_email_layout(text)
    assert out == "Hi Jo,\n\nThe report is attached.\n\nThanks!", repr(out)


def test_various_closings_split():
    for closing in ["Cheers, Sam", "Kind regards, Dr Smith", "Best regards, Alex",
                    "Regards, J", "Many thanks, Priya", "Best wishes, Mum"]:
        text = f"Hi there,\n\nHope you are well. {closing}."
        out = S._format_email_layout(text)
        assert f"\n\n{closing}." == out[-(len(closing) + 3):], (closing, repr(out))


# ── Idempotence: never double-break already-correct text ─────────────────────

def test_already_formatted_signoff_unchanged():
    text = (
        "Hi Jamie,\n\n"
        "Thanks for the slides.\n\n"
        "Can we meet Tuesday at 10.30am?\n\n"
        "Thank you, James."
    )
    assert S._format_email_layout(text) == text


def test_idempotent_double_apply():
    text = (
        "Hi Jamie,\n\n"
        "The slides look great.\n\n"
        "Can we meet at 10.30am? Thank you, James."
    )
    once = S._format_email_layout(text)
    twice = S._format_email_layout(once)
    assert once == twice
    assert once.endswith("10.30am?\n\nThank you, James.")


# ── Greeting on its own line ─────────────────────────────────────────────────

def test_glued_greeting_split():
    text = "Hi Jamie, Thanks for the slides.\n\nSee you Tuesday."
    out = S._format_email_layout(text)
    assert out.startswith("Hi Jamie,\n\nThanks for the slides."), repr(out)


# ── Safety guards: must NOT reflow non-email text ────────────────────────────

def test_plain_oneliner_not_touched():
    """A single-line note with no greeting and no newlines is left exactly as-is,
    even though it ends with a capitalised word."""
    text = "Remind me to call the bank tomorrow morning."
    assert S._format_email_layout(text) == text


def test_midtext_thankyou_not_split():
    """'thank you, Sarah' mid-sentence (not at the end) is NOT a sign-off."""
    text = (
        "Hi team,\n\n"
        "I wanted to thank you, Sarah did a great job on the launch and the "
        "numbers look strong."
    )
    out = S._format_email_layout(text)
    assert "thank you, Sarah did a great job" in out


def test_thanks_followed_by_more_sentence_not_split():
    """'Thanks James will sort the rest.' is a sentence, not a sign-off — the
    optional-name match must back off because text continues after the name."""
    text = "Hi team,\n\nThe slides look good. Thanks James will sort the rest."
    out = S._format_email_layout(text)
    assert out == text, repr(out)


def test_empty_and_whitespace_safe():
    assert S._format_email_layout("") == ""
    assert S._format_email_layout("   ") == "   "
    assert S._format_email_layout(None) is None


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
