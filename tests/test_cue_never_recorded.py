"""A sound cue is never in what is sent to speech to text.

Waffler keeps the microphone open and splices the half-second before the key
went down into each recording. A cue played through the speakers would be
heard by that microphone: the start cue would be in the recording, and the stop
cue in the pre-roll of the next one. So, before a cue starts, the recorder is
told to silence the moments it sounds in (AudioRecorder.deafen_for).

These tests feed the real recorder callback a synthetic stream (room noise,
a voice, and the actual cue mixed in as the microphone would hear it, after the
speakers' delay) with a fake clock, and look at the WAV it returns.
"""
import io
import math
import random
import struct
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import audio as audio_mod  # noqa: E402
from audio import AudioRecorder, cue_mask_range  # noqa: E402
import sounds as S  # noqa: E402

RATE = 16000
CHUNK = 1024
LATENCY = 0.06          # what the Mac backend assumes; the cue is heard this late


class Clock:
    def __init__(self, now=5000.0):
        self.now = now

    def __call__(self):
        return self.now


# ── the pure part ────────────────────────────────────────────────────────

def test_a_chunk_before_the_window_is_untouched():
    assert cue_mask_range(1024, RATE, 1.0, 2.0, 2.2) is None


def test_a_chunk_after_the_window_is_untouched():
    assert cue_mask_range(1024, RATE, 5.0, 2.0, 2.2) is None


def test_a_chunk_ending_exactly_as_the_window_starts_is_untouched():
    assert cue_mask_range(1024, RATE, 2.0, 2.0, 2.2) is None
    assert cue_mask_range(1024, RATE, 2.2 + 1024 / RATE, 2.0, 2.2) is None


def test_a_chunk_inside_the_window_is_silenced_whole():
    end = 2.1 + 512 / RATE
    assert cue_mask_range(512, RATE, end, 2.0, 2.2) == (0, 512)


def test_a_chunk_that_straddles_the_start_is_cut_part_way():
    # The chunk covers 1.95 to 2.05 s; the window opens at 2.0 s.
    frames = int(0.10 * RATE)
    first, end = cue_mask_range(frames, RATE, 2.05, 2.0, 2.2)
    assert end == frames and first == pytest.approx(0.05 * RATE, abs=1)


def test_a_chunk_that_straddles_the_end_is_cut_part_way():
    frames = int(0.10 * RATE)                      # 2.15 to 2.25 s
    first, end = cue_mask_range(frames, RATE, 2.25, 2.0, 2.2)
    assert first == 0 and end == pytest.approx(0.05 * RATE, abs=1)


def test_a_window_inside_one_chunk_cuts_a_piece_out_of_it():
    frames = 4096                                   # 1.0 to 1.256 s
    first, end = cue_mask_range(frames, RATE, 1.0 + frames / RATE, 1.05, 1.10)
    assert (first, end) == (pytest.approx(0.05 * RATE, abs=1), pytest.approx(0.10 * RATE, abs=1))


# ── a rig around the real recorder ───────────────────────────────────────

class Rig:
    """The real AudioRecorder callback, a fake clock, no stream."""

    def __init__(self, monkeypatch, postroll=0):
        monkeypatch.setattr(audio_mod, "_POSTROLL_MS", postroll)
        self.clock = Clock()
        self.t0 = self.clock.now                    # the time of sample 0
        self.r = AudioRecorder(sample_rate=RATE)
        self.r._callback_active = True
        self.r._clock = self.clock
        self.r._stream = types.SimpleNamespace(active=True)
        self.fed = 0                                # samples given so far

    def now_index(self):
        return self.fed

    def feed(self, samples):
        """Hand the callback ``samples`` in 1024-frame chunks, the clock at
        the end of each as when PortAudio calls."""
        samples = np.asarray(samples, dtype=np.int16)
        for i in range(0, len(samples), CHUNK):
            part = samples[i:i + CHUNK].reshape(-1, 1)
            self.fed += len(part)
            self.clock.now = self.t0 + self.fed / RATE
            self.r._callback(part, len(part), None, None)

    def start(self):
        self.r.start()

    def stop(self):
        wav = self.r.stop()
        if not wav:
            return np.zeros(0, dtype=np.int16)
        with wave.open(io.BytesIO(wav), "rb") as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)

    def cue_window(self, name, latency=LATENCY):
        """Seconds to ignore, as the player computes it for the Mac."""
        return S.mask_seconds(name, latency)


