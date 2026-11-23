"""Meetings: a quiet microphone's voice counts as voice.

Seen on a Windows PC (Nov 2026): its microphone handed over speech at -44 to
-60 dBFS. Dictation lifts such a take before judging it (boost_quiet_speech in
src/transcribe_whisper.py, towards -20 dBFS by 30 dB at most); Meetings judged
voice against a fixed -46 dBFS floor. Of 20 real takes from that PC, heard as
a meeting would hear them, 4 were never sent (too little voice) and 7 more
were dropped as written over nothing (tests/test_meetings_invented_lines.py).
Voice is now measured as dictation measures it, after the same lift: 20 of
20 kept, and lines written over a quiet room still go.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from meetings import audioproc  # noqa: E402
from meetings.transcribe import clean_segments, transcribe  # noqa: E402

RATE = 16000
rng = np.random.default_rng(11)
REAL = "We ship the new build on Friday."


def room(seconds, dbfs=-70.0):
    """A quiet room: steady low noise."""
    return (rng.standard_normal(int(seconds * RATE)) * 10 ** (dbfs / 20)).astype(np.float32)


def voice(seconds, dbfs, room_dbfs=-70.0):
    """Syllables (a 180 Hz tone, on two thirds of the time) at `dbfs` while on, in a quiet room."""
    t = np.arange(int(seconds * RATE)) / RATE
    on = (np.sin(2 * np.pi * 4 * t) > -0.5).astype(np.float32)
    tone = (10 ** (dbfs / 20) * np.sqrt(2) * np.sin(2 * np.pi * 180 * t) * on).astype(np.float32)
    return tone + room(seconds, room_dbfs)


def texts(segments):
    return [s["text"] for s in segments]


@pytest.mark.parametrize("dbfs", [-50.0, -56.0])
def test_a_quiet_voice_is_measured_as_voice_and_its_words_are_sent(dbfs):
    audio = np.concatenate([room(1.0), voice(4.0, dbfs), room(1.0)])
    assert audioproc.voiced_seconds(audio) > 2.0, "about two thirds of the 4 s is voice"
    asked = []

    def model(a, provider, model_name, language):
        asked.append(len(a))
        return [{"start": 1.0, "end": 5.0, "text": REAL}]
    out = transcribe(audio, {"transcribe_provider": "groq", "language": "en"}, backend=model)
    assert asked, "the chunk is sent, not skipped as silence"
    assert texts(out) == [REAL], "and its line is not taken for one written over nothing"


def test_a_line_written_over_the_quiet_room_still_goes():
    audio = np.concatenate([voice(3.0, -50.0), room(30.0)])
    raw = [{"start": 0.0, "end": 3.0, "text": REAL},
           {"start": 15.0, "end": 17.5, "text": "Teksting av Nicolai Winther"}]
    assert texts(clean_segments(raw, audio)) == [REAL]


@pytest.mark.parametrize("dbfs", [-70.0, -40.0])
def test_steady_noise_and_digital_silence_are_still_nothing(dbfs):
    assert audioproc.voiced_seconds(room(10.0, dbfs)) == 0.0
    assert audioproc.voiced_seconds(np.zeros(10 * RATE, dtype=np.float32)) == 0.0


def test_the_lift_is_dictations_and_never_more_than_30_db():
    normal = audioproc.window_rms(voice(3.0, -20.0))
    assert audioproc._lift(normal) == pytest.approx(1.0, abs=0.15), "a normal take is not lifted"
    # 40 dB short of normal speech, in a near-silent room: lifted 30, no more.
    quiet = audioproc.window_rms(voice(3.0, -60.0, room_dbfs=-90.0))
    assert audioproc._lift(quiet) == pytest.approx(10 ** (30 / 20)), "40 dB short, lifted 30"
    # Voice must stand 4 times above the room to be lifted, as dictation's boost
    # asks: -60 dBFS in a -70 dBFS room is not told from the room.
    assert audioproc._lift(audioproc.window_rms(voice(3.0, -60.0))) == 1.0
    assert audioproc._lift(audioproc.window_rms(room(3.0, -90.0))) == 1.0, "nothing to lift"
