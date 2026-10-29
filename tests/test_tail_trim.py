"""3.16.3: the silent end of a take is cut off before speech to text.

Whisper's best-known invention is "Thank you." and it comes from noisy
near-silence at the end of a clip. The owner's Mac records at an input volume
of 40%, so Waffler's quiet-audio lift raises his takes by about 21 dB, and the
room noise in the silent end of each one with them. Every recent "Thank you."
he never said was already in the speech-to-text output.

So, before the upload, the take is cut after its last voiced moment plus
_TAIL_MARGIN_S (0.6 s), when what follows is longer than _TAIL_MIN_S (1 s).
The start of a take is never touched (the pre-roll catches first words),
nothing the detector calls speech is ever cut, and a take with no clear
speech, or speech that hardly stands out from its noise, is left alone.

These tests use synthetic audio: no network, no keys, no private recordings.
"""
import io
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import transcribe_whisper as tw  # noqa: E402
from transcribe_whisper import WhisperTranscriber  # noqa: E402

SR = 16000
WIN_S = 0.03


# ── synthetic audio ─────────────────────────────────────────────────────────

def _rng(seed):
    return np.random.default_rng(seed)


def speech(seconds, db, seed=0):
    """A voice-like tone (180 Hz with a 3 Hz syllable swell) at ``db`` dBFS RMS."""
    t = np.arange(int(seconds * SR)) / SR
    v = np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t + seed))
    return v * (10 ** (db / 20) / np.sqrt(np.mean(v ** 2)))


def noise(seconds, db, seed=1):
    n = _rng(seed).normal(0.0, 1.0, int(seconds * SR))
    return n * (10 ** (db / 20) / np.sqrt(np.mean(n ** 2)))


def silence(seconds):
    return np.zeros(int(seconds * SR))


def take(*parts, floor_db=-70.0, seed=7):
    """The parts one after another, with room noise at ``floor_db`` under all
    of them (None: no room noise)."""
    a = np.concatenate(parts)
    if floor_db is not None:
        a = a + noise(len(a) / SR, floor_db, seed=seed)
    return a


def wav(a, rate=SR, channels=1, width=2):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
        else:
            w.writeframes(((np.clip(a, -1, 1) * 127) + 128).astype(np.uint8).tobytes())
    return b.getvalue()


