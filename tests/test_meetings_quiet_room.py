"""A quiet room is not a quiet microphone.

The quiet-microphone lift (tests/test_meetings_quiet_mic.py) measures a faint
voice as voice. Faint taps, rustling and knocks in a silent room sit just as
far above the room's floor, and lifted they read as voice too: 30 s of room
with five faint taps measured 1 s of "voice", enough for a chunk to be sent
and for a line the model made up over it to stay. What tells them apart is
pitch, so a take is lifted only when it holds a voice's pitch in runs
(audioproc._has_pitch). Ten real dictations made 30 dB quieter measured 0.7 s
to 5.5 s of pitched sound; these sounds measure none.
"""
import numpy as np

from src.meetings import audioproc as ap
from src.meetings import transcribe as mt

R = ap.RATE


def _db(x):
    return 10 ** (x / 20)


def _room(seconds, hiss_db=-72, seed=7):
    rng = np.random.default_rng(seed)
    return rng, (rng.standard_normal(R * seconds) * _db(hiss_db)).astype(np.float32)


def _burst(a, rng, at, dur, level_db):
    n = int(R * dur)
    t = np.arange(n) / R
    a[int(R * at):int(R * at) + n] += (_db(level_db) * rng.standard_normal(n) * np.exp(-t * 6)).astype(np.float32)


def _knock(a, at, level_db, hz=180):
    n = int(R * 0.25)
    t = np.arange(n) / R
    a[int(R * at):int(R * at) + n] += (_db(level_db) * 3 * np.sin(2 * np.pi * hz * t) * np.exp(-t * 35)).astype(np.float32)


def _voice(a, at, dur, level_db, hz=140):
    """A voice-like sound: a buzz at a speaking pitch, in syllables."""
    n = int(R * dur)
    t = np.arange(n) / R
    syllables = (np.sin(2 * np.pi * 3.5 * t) > -0.2).astype(np.float32)
    a[int(R * at):int(R * at) + n] += (_db(level_db) * np.sign(np.sin(2 * np.pi * hz * t)) * syllables).astype(np.float32)


def test_faint_taps_in_a_silent_room_are_not_voice():
    rng, a = _room(30)
    for at in (4, 9, 15, 21, 27):
        _burst(a, rng, at, 0.2, -50)
    assert ap.voiced_seconds(a) == 0.0


def test_faint_rustling_in_a_silent_room_is_not_voice():
    rng, a = _room(30)
    _burst(a, rng, 10, 2.5, -55)
    _burst(a, rng, 20, 1.5, -55)
    assert ap.voiced_seconds(a) == 0.0


def test_knocks_on_the_desk_are_not_voice():
    # A knock rings at a pitch, but only for an instant: never three windows running.
    _, a = _room(30)
    for at in (3, 8, 12, 19, 25, 28):
        _knock(a, at, -50)
    assert ap.voiced_seconds(a) == 0.0


def test_a_line_written_over_taps_is_still_called_invented():
    rng, a = _room(30)
    for at in (4, 9, 15, 21, 27):
        _burst(a, rng, at, 0.2, -50)
    mask = ap.voiced_mask(a)
    assert mt.invented("Thank you for watching", ap.voiced_between(mask, 0, 30))
    assert mt.invented("Kjell, du har en kjell.", ap.voiced_between(mask, 0, 30))


def test_a_quiet_microphones_voice_is_still_voice():
    _, a = _room(30)
    _voice(a, 5, 6, -50)
    assert ap.voiced_seconds(a) > 3.0


def test_a_short_quiet_answer_is_still_voice():
    _, a = _room(30)
    _voice(a, 5, 0.6, -50)
    assert ap.voiced_seconds(a) >= 0.3


def test_a_voice_at_a_normal_level_measures_the_same_with_taps_about():
    rng, a = _room(30, hiss_db=-60)
    _voice(a, 5, 6, -25)
    plain = ap.voiced_seconds(a)
    _burst(a, rng, 20, 0.3, -50)
    assert abs(ap.voiced_seconds(a) - plain) < 0.1
    assert plain > 3.0


def test_a_one_word_answer_on_a_very_quiet_microphone_is_still_voice():
    # 0.25 s of voice at -58 dBFS: little more than one run of pitched windows.
    _, a = _room(2, hiss_db=-80)
    _voice(a, 0.8, 0.25, -58)
    assert ap.voiced_seconds(a) >= 0.15


def test_typing_and_shuffling_are_not_voice():
    rng, a = _room(30)
    for at in np.arange(2, 28, 0.7):
        n = int(R * 0.08)
        t = np.arange(n) / R
        a[int(R * at):int(R * at) + n] += (_db(-45) * rng.standard_normal(n) * np.exp(-t * 30)).astype(np.float32)
    assert ap.voiced_seconds(a) == 0.0
    rng, b = _room(30)
    for at in (5, 12, 20):
        n = int(R * 0.6)
        t = np.arange(n) / R
        b[int(R * at):int(R * at) + n] += (_db(-52) * rng.standard_normal(n) * np.exp(-t)).astype(np.float32)
    assert ap.voiced_seconds(b) == 0.0
