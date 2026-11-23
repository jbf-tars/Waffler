"""Meetings: a made-up sign-off at the start or the end of a real line goes;
only phrases from the outro lists nobody says in a meeting, and never a line
that is only one. Real speech beginning "Thank you." or "Bye" stays."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from meetings.transcribe import clean_segments, strip_signoffs  # noqa: E402

RATE = 16000


def _voice(seconds, dbfs=-20.0):
    t = np.arange(int(seconds * RATE)) / RATE
    on = (np.sin(2 * np.pi * 4 * t) > -0.5).astype(np.float32)
    return (10 ** (dbfs / 20) * np.sqrt(2) * np.sin(2 * np.pi * 180 * t) * on).astype(np.float32)


@pytest.mark.parametrize("said, kept", [
    ("Thank you for watching So that the ... stuff I need to sort out", "So that the ... stuff I need to sort out"),
    ("Thank you for watching. So that is the plan.", "So that is the plan."),
    ("Thanks for watching, the numbers are up.", "The numbers are up."),
    ("Okay, thanks for watching, see you in the next video. And now the real stuff.", "And now the real stuff."),
    ("Please like and subscribe. The plan is fine.", "The plan is fine."),
    ("I need to sort out the stuff. Thank you for watching", "I need to sort out the stuff."),
    ("I need to sort out the stuff Thank you for watching!", "I need to sort out the stuff"),
    ("Thank you for watching So we start Monday. Thanks for watching", "So we start Monday."),
])
def test_a_sign_off_beside_real_words_goes(said, kept):
    assert strip_signoffs(said) == kept


@pytest.mark.parametrize("said", [
    "Thank you for watching", "Thank you.", "Thank you. So that is the plan.", "Thanks, that helps a lot.",
    "Bye, see you tomorrow.", "Thanks for watching Sam's kids tonight", "we should say thank you for watching",
    "I will subscribe to the newsletter.", "Okay.", "You know what I mean.", "See you on Friday then.",
    "Thank you for the update, so we start Monday.", "Okay, thanks for watching, see you in the next video.",
    "Thank you for watching. Thanks for watching!", ""])
def test_real_speech_and_whole_line_sign_offs_are_left_to_the_audio_rule(said):
    assert strip_signoffs(said) == said


def test_clean_segments_strips_the_start_of_a_voiced_line_and_keeps_a_voiced_thank_you():
    audio = np.concatenate([_voice(5.0), np.zeros(RATE), _voice(2.0)])
    raw = [{"start": 0.0, "end": 5.0, "text": "Thank you for watching So that the stuff I need to sort out is the budget."},
           {"start": 6.0, "end": 8.0, "text": "Thank you."}]
    out = clean_segments(raw, audio)
    assert [s["text"] for s in out] == ["So that the stuff I need to sort out is the budget.", "Thank you."]