def seconds(b):
    with wave.open(io.BytesIO(b), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def samples(b):
    with wave.open(io.BytesIO(b), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


# ── the cut ─────────────────────────────────────────────────────────────────

def test_speech_then_room_noise_is_cut_after_the_speech_plus_the_margin():
    clip = wav(take(noise(0.5, -70, 3), speech(3.0, -30), noise(3.0, -70, 4)))
    out, trailing = tw._trim_silent_tail(clip)
    assert trailing == pytest.approx(3.0, abs=0.06)
    assert seconds(out) == pytest.approx(0.5 + 3.0 + tw._TAIL_MARGIN_S, abs=0.06)
    # The start and the speech are the same samples, untouched.
    assert np.array_equal(samples(out), samples(clip)[: len(samples(out))])


def test_speech_then_digital_silence_is_cut_the_same_way():
    """A stream that went dead, or a noise gate, ends in exact zeros."""
    clip = wav(np.concatenate([take(noise(0.5, -70, 3), speech(2.0, -30)), silence(2.5)]))
    out, trailing = tw._trim_silent_tail(clip)
    assert trailing == pytest.approx(2.5, abs=0.06)
    assert seconds(out) == pytest.approx(2.5 + tw._TAIL_MARGIN_S, abs=0.06)


def test_speech_to_the_very_end_is_left_exactly_as_it_was():
    clip = wav(take(noise(0.5, -70, 3), speech(3.0, -30)))
    out, trailing = tw._trim_silent_tail(clip)
    assert out is clip
    assert trailing == pytest.approx(0.0, abs=0.06)


def test_a_soft_last_word_after_a_pause_is_kept():
    """A quiet "thanks" 18 dB under the rest, after a short pause."""
    clip = wav(take(noise(0.5, -70, 3), speech(2.0, -30), noise(0.5, -70, 5),
                    speech(0.3, -52, seed=2), noise(2.0, -70, 6)))
    out, trailing = tw._trim_silent_tail(clip)
    word_end = 0.5 + 2.0 + 0.5 + 0.3
    assert seconds(out) >= word_end + tw._TAIL_MARGIN_S - 0.06
    assert trailing == pytest.approx(2.0, abs=0.1)


def test_a_word_barely_over_the_noise_still_counts_as_speech():
    """9 dB over the room noise: under the strict measure's bar, over the
    cut's, so the cut keeps it."""
    clip = wav(take(noise(0.5, -70, 3), speech(2.0, -30), noise(0.4, -70, 5),
                    speech(0.4, -61, seed=2), noise(1.5, -70, 6)))
    out, _ = tw._trim_silent_tail(clip)
    assert seconds(out) >= 0.5 + 2.0 + 0.4 + 0.4 + tw._TAIL_MARGIN_S - 0.06


def test_a_take_of_room_noise_alone_is_left_alone():
    """No speech found: the near-silent rules decide, not the cut."""
    clip = wav(noise(2.0, -70, 3))
    out, trailing = tw._trim_silent_tail(clip)
    assert out is clip
    assert trailing is None


def test_digital_silence_alone_is_left_alone():
    clip = wav(silence(1.5))
    out, trailing = tw._trim_silent_tail(clip)
    assert out is clip
    assert trailing is None


@pytest.mark.parametrize("tail_s,cut", [(0.5, False), (0.9, False), (1.2, True), (2.0, True)])
def test_only_a_tail_longer_than_the_minimum_is_cut(tail_s, cut):
    clip = wav(take(noise(0.5, -70, 3), speech(2.0, -30), noise(tail_s, -70, 4)))
    out, trailing = tw._trim_silent_tail(clip)
    assert trailing == pytest.approx(tail_s, abs=0.06)
    if cut:
        assert seconds(out) == pytest.approx(2.5 + tw._TAIL_MARGIN_S, abs=0.06)
    else:
        assert out is clip


def test_the_minimum_the_margin_and_the_bar_are_the_chosen_ones():
    assert tw._TAIL_MIN_S == 1.0
    assert tw._TAIL_MARGIN_S == 0.6
    assert tw._TAIL_MARGIN_S < tw._TAIL_MIN_S        # a cut always removes something
    # The cut's bar for voice is the lowest of the three measures: it errs
    # towards keeping a soft word.
    assert tw._TAIL_VOICE_RATIO == 1.5
    assert tw._TAIL_VOICE_RATIO < tw._SEGMENT_VOICE_RATIO < tw._STRICT_VOICE_RATIO


def test_a_word_only_5_db_over_the_room_is_kept():
    """A steady sound 4.8 dB over the room: over the cut's bar (1.5x, +3.5 dB),
    under 2x (+6 dB). Kept."""
    t = np.arange(int(0.4 * SR)) / SR
    hum = np.sin(2 * np.pi * 200 * t) * np.sqrt(2) * 10 ** (-67 / 20) + noise(0.4, -70, 8)
    clip = wav(take(noise(0.5, -70, 3), speech(2.0, -30) + noise(2.0, -70, 4),
                    noise(0.4, -70, 5), hum, noise(1.5, -70, 6), floor_db=None))
    out, _ = tw._trim_silent_tail(clip)
    assert seconds(out) >= 0.5 + 2.0 + 0.4 + 0.4 + tw._TAIL_MARGIN_S - 0.06
    p = tw._voice_profile(clip)
    over = 20 * np.log10(np.median(p.rms[int(2.95 / 0.03):int(3.25 / 0.03)]) / p.floor)
    assert 3.5 < over < 6.0


def test_speech_that_hardly_stands_out_from_loud_noise_is_never_cut():
    """A take in a loud room (speech a few dB over the noise, as one of the
    owner's was): the detector cannot tell speech from noise, so nothing is
    cut, however long the end looks."""
    clip = wav(take(noise(0.5, -30, 3), speech(3.0, -27), noise(2.5, -30, 4), floor_db=-30))
    out, trailing = tw._trim_silent_tail(clip)
    assert out is clip
    assert trailing is None


@pytest.mark.parametrize("make", [
    lambda: b"not a wav at all",
    lambda: wav(take(speech(1.0, -30), noise(2.0, -70)), width=1),
    lambda: wav(np.repeat(take(speech(1.0, -30), noise(2.0, -70)), 2), channels=2),
    lambda: b"",
])
def test_anything_unusual_passes_through(make):
    clip = make()
    out, trailing = tw._trim_silent_tail(clip)
    assert out is clip
    assert trailing is None


def test_a_click_after_the_speech_stops_the_cut_there():
    """Anything the detector calls voiced is kept: a knock 1.5 s after the
    last word moves the cut after the knock."""
    knock = np.zeros(int(0.06 * SR))
    knock[: int(0.02 * SR)] = 0.2
    clip = wav(take(noise(0.5, -70, 3), speech(2.0, -30), noise(1.5, -70, 4), knock,
                    noise(1.5, -70, 5)))
    out, trailing = tw._trim_silent_tail(clip)
    assert seconds(out) >= 0.5 + 2.0 + 1.5 + 0.02 + tw._TAIL_MARGIN_S - 0.06
    assert trailing == pytest.approx(1.54, abs=0.1)


# ── measuring the take ──────────────────────────────────────────────────────

def test_the_noise_floor_ignores_digital_silence():
    """The 300 ms of padding (exact zeros) used to make the floor zero, so a
    boosted room's hiss counted as speech."""
    a = np.concatenate([silence(0.3), noise(2.0, -50, 3), silence(0.3)])
    prof = tw._voice_profile(wav(a))
    assert 20 * np.log10(prof.floor / 32768) == pytest.approx(-50, abs=1.5)


def test_strict_voiced_seconds_of_room_noise_is_nothing():
    """The 27 October take: 2 s of boosted room noise measured 2.0 s of
    speech, so its "Thank you." was kept. The strict measure says none."""
    for db in (-55, -49, -38):
        clip = tw._pad_audio_with_silence(wav(noise(2.0, db, 3)))
        assert tw._speech_seconds(clip) > 1.5            # the loose measure is fooled
        assert tw._voiced_seconds(clip) < tw._MIN_VOICED_S


def test_strict_voiced_seconds_counts_real_speech():
    clip = wav(take(noise(1.0, -70, 3), speech(0.5, -40), noise(1.0, -70, 4)))
    assert tw._voiced_seconds(clip) == pytest.approx(0.5, abs=0.1)
    assert tw._voiced_seconds(b"junk") == 0.0


def test_the_speech_level_is_the_one_the_quiet_lift_measures():
    """The level kept for each take (and read by the quiet-microphone tip) is
    what boost_quiet_speech logs: the median of its voiced 30 ms frames."""
    a = take(noise(1.0, -80, 3), speech(4.0, -45), noise(1.0, -80, 4), speech(4.0, -45),
             floor_db=None)
    prof = tw._voice_profile(wav(a))
    assert prof.level_db == pytest.approx(-45, abs=1.5)
    assert tw._voice_profile(wav(silence(1.0))).level_db is None
    assert tw._voice_profile(b"junk") is None


# ── in the transcriber ──────────────────────────────────────────────────────

class _Recorder:
    def __init__(self, text="okay so ship it on monday"):
        self.text = text
        self.sent = []

    def install(self, monkeypatch):
        rec = self

        def dispatch(self, audio, exclude=None):
            rec.sent.append(audio)
            return rec.text

        monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", dispatch)
        monkeypatch.setattr(WhisperTranscriber, "_retry_if_incomplete", lambda self, ab, tr: tr)
        return self


def _transcriber():
    t = object.__new__(WhisperTranscriber)
    t._backend = "groq"
    t._cloud_order = ["groq", "openai"]
    t._groq_skip_until = 0.0
    t.client = None
    t._groq_client = object()
    return t


def test_the_upload_is_the_cut_take_with_its_padding(monkeypatch):
    rec = _Recorder().install(monkeypatch)
    clip = wav(take(noise(0.5, -70, 3), speech(3.0, -45), noise(3.0, -70, 4)))
    _transcriber().transcribe_sync(clip)
    assert len(rec.sent) == 1
    pad = 2 * 0.3
    assert seconds(rec.sent[0]) == pytest.approx(0.5 + 3.0 + tw._TAIL_MARGIN_S + pad, abs=0.06)


def test_a_take_with_no_silent_end_is_uploaded_as_before(monkeypatch):
    rec = _Recorder().install(monkeypatch)
    clip = wav(take(noise(0.5, -70, 3), speech(3.0, -30)))
    _transcriber().transcribe_sync(clip)
    assert rec.sent[0] == tw._pad_audio_with_silence(tw.boost_quiet_speech(clip))


def test_the_lift_is_measured_on_the_whole_take_before_the_cut(monkeypatch):
    """Boost first, cut after: the speech is lifted by exactly what it would
    have been without the cut."""
    rec = _Recorder().install(monkeypatch)
    clip = wav(take(noise(0.5, -75, 3), speech(3.0, -48), noise(2.5, -75, 4)))
    _transcriber().transcribe_sync(clip)
    boosted = samples(tw.boost_quiet_speech(clip))
    sent = samples(rec.sent[0])
    pad = int(0.3 * SR)
    body = sent[pad: len(sent) - pad]
    assert np.array_equal(body, boosted[: len(body)])
    assert len(body) < len(boosted)


def test_the_transcriber_keeps_the_level_of_each_take(monkeypatch):
    _Recorder().install(monkeypatch)
    t = _transcriber()
    t.transcribe_sync(wav(take(noise(1.0, -80, 3), speech(4.0, -44), floor_db=None)))
    assert t.last_speech_dbfs == pytest.approx(-44, abs=1.5)
    t.transcribe_sync(wav(silence(1.0)))
    assert t.last_speech_dbfs is None


def test_the_source_cuts_after_the_lift_and_before_the_padding():
    src = open(tw.__file__, encoding="utf-8").read()
    body = src[src.index("    def transcribe_sync(self"):]
    boost = body.index("audio_bytes = boost_quiet_speech(audio_bytes)")
    cut = body.index("_trim_silent_tail(audio_bytes", boost)
    assert cut < body.index("audio_bytes = _pad_audio_with_silence(audio_bytes)", cut)


def _long_take(minutes, extra_tail_s):
    """A take over the single-upload size (18 MB, 9.4 minutes): a minute of
    5 s phrases with 1 s pauses, repeated, ending on a pause that
    ``extra_tail_s`` more seconds of room noise make long."""
    minute = take(*[p for _ in range(10) for p in (speech(5.0, -35), noise(1.0, -70, 4))])
    pcm = np.tile((minute * 32767).astype(np.int16), minutes)
    tail = (noise(extra_tail_s, -70, 11) * 32767).astype(np.int16)
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes() + tail.tobytes())
    return b.getvalue()


def test_a_take_in_pieces_is_cut_once_before_it_is_split(monkeypatch):
    rec = _Recorder().install(monkeypatch)
    clip = _long_take(10, 2.0)                    # ends on 1 s + 2 s of room noise
    assert len(clip) > 18 * 1024 * 1024
    t = _transcriber()
    t.transcribe_sync(clip)
    assert len(rec.sent) >= 2                                   # it went up in pieces
    total = sum(seconds(c) for c in rec.sent)
    expected = seconds(clip) - 3.0 + tw._TAIL_MARGIN_S + 0.6    # cut once, padded once
    assert total == pytest.approx(expected, abs=0.1)
    tail = samples(rec.sent[-1])[-int((tw._TAIL_MARGIN_S + 0.3) * SR):]
    assert np.abs(tail).max() < 3000                       # it ends on the margin and the pad
