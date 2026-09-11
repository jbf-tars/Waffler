"""A sign-off the speaker dictated must survive styling.

Live failure (2026-09-11, Mac, v3.14.97): the raw transcript ended
"...which version that you're on? Thank you, James." and the styled output
ended "...which version that you're on?" — the whole sign-off deleted.

Reproduced deterministically against Groq openai/gpt-oss-120b, 4/4 runs, and
the trigger is narrow: a GREETING must be present (so the model engages its
email machinery) AND the closing must be "Thank you" WITH a comma before the
name. "Thanks, James." survives, "Thank you James." (no comma) survives,
"Thank you, James." with no greeting survives. Root cause: "Thank you" is
absent from the prompt's Recognised sign-offs list, so with the comma the
model reads "Thank you, James" as thanking James mid-body and drops it as a
pleasantry.

The prompt is fixed too, but a prompt is a request, not a guarantee — these
tests cover the deterministic guard, which is provider-independent and is
what actually holds the line.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from style_openai import OpenAIStyler  # noqa: E402


def _styler():
    """A bare instance — the guard touches no network state."""
    return object.__new__(OpenAIStyler)


BODY = "Just wanted to check which version of the toolkit that you're on?"
RAW = "Hi Malak, just wanted to check which version of the toolkit that you're on? Thank you, James."


# ── the reported regression ──────────────────────────────────────────────────

def test_deleted_signoff_is_restored():
    styled = "Hi Malak,\n\n" + BODY
    out = _styler()._restore_dropped_signoff(styled, RAW)
    assert out.endswith("Thank you,\nJames"), repr(out)
    assert BODY in out


@pytest.mark.parametrize("closing", [
    "Thank you, James.", "Thanks, James.", "Cheers, James.",
    "Kind regards, James.", "Thank you so much, James.",
])
def test_every_closing_form_is_restored(closing):
    raw = f"Hi Malak, {BODY.lower()} {closing}"
    out = _styler()._restore_dropped_signoff("Hi Malak,\n\n" + BODY, raw)
    name = closing.rstrip(".").split(", ")[-1]
    assert out.rstrip().endswith(name), repr(out)


def test_bare_closing_with_no_name_is_restored_without_one():
    raw = f"Hi Malak, {BODY.lower()} Thank you."
    out = _styler()._restore_dropped_signoff("Hi Malak,\n\n" + BODY, raw)
    assert out.rstrip().endswith("Thank you,") or out.rstrip().endswith("Thank you")
    assert "James" not in out


# ── it must not fire otherwise ───────────────────────────────────────────────

def test_signoff_already_present_is_untouched():
    styled = "Hi Malak,\n\n" + BODY + "\n\nThank you,\nJames"
    assert _styler()._restore_dropped_signoff(styled, RAW) == styled


def test_is_idempotent():
    s = _styler()
    once = s._restore_dropped_signoff("Hi Malak,\n\n" + BODY, RAW)
    assert s._restore_dropped_signoff(once, RAW) == once


def test_signoff_kept_inline_by_the_model_is_not_duplicated():
    """The model kept it, just glued on. Restoring would double it."""
    styled = "Hi Malak,\n\n" + BODY + " Thank you, James."
    out = _styler()._restore_dropped_signoff(styled, RAW)
    assert out.lower().count("thank you") == 1, repr(out)


def test_no_signoff_in_raw_means_nothing_is_added():
    raw = "Hi Malak, just wanted to check which version of the toolkit you are on."
    styled = "Hi Malak,\n\n" + BODY
    assert _styler()._restore_dropped_signoff(styled, raw) == styled


def test_thanks_used_mid_body_is_not_a_signoff():
    """Content after the name proves it is body text, not a closing."""
    raw = "Hi Malak, thanks for meeting today, James, it was really useful."
    styled = "Hi Malak,\n\nThanks for meeting today, James, it was really useful."
    assert _styler()._restore_dropped_signoff(styled, raw) == styled


def test_never_invents_a_signoff_the_speaker_did_not_say():
    raw = "Send me the file when you get a chance."
    styled = "Send me the file when you get a chance."
    out = _styler()._restore_dropped_signoff(styled, raw)
    assert out == styled
    assert "thank" not in out.lower()


def test_empty_inputs_are_safe():
    s = _styler()
    assert s._restore_dropped_signoff("", RAW) == ""
    assert s._restore_dropped_signoff("text", "") == "text"
