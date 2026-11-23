"""Meetings: lines the speech model wrote with nobody speaking never reach the
transcript, in any language, from any engine.

Seen on a Mac (Nov 2026): a meeting's notes talked about "Norwegian phrases"
nobody said. Whisper writes text over silence and over a lone cough or click,
often in another language and sometimes as one phrase over and over. Measured
on Windows with the on-device `small` model, decoded as src/local_stt_ct2.py
does, on synthetic audio: a quiet room with one short burst, language left to
the model, came back as three lines of Welsh ("Y Llywodraeth Cymru yw'r
Llywodraeth Cymru?" and two loops of a phrase). clean_segments kept one of the
three with Whisper's confidence numbers, and all three without them, which is
what the Waffler Plus answer looks like: it carries no avg_logprob and no
no_speech_prob, so only the English thank-you rule was left to catch anything.

The rules now look at the audio itself, so they hold for every engine:
- no voice under a line: it goes, whatever it says;
- far more letters than the voice under it could carry: it goes;
- one phrase repeated over and over (Whisper's own compression-ratio bar): it goes.
Real speech, short answers, fast talkers and quiet voices stay.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from meetings.transcribe import clean_segments  # noqa: E402

RATE = 16000
rng = np.random.default_rng(3)


def silence(seconds):
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def voice(seconds, dbfs=-20.0):
    """Syllables: a 180 Hz tone switched on and off four times a second (on
    about two thirds of the time), as loud as `dbfs` while on."""
    t = np.arange(int(seconds * RATE)) / RATE
    on = (np.sin(2 * np.pi * 4 * t) > -0.5).astype(np.float32)
    return (10 ** (dbfs / 20) * np.sqrt(2) * np.sin(2 * np.pi * 180 * t) * on).astype(np.float32)


def blip(seconds=0.3, dbfs=-20.0):
    """A click or a cough: a short burst of noise."""
    return (rng.standard_normal(int(seconds * RATE)) * 10 ** (dbfs / 20)).astype(np.float32)


def seg(text, start, end, nsp=None, lp=None):
    """A model segment. Without nsp/lp it is shaped like the Waffler Plus
    answer: times and text only."""
    out = {"start": start, "end": end, "text": text}
    if nsp is not None:
        out["no_speech_prob"] = nsp
    if lp is not None:
        out["avg_logprob"] = lp
    return out


def texts(segments):
    return [s["text"] for s in segments]


REAL = "We ship the new build on Friday."

# Lines a speech model writes over silence, in several languages. The Welsh
# one is what the on-device model wrote over a quiet room in the measurement
# above; the others are the same kind of line.
INVENTED = [
    "Teksting av Nicolai Winther",
    "Takk for at du så på.",
    "Y Llywodraeth Cymru yw'r Llywodraeth Cymru?",
    "Untertitel im Auftrag des ZDF, 2020",
    "ご視聴ありがとうございました",
]


# ── written over silence ─────────────────────────────────────────────────────

@pytest.mark.parametrize("invented", INVENTED)
def test_a_line_over_silence_goes_when_the_answer_has_no_confidence_numbers(invented):
    """The Waffler Plus answer: times and text, nothing else."""
    audio = np.concatenate([voice(3.0), silence(57.0)])
    out = clean_segments([seg(REAL, 0.0, 3.0), seg(invented, 30.0, 32.0)], audio)
    assert texts(out) == [REAL]


@pytest.mark.parametrize("invented", INVENTED)
def test_a_line_over_silence_goes_even_when_the_model_sounds_sure(invented):
    """Whisper can be confident about a line it made up (avg_logprob -0.1)
    and only fairly sure nobody spoke (no_speech_prob 0.5)."""
    audio = np.concatenate([voice(3.0), silence(57.0)])
    out = clean_segments([seg(REAL, 0.0, 3.0, 0.01, -0.3), seg(invented, 30.0, 32.0, 0.5, -0.1)], audio)
    assert texts(out) == [REAL]


def test_a_part_that_is_mostly_silence_keeps_only_what_was_said():
    """A Plus part: what was said, then silence standing where the chunks with
    no voice were never kept (recorder._joined), lines invented in it."""
    audio = np.concatenate([voice(4.0), silence(60.0), voice(4.0), silence(90.0)])
    raw = [seg(REAL, 0.2, 3.8), seg("you", 30.0, 32.1), seg("Teksting av Nicolai Winther", 34.0, 36.5),
           seg("And the slides go out on Monday.", 64.1, 67.9), seg("Takk for at du så på.", 120.0, 122.0)]
    assert texts(clean_segments(raw, audio)) == [REAL, "And the slides go out on Monday."]


# ── written over a cough or a click ──────────────────────────────────────────

def test_a_long_line_over_a_short_burst_goes():
    """0.3 s of noise cannot carry a sentence: about 80 letters a second."""
    audio = np.concatenate([silence(14.0), blip(0.3), silence(14.0)])
    out = clean_segments([seg("Teksting av Nicolai Winther", 0.0, 28.0)], audio)
    assert out == []


def test_a_phrase_repeated_over_and_over_goes():
    """The measured Welsh loop: one phrase twelve times across a burst, the
    model confident (avg_logprob -0.11), no_speech_prob 0.90."""
    audio = np.concatenate([silence(30.0), blip(0.5), silence(30.0)])
    loop = " ".join(["Y Llywodraeth"] * 12)
    assert clean_segments([seg(loop, 30.0, 60.0, 0.90, -0.11)], audio) == []
    assert clean_segments([seg(loop, 30.0, 60.0)], audio) == []


def test_a_loop_over_real_speech_goes_too():
    """Whisper stuck on one sentence it heard once: the line cannot be trusted."""
    audio = voice(12.0)
    loop = " ".join(["I want to check the numbers."] * 8)
    out = clean_segments([seg(REAL, 0.0, 2.5), seg(loop, 2.5, 12.0)], audio)
    assert texts(out) == [REAL]


# ── what was said stays ──────────────────────────────────────────────────────

@pytest.mark.parametrize("numbers", [{}, {"nsp": 0.02, "lp": -0.3}])
def test_real_speech_stays_from_every_engine(numbers):
    audio = np.concatenate([voice(3.0), silence(2.0), voice(3.0)])
    raw = [seg(REAL, 0.0, 3.0, **numbers), seg("And the slides go out on Monday.", 5.0, 8.0, **numbers)]
    assert texts(clean_segments(raw, audio)) == [REAL, "And the slides go out on Monday."]


def test_a_short_answer_stays_when_its_times_are_a_little_off():
    """A 0.5 s "Yes." the model placed 0.3 s late: most of its line is
    silence, but the voice is right beside it."""
    audio = np.concatenate([silence(10.0), voice(0.5), silence(10.0)])
    assert texts(clean_segments([seg("Yes.", 10.3, 10.9)], audio)) == ["Yes."]


def test_a_fast_talker_stays():
    """67 letters over 1.6 s of voice, about 42 a second: faster than the
    measured voice's 10 to 16, still under the bar of 60."""
    audio = voice(2.0)
    line = "So basically we need the quarterly figures and the forecast by tomorrow morning"
    assert texts(clean_segments([seg(line, 0.0, 2.0)], audio)) == [line]


def test_a_quiet_voice_stays():
    audio = np.concatenate([silence(1.0), voice(3.0, dbfs=-38.0), silence(1.0)])
    assert texts(clean_segments([seg(REAL, 1.0, 4.0)], audio)) == [REAL]


@pytest.mark.parametrize("said", ["No, no, no, no.", "Yeah, yeah, yeah.", "One, two, three, four, five, six."])
def test_a_little_repetition_that_people_say_stays(said):
    audio = voice(3.0)
    assert texts(clean_segments([seg(said, 0.0, 3.0)], audio)) == [said]


@pytest.mark.parametrize("said", INVENTED)
def test_the_same_words_said_aloud_stay(said):
    """The rules judge the audio, not the language: someone really speaking
    Norwegian, Welsh, German or Japanese is kept."""
    audio = voice(3.0)
    assert texts(clean_segments([seg(said, 0.0, 3.0)], audio)) == [said]