def room(n, seed):
    rng = random.Random(seed)
    return np.array([int(rng.gauss(0, 6)) for _ in range(n)], dtype=np.int16)


def voice(n, amp=3000):
    t = np.arange(n) / RATE
    return (amp * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
            + 0.5 * amp * np.sin(2 * np.pi * 330 * t)).astype(np.int16)


def mixed(total_s, voice_from_s=None, cue_name=None, cue_at_s=None, seed=1):
    """Room noise, a voice from ``voice_from_s`` on, and the cue as the
    microphone hears it from ``cue_at_s``. Also returns the same without the
    cue."""
    n = int(total_s * RATE)
    base = room(n, seed).astype(np.int32)
    if voice_from_s is not None:
        i = int(voice_from_s * RATE)
        base[i:] += voice(n - i)
    base = np.clip(base, -32768, 32767).astype(np.int16)
    with_cue = base.astype(np.int32)
    if cue_name:
        cue = np.array(S.cue_samples(cue_name, RATE), dtype=np.int32)
        j = int(cue_at_s * RATE)
        with_cue[j:j + len(cue)] += cue
    return base, np.clip(with_cue, -32768, 32767).astype(np.int16)


def correlation(signal, template):
    """Normalised correlation of ``template`` with the start of ``signal``."""
    n = min(len(signal), len(template))
    a, b = signal[:n].astype(np.float64), template[:n].astype(np.float64)
    den = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
    return float(np.dot(a, b)) / den if den else 0.0


# Press at chunk 16 (1.024 s): a full pre-roll is behind it.
PRESS_CHUNKS = 16
PRESS_N = PRESS_CHUNKS * CHUNK
PRESS_S = PRESS_N / RATE


# ── the start cue ────────────────────────────────────────────────────────

def test_without_a_request_the_stream_is_recorded_exactly_as_before(monkeypatch):
    base, with_cue = mixed(3.0, voice_from_s=1.8, cue_name=S.START, cue_at_s=PRESS_S + LATENCY)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:PRESS_N])
    rig.start()
    rig.feed(with_cue[PRESS_N:])
    out = rig.stop()
    assert np.array_equal(out, with_cue[PRESS_N - 8 * CHUNK:]), "no mask armed: nothing is touched"
    # And that cue really is in it: this is what the mask has to prevent.
    cue = np.array(S.cue_samples(S.START, RATE))
    at = int(LATENCY * RATE) + 8 * CHUNK
    assert correlation(out[at:], cue) > 0.9


def test_the_start_cue_is_not_in_the_recording(monkeypatch):
    base, with_cue = mixed(3.0, voice_from_s=1.8, cue_name=S.START, cue_at_s=PRESS_S + LATENCY)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:PRESS_N])
    rig.start()
    rig.r.deafen_for(rig.cue_window(S.START))          # what CuePlayer does first
    rig.feed(with_cue[PRESS_N:])
    out = rig.stop()

    keep = 8 * CHUNK                                   # the pre-roll the recorder spliced in
    cue = np.array(S.cue_samples(S.START, RATE))
    at = keep + int(LATENCY * RATE)
    assert abs(correlation(out[at:at + len(cue)], cue)) < 0.05, "the cue is gone"
    assert not np.any(out[at:at + len(cue)]), "silence where it sounded"
    # The window opens a hundredth of a second after the call (no cue is
    # heard sooner) and stays shut until the cue and its ring have passed.
    window = out[keep + int(0.011 * RATE):keep + int(S.mask_seconds(S.START, LATENCY) * RATE) - 2]
    assert len(window) > 0 and not np.any(window)


def test_everything_outside_the_cues_window_is_kept_bit_for_bit(monkeypatch):
    base, with_cue = mixed(3.0, voice_from_s=1.8, cue_name=S.START, cue_at_s=PRESS_S + LATENCY)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:PRESS_N])
    rig.start()
    rig.r.deafen_for(rig.cue_window(S.START))
    rig.feed(with_cue[PRESS_N:])
    out = rig.stop()

    keep = 8 * CHUNK
    expected = base[PRESS_N - keep:].copy()            # the voice and room, no cue
    w_from = keep + int(0.01 * RATE)
    w_to = keep + int(rig.cue_window(S.START) * RATE)
    expected[w_from:w_to] = 0
    margin = 3
    assert np.array_equal(out[:keep], with_cue[PRESS_N - keep:PRESS_N]), \
        "the half-second before the press is exactly what was heard"
    after = slice(w_to + margin, None)
    # After the window the recording is the voice and the room, with no cue left.
    assert np.array_equal(out[after], expected[after])
    # The voice starts well after the window, so none of it is lost.
    assert 1.8 * RATE > PRESS_N + w_to - keep


