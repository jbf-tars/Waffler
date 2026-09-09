#!/usr/bin/env python3
"""A longer transcript is not automatically a better one.

`_retry_if_incomplete` re-transcribes on the alternate provider when the first
result is impossibly short for the measured speech, then decided which to keep
using WORD COUNT ALONE (>= 1.25x wins). That authorises a destructive
replacement on no evidence that the two are even the same utterance. External
review reproduced the worst case:

    original  : "Do not transfer the money to that account."          (8 words)
    alternate : "Please transfer the money to that account right now
                 without any delay."                                  (12 words)

The alternate wins on count and reverses the instruction. The retry exists to
RECOVER truncated speech, so a genuine recovery is a fuller version of the same
words: the original's content should still be present in it. A different or
hallucinated utterance will not contain them, and a dropped negation or changed
number is disqualifying however much longer the result is.

Pure offline tests: no API keys, no audio, no network.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from transcribe_whisper import _alternate_is_safe_replacement  # noqa: E402


# ── The reported failure ────────────────────────────────────────────────────

def test_dropped_negation_is_never_accepted():
    """The exact reviewed case. Losing 'not' inverts the instruction."""
    ok, reason = _alternate_is_safe_replacement(
        "Do not transfer the money to that account.",
        "Please transfer the money to that account right now without any delay.")
    assert ok is False, "a longer transcript that drops a negation was accepted"
    assert "negation" in reason


@pytest.mark.parametrize("orig,alt", [
    ("Do not send it.", "Please send it as soon as you possibly can today."),
    ("I can't make Tuesday.", "I can make Tuesday and Wednesday and Thursday too."),
    ("Never approve that invoice.", "Approve that invoice today please and let me know."),
    ("We have no budget for this.", "We have budget for this and plenty more besides."),
])
def test_negation_loss_rejected_across_forms(orig, alt):
    ok, _ = _alternate_is_safe_replacement(orig, alt)
    assert ok is False, f"{orig!r} -> {alt!r} reversed the meaning"


# ── Genuine recovery must still be accepted ────────────────────────────────

def test_genuine_truncation_recovery_is_accepted():
    """The case the retry exists for: the same speech, more of it."""
    ok, reason = _alternate_is_safe_replacement(
        "I wanted to ask about the",
        "I wanted to ask about the invoice for two thousand pounds and "
        "whether it went out on Friday.")
    assert ok is True, reason


def test_recovery_with_preserved_negation_is_accepted():
    ok, reason = _alternate_is_safe_replacement(
        "Do not send the",
        "Do not send the contract until legal have signed it off.")
    assert ok is True, reason


# ── Unrelated or hallucinated content must be rejected ─────────────────────

def test_unrelated_content_rejected():
    ok, reason = _alternate_is_safe_replacement(
        "Send the report to Priya",
        "Thank you for watching this video, please subscribe to the channel.")
    assert ok is False
    assert "overlap" in reason


def test_changed_number_rejected():
    """Numbers are exactly the detail a user cannot afford to have silently
    rewritten."""
    ok, reason = _alternate_is_safe_replacement(
        "Transfer 2500 pounds",
        "Transfer 3500 pounds to the account today without any further delay.")
    assert ok is False
    assert "number" in reason


def test_preserved_number_accepted():
    ok, _ = _alternate_is_safe_replacement(
        "Transfer 2500 pounds",
        "Transfer 2500 pounds to the client account before Friday afternoon.")
    assert ok is True


# ── Robustness ─────────────────────────────────────────────────────────────

def test_empty_original_accepts_anything_non_empty():
    """Nothing to contradict, and anything beats an empty transcript."""
    ok, _ = _alternate_is_safe_replacement("", "Some recovered speech here.")
    assert ok is True


def test_empty_alternate_rejected():
    ok, _ = _alternate_is_safe_replacement("Some words here", "")
    assert ok is False


def test_case_and_punctuation_insensitive():
    ok, _ = _alternate_is_safe_replacement(
        "do NOT send it",
        "Do not send it until the review is complete, please.")
    assert ok is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
