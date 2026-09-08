#!/usr/bin/env python3
"""Mic-dropout detection: distinguish a dead stream from a gated pause.

THE BUG
-------
The detector flagged a recording when >=30% of its windows were digital
silence (RMS < 1.0), on the reasoning that "natural pauses keep room-tone,
they don't go to exact zero". That is not true of modern capture: noise
suppression (Windows Voice Focus, headset DSP, Krisp/Broadcast-style
filters) outputs EXACT zeros while you are not speaking. So pausing to
think looked identical to the microphone dying.

Measured over 861 real recordings in app.log: it fired 4 times, and all 4
transcribed at a healthy 1.56-3.05 words per second of live audio - nothing
was missing. 4/4 false positives, 0 confirmed true positives. The user saw
"Mic dropped out - please re-record" over a perfectly good transcript.

THE RULE
--------
A dead stream is one contiguous run that does not recover. Gated pauses are
many short runs with speech after them. So: measure the LONGEST CONTIGUOUS
dead run, not the scattered total, and only suspect a dropout when that run
does not recover before the end of the recording.

Even then the audio alone is not conclusive - a user who stops talking
before releasing the hotkey also produces a terminal dead run. The caller
must confirm against the transcript before alarming the user.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quality import mic_dropout_signal  # noqa: E402

LOUD, DEAD = 400.0, 0.0
WIN = 0.05  # seconds per window


def _rms(pattern):
    """'S' = speech, '.' = digital silence."""
    return [LOUD if c == "S" else DEAD for c in pattern]


def test_gated_pauses_between_speech_are_not_a_dropout():
    """The real false positive: ~50% dead, but speech keeps resuming."""
    sig = mic_dropout_signal(_rms("SSS....SSS....SSS....SSS"), window_s=WIN)
    assert sig["suspected"] is False
    assert sig["dead_fraction"] > 0.4          # plenty of digital silence
    assert sig["longest_dead_run_s"] < 0.3     # but never a long unbroken run


def test_long_terminal_dead_run_is_suspected():
    """Stream dies mid-take and never recovers - the case worth warning about."""
    sig = mic_dropout_signal(_rms("SSSSSSSSSS" + "." * 60), window_s=WIN)
    assert sig["suspected"] is True
    assert sig["terminal"] is True
    assert sig["longest_dead_run_s"] >= 3.0


def test_long_dead_run_that_recovers_is_not_suspected():
    """If speech resumes afterwards the mic plainly did not die."""
    sig = mic_dropout_signal(_rms("SSSS" + "." * 60 + "SSSSSSSSSS"), window_s=WIN)
    assert sig["suspected"] is False
    assert sig["terminal"] is False


def test_short_trailing_silence_is_not_a_dropout():
    """Everyone stops talking a moment before releasing the hotkey."""
    sig = mic_dropout_signal(_rms("SSSSSSSSSSSSSSSSSSSS" + "." * 10), window_s=WIN)
    assert sig["suspected"] is False


def test_recording_with_no_speech_is_not_a_dropout():
    """Silence throughout is an empty recording, not a hardware failure."""
    sig = mic_dropout_signal(_rms("." * 80), window_s=WIN)
    assert sig["suspected"] is False


def test_all_speech_is_clean():
    sig = mic_dropout_signal(_rms("S" * 60), window_s=WIN)
    assert sig["suspected"] is False
    assert sig["dead_fraction"] == 0.0


def test_empty_input_is_safe():
    sig = mic_dropout_signal([], window_s=WIN)
    assert sig["suspected"] is False
    assert sig["dead_fraction"] == 0.0


def test_real_false_positive_shapes_from_the_log():
    """Reconstructed from the four recordings that actually fired: roughly
    half digital silence, dozens of speech windows interleaved. None may
    suspect a dropout."""
    for pattern in ["SS..SS..SS..SS..SS..SS..SS..SS..",
                    "S..S..S..S..S..S..S..S..S..S..S.",
                    "SSSS........SSSS........SSSS...."]:
        sig = mic_dropout_signal(_rms(pattern), window_s=WIN)
        assert sig["suspected"] is False, (pattern, sig)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