def test_speech_inside_the_cues_moments_is_the_price_and_only_those_moments(monkeypatch):
    """Someone who starts talking at the same instant as the cue loses that
    quarter-second. Nothing before it and nothing after it."""
    base, with_cue = mixed(3.0, voice_from_s=PRESS_S + 0.05, cue_name=S.START,
                           cue_at_s=PRESS_S + LATENCY)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:PRESS_N])
    rig.start()
    rig.r.deafen_for(rig.cue_window(S.START))
    rig.feed(with_cue[PRESS_N:])
    out = rig.stop()

    keep = 8 * CHUNK
    w_to = keep + int(rig.cue_window(S.START) * RATE)
    assert not np.any(out[keep + int(0.011 * RATE):w_to - 2])
    assert np.array_equal(out[w_to + 3:], base[PRESS_N - keep:][w_to + 3:])
    assert S.mask_seconds(S.START, LATENCY) < 0.30


def test_a_cue_heard_as_late_as_the_backend_allows_is_still_inside_the_window(monkeypatch):
    base, with_cue = mixed(3.0, cue_name=S.STOP, cue_at_s=PRESS_S + LATENCY)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:PRESS_N])
    rig.start()
    rig.r.deafen_for(rig.cue_window(S.STOP))
    rig.feed(with_cue[PRESS_N:])
    out = rig.stop()
    cue = np.array(S.cue_samples(S.STOP, RATE))
    at = 8 * CHUNK + int(LATENCY * RATE)
    assert not np.any(out[at:at + len(cue)])
    # The last sample of the cue is still earlier than the window's end by the
    # room's tail, so a skewed clock by tens of milliseconds still covers it.
    window_end = 8 * CHUNK + int(rig.cue_window(S.STOP) * RATE)
    assert at + len(cue) <= window_end - int(0.05 * RATE)


# ── the stop cue and the next recording's pre-roll ───────────────────────

def test_the_stop_cue_is_not_in_the_next_recordings_half_second(monkeypatch):
    """The stop cue sounds after stop() returned, into the open stream. If the
    next press comes within half a second, its pre-roll would carry it."""
    base, with_cue = mixed(4.0, cue_name=S.STOP, cue_at_s=PRESS_S + 1.0 + LATENCY)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:PRESS_N])
    rig.start()
    rig.feed(with_cue[PRESS_N:PRESS_N + RATE])          # one second of recording
    assert len(rig.stop()) > 0
    stop_at = rig.fed                                    # the cue starts here, plus the delay
    rig.r.deafen_for(rig.cue_window(S.STOP))
    rig.feed(with_cue[stop_at:stop_at + int(0.35 * RATE)])
    pressed_at = rig.fed
    rig.start()                                          # the next press, 0.35 s later
    rig.feed(with_cue[pressed_at:pressed_at + RATE])
    second = rig.stop()

    cue = np.array(S.cue_samples(S.STOP, RATE))
    preroll_from = pressed_at - 8 * CHUNK
    at = (stop_at + int(LATENCY * RATE)) - preroll_from
    assert 0 <= at and at + len(cue) <= 8 * CHUNK, "the cue is inside the spliced half-second"
    heard = second[at:at + len(cue)]
    assert int(np.max(np.abs(heard))) < 100, "room noise at most"
    assert abs(correlation(heard, cue)) < 0.1


def test_a_stop_cue_wins_against_a_press_that_comes_straight_after(monkeypatch):
    base, with_cue = mixed(3.0, cue_name=S.STOP, cue_at_s=1.2 + LATENCY)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:int(1.2 * RATE)])
    rig.r.deafen_for(rig.cue_window(S.STOP))
    rig.feed(with_cue[rig.fed:rig.fed + int(0.1 * RATE)])
    rig.start()                                         # pressed again 0.1 s later
    rig.feed(with_cue[rig.fed:])
    out = rig.stop()
    cue = np.array(S.cue_samples(S.STOP, RATE))
    peak = int(np.max(np.abs(out)))
    # Room noise only (a few counts): the cue's own peak is thousands.
    assert peak < 200, f"something loud is left ({peak}); the cue peaks at {int(np.max(np.abs(cue)))}"


# ── the hands-free lock cue, during a recording ──────────────────────────

