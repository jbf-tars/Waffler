#!/usr/bin/env python3
"""Fidelity tests for the ASR-layer hallucination filter.

WHY THIS EXISTS
---------------
`_strip_hallucinations` removes stock Whisper outros ("Thanks for watching!")
that the model invents on silence. The patterns were anchored to end-of-string
with NO grammatical guard, so they also deleted legitimate speech that merely
ENDS with those words. Reproduced offline on 2026-09-08 against v3.14.85:

    "Please send the deck to Priya and thank you."  -> "...to Priya and"
    "I'll sign it. Over to you."                    -> "...Over to"
    "...onboarding, payroll, benefits and more."    -> "...benefits"
    "The tutorial ends by saying thanks for watching." -> "...by saying"
    "Thank you."                                    -> ""   (whole utterance)

Every one is silent, unrecoverable loss of the user's own words, and several
leave the text grammatically broken ("Over to"), which is the tell-tale that a
phrase was *integrated* speech rather than an appended hallucination.

THE RULE THIS PINS
------------------
A hallucinated outro is APPENDED AFTER a finished sentence. Legitimate speech
is GRAMMATICALLY INTEGRATED. So a stock phrase is only removed when it starts
its own sentence, and never when removing it would leave a dangling function
word. Where audio evidence is available it is consulted rather than guessed:
a transcript is never blanked when the recording actually contained speech.

These are pure-function tests: no API keys, no private history, no microphone.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from transcribe_whisper import _strip_hallucinations  # noqa: E402


# ── NEGATIVE CONTROLS: real speech that merely ends with a stock phrase ──────
# Each of these was silently mangled before the fix. They must survive intact.

LEGITIMATE = [
    ("trailing thank-you addressed to someone",
     "Please send the deck to Priya and thank you."),
    ("handover ending in 'you'",
     "I need you to review the contract before Friday. Over to you."),
    ("enumeration ending 'and more'",
     "The pack covers onboarding, payroll, benefits and more."),
    ("'thanks for watching' as reported speech",
     "The tutorial ends by saying thanks for watching."),
    ("'and the rest' enumeration",
     "Bring the cables, the adaptor and the rest."),
    ("subscribe as real content",
     "Ask the customer whether they want to subscribe."),
    ("'more' as an object",
     "We shipped the parser, the linter and much more."),
    ("question ending in you",
     "Can I leave the final decision with you?"),
]


@pytest.mark.parametrize("label,text", LEGITIMATE, ids=[c[0] for c in LEGITIMATE])
def test_legitimate_endings_are_preserved(label, text):
    """Real speech is never truncated, whatever it happens to end with."""
    assert _strip_hallucinations(text, speech_seconds=12.0) == text


@pytest.mark.parametrize("label,text", LEGITIMATE, ids=[c[0] for c in LEGITIMATE])
def test_legitimate_endings_preserved_without_audio_evidence(label, text):
    """The guard must not depend on audio being available — with duration
    unknown the filter still may not eat integrated speech."""
    assert _strip_hallucinations(text) == text


def test_no_dangling_function_word_is_ever_left():
    """A trailing conjunction/preposition proves the phrase was integrated.
    This is the structural signature of the bug and must never reappear."""
    danglers = ("and", "to", "for", "of", "with", "or", "plus", "by", "saying")
    for _label, text in LEGITIMATE:
        out = _strip_hallucinations(text, speech_seconds=12.0)
        last = out.rstrip(".!?,").split()[-1].lower() if out.split() else ""
        assert last not in danglers, f"{text!r} -> {out!r} left dangling {last!r}"


# ── POSITIVE CONTROLS: genuine appended hallucinations still removed ─────────

GENUINE = [
    ("classic outro after a finished sentence",
     "So that is the plan for the sprint. Thanks for watching!",
     "So that is the plan for the sprint."),
    ("subscribe outro after a comma",
     "The new pricing goes live on Monday, please subscribe!",
     "The new pricing goes live on Monday"),
    ("like-button outro",
     "That covers the migration steps. Hit the like button!",
     "That covers the migration steps."),
    ("see-you-next-time outro",
     "We will pick this up on Thursday. See you next time.",
     "We will pick this up on Thursday."),
    ("caption credit",
     "The invoice is attached. Subtitles by the Amara.org community",
     "The invoice is attached."),
]


@pytest.mark.parametrize("label,text,expected", GENUINE, ids=[c[0] for c in GENUINE])
def test_genuine_appended_outros_still_stripped(label, text, expected):
    assert _strip_hallucinations(text, speech_seconds=12.0) == expected


# ── Never blank a transcript when the audio contained real speech ────────────

def test_standalone_thank_you_kept_when_audio_had_speech():
    """'Thank you.' is the classic silence hallucination — but if the mic
    actually captured several seconds of speech, it is the user's words."""
    assert _strip_hallucinations("Thank you.", speech_seconds=4.0) == "Thank you."


def test_standalone_thank_you_dropped_on_near_silence():
    """With almost no speech in the clip, the same text IS the hallucination."""
    assert _strip_hallucinations("Thank you.", speech_seconds=0.2) == ""


def test_remainder_not_discarded_when_speech_present():
    """The old '<=2 words left -> return empty' rule deleted real short
    dictations. It may only fire as an audio-evidenced judgement."""
    out = _strip_hallucinations("Web outfits. Please subscribe!", speech_seconds=9.0)
    assert out == "Web outfits."


def test_remainder_discarded_on_near_silence():
    out = _strip_hallucinations("Web outfits. Please subscribe!", speech_seconds=0.3)
    assert out == ""


# ── The September 2026 incident: this filter is NOT the cause ───────────────

def test_and_the_rest_is_not_touched():
    """History entry 2026-09-03T19:23:44 ended '...Correct me if I'm wrong.
    and the rest.' in BOTH the saved transcript and the styled output. This
    pins that the ASR filter does not produce that ending, so the loss is
    upstream (capture or ASR) — and guards against anyone 'fixing' it by
    adding another blind blacklist entry."""
    text = ("So is there a difference between what audience you choose? "
            "I don't think there really is. Correct me if I'm wrong. and the rest.")
    assert _strip_hallucinations(text, speech_seconds=30.8) == text


# ── Robustness ──────────────────────────────────────────────────────────────

def test_empty_and_whitespace_safe():
    assert _strip_hallucinations("") == ""
    assert _strip_hallucinations("   ") == ""


def test_idempotent():
    t = "So that is the plan for the sprint. Thanks for watching!"
    once = _strip_hallucinations(t, speech_seconds=12.0)
    assert _strip_hallucinations(once, speech_seconds=12.0) == once


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