def test_the_lock_cue_is_cut_out_of_a_recording_in_progress(monkeypatch):
    base, with_cue = mixed(4.0, voice_from_s=0.0, cue_name=S.LOCK, cue_at_s=2.0 + LATENCY)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:PRESS_N])
    rig.start()
    rig.feed(with_cue[PRESS_N:int(2.0 * RATE)])
    rig.r.deafen_for(rig.cue_window(S.LOCK))            # Fn + Space pressed
    rig.feed(with_cue[rig.fed:])
    out = rig.stop()

    keep = 8 * CHUNK
    lock_at = int(2.0 * RATE) - (PRESS_N - keep)
    window = int(rig.cue_window(S.LOCK) * RATE)
    assert not np.any(out[lock_at + int(0.011 * RATE):lock_at + window - 2])
    # Before the lock and after the window, the take is untouched.
    assert np.array_equal(out[:lock_at], base[PRESS_N - keep:][:lock_at])
    assert np.array_equal(out[lock_at + window + 3:], base[PRESS_N - keep:][lock_at + window + 3:])


def test_two_cues_close_together_are_both_covered(monkeypatch):
    """Start, then the lock tick a moment later."""
    base, with_cue = mixed(3.0, cue_name=S.START, cue_at_s=PRESS_S + LATENCY)
    lock = np.array(S.cue_samples(S.LOCK, RATE), dtype=np.int32)
    j = int((PRESS_S + 0.15 + LATENCY) * RATE)
    with_cue = with_cue.astype(np.int32)
    with_cue[j:j + len(lock)] += lock
    with_cue = np.clip(with_cue, -32768, 32767).astype(np.int16)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:PRESS_N])
    rig.start()
    rig.r.deafen_for(rig.cue_window(S.START))
    rig.feed(with_cue[PRESS_N:PRESS_N + int(0.15 * RATE)])
    rig.r.deafen_for(rig.cue_window(S.LOCK))
    rig.feed(with_cue[rig.fed:])
    out = rig.stop()
    after_pre = out[8 * CHUNK:]
    assert not np.any(np.abs(after_pre[:int(0.35 * RATE)]) > 100), "neither cue is audible"


# ── limits and failure ───────────────────────────────────────────────────

def test_a_window_is_ignored_once_it_is_over(monkeypatch):
    base, with_cue = mixed(3.0)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:PRESS_N])
    rig.r.deafen_for(0.2)
    assert rig.r._deaf
    rig.feed(with_cue[PRESS_N:PRESS_N + RATE])
    assert rig.r._deaf == (), "nothing is checked on every chunk after it ends"


@pytest.mark.parametrize("bad", [0, -1, float("nan"), None, "x", [], float("-inf")])
def test_a_request_that_makes_no_sense_silences_nothing(monkeypatch, bad):
    rig = Rig(monkeypatch)
    rig.r.deafen_for(bad)
    assert rig.r._deaf == ()


def test_no_request_can_silence_the_microphone_for_long(monkeypatch):
    rig = Rig(monkeypatch)
    rig.r.deafen_for(3600)
    (a, b), = rig.r._deaf
    assert b - rig.clock.now <= 1.0 + 1e-9


def test_a_failure_inside_the_mask_does_not_lose_the_chunk(monkeypatch):
    rig = Rig(monkeypatch)
    rig.r._deaf = ((object(), object()),)               # garbage
    rig.r.is_recording = True
    rig.feed(room(CHUNK, 3))
    assert rig.r._capture_errors == 1, "counted, as any callback problem is, not raised"


def test_the_recorders_meter_shows_the_silenced_audio(monkeypatch):
    base, with_cue = mixed(2.0, cue_name=S.START, cue_at_s=PRESS_S + LATENCY)
    rig = Rig(monkeypatch)
    rig.feed(with_cue[:PRESS_N])
    rig.start()
    rig.r.deafen_for(rig.cue_window(S.START))
    rig.feed(with_cue[PRESS_N:PRESS_N + 4 * CHUNK])
    assert rig.r.get_level() < 0.05, "the pill's level does not dance to the cue"


# ── the app asks for it, in this order ───────────────────────────────────

def test_a_cue_player_asks_the_recorder_first(monkeypatch):
    rig = Rig(monkeypatch)
    order = []

    class B:
        latency_s = LATENCY

        def play(self, name, wav):
            order.append(("play", len(rig.r._deaf)))
    player = S.CuePlayer(backend=B(), settings=lambda: {"sounds": True})
    assert player.play(S.START, deafen=rig.r.deafen_for)
    assert order == [("play", 1)], "the window was set when the sound started"
