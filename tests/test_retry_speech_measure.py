"""3.16.3: the words-per-second checks judge speech with the strict measure.

The incomplete-transcript retry (and the quality flag "low_word_rate") divide
a transcript's words by its seconds of speech, and act under 1.5. The old
measure, _speech_seconds, counts a lifted room's hiss as speech: on the
owner's quiet-mic takes it counted 97% of each take's length (median). In two
weeks of his logs the retry fired 13 times, at 0.15 to 1.50 words a second,
and every one came back "retry no better ... keeping original", each costing
2 to 8 seconds. Of his ten latest takes, with and without room noise added,
the old measure fired on 2 of 20, the strict one on none; the slowest healthy
take reads 1.61 words a second by it, so 1.5 stays.

_speech_for_rate is the strict measure (_voiced_seconds) where the take has a
floor to measure from, and the old measure where it does not (speech from end
to end, or a room too loud to tell). History's ``speech_s`` keeps the old
measure. Synthetic audio and fake providers: no network.
"""
import io
import json
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import quality  # noqa: E402
import transcribe_whisper as tw  # noqa: E402
from transcribe_whisper import WhisperTranscriber  # noqa: E402

SR = 16000


def _speech(seconds, db, seed=0):
    t = np.arange(int(seconds * SR)) / SR
    v = np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t + seed))
    return v * (10 ** (db / 20) / np.sqrt(np.mean(v ** 2)))


def _noise(seconds, db, seed=1):
    n = np.random.default_rng(seed).normal(0.0, 1.0, int(seconds * SR))
    return n * (10 ** (db / 20) / np.sqrt(np.mean(n ** 2)))


def _wav(a):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
    return b.getvalue()


def take(speech_db, room_db):
    """40 s of dictation: five 5.6 s phrases with 2.4 s pauses, the room's
    noise under all of it."""
    parts = []
    for i in range(5):
        parts += [_speech(5.6, speech_db, seed=i), np.zeros(int(2.4 * SR))]
    a = np.concatenate(parts)
    return _wav(a + _noise(len(a) / SR, room_db, seed=3))


QUIET_MIC = take(-45, -58)     # speech 13 dB over the room: the lift raises both by 25 dB
CLEAN = take(-30, -75)


def upload(raw):
    return tw._pad_audio_with_silence(tw.boost_quiet_speech(raw))


def words(n):
    return " ".join(["word"] * n)


class Rig:
    """A transcriber whose first answer is ``first`` and whose retry, on the
    other provider, answers ``alt``; it records each call's ``exclude``."""

    def __init__(self, monkeypatch, first, alt=None):
        self.calls = []
        rig = self

        def dispatch(self, audio, exclude=None):
            rig.calls.append(exclude)
            return first if exclude is None else (alt or first)

        monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", dispatch)
        monkeypatch.setattr(tw, "load_vocab", lambda: [])
        t = object.__new__(WhisperTranscriber)
        t._backend = "groq"
        t._cloud_order = ["groq", "openai"]
        t._groq_skip_until = 0.0
        t.client = object()
        t._groq_client = object()
        t._last_cloud_provider = "groq"
        self.t = t

    def retried(self):
        return any(c is not None for c in self.calls)


# ── the measure ─────────────────────────────────────────────────────────────

def test_the_old_measure_counts_a_lifted_room_as_speech_and_the_new_one_does_not():
    loose = tw._speech_seconds(upload(QUIET_MIC))
    judged = tw._speech_for_rate(QUIET_MIC)
    assert loose == pytest.approx(40.0, abs=0.7)          # every window, pauses and all
    assert 15.0 <= judged <= 22.0                          # the phrases' voiced part


def test_on_a_clean_take_the_two_agree():
    assert tw._speech_for_rate(CLEAN) == pytest.approx(tw._speech_seconds(upload(CLEAN)), abs=0.5)


@pytest.mark.parametrize("make", [
    lambda: _wav(np.sin(2 * np.pi * 180 * np.arange(40 * SR) / SR) * 0.2),   # speech end to end
    lambda: take(-27, -30),                                                   # a room too loud to tell
])
def test_with_no_floor_to_measure_from_the_old_measure_decides(make):
    clip = make()
    assert tw._speech_for_rate(clip) == tw._speech_seconds(clip)
    assert tw._speech_for_rate(clip, loose=12.5) == 12.5


def test_the_threshold_is_unchanged():
    assert tw._MIN_WORDS_PER_SPEECH_SEC == 1.5 == quality._MIN_WORDS_PER_SPEECH_SEC


# ── the retry ───────────────────────────────────────────────────────────────

def test_a_quiet_mics_complete_transcript_is_not_retried(monkeypatch):
    """35 words for 40 s of "speech" by the old measure (0.88 a second) is a
    retry; by the strict one it is about 1.9 a second, a slow speaker."""
    assert 35 / tw._speech_seconds(upload(QUIET_MIC)) < 1.5               # the old measure fired
    rig = Rig(monkeypatch, words(35))
    assert rig.t.transcribe_sync(QUIET_MIC) == words(35)
    assert not rig.retried() and rig.t.last_retry_fired is False


def test_a_transcript_missing_half_of_a_clearly_voiced_take_still_retries(monkeypatch):
    full, half = words(70), words(35)          # 70 words is 2.5 a second of its voiced speech
    rig = Rig(monkeypatch, half, alt=full)
    assert rig.t.transcribe_sync(CLEAN) == full
    assert rig.calls == [None, "groq"] and rig.t.last_retry_fired is True


def test_the_full_transcript_of_that_take_is_not_retried(monkeypatch):
    rig = Rig(monkeypatch, words(70))
    rig.t.transcribe_sync(CLEAN)
    assert not rig.retried()


def test_a_quiet_mics_transcript_missing_most_of_it_still_retries(monkeypatch):
    rig = Rig(monkeypatch, words(12), alt=words(40))
    assert rig.t.transcribe_sync(QUIET_MIC) == words(40)
    assert rig.t.last_retry_fired is True


def test_the_retry_called_on_its_own_measures_the_same_way(monkeypatch):
    rig = Rig(monkeypatch, words(35))
    up = upload(QUIET_MIC)
    assert rig.t._retry_if_incomplete(up, words(35)) == words(35)
    assert not rig.retried()


def test_history_keeps_the_old_measure_and_the_checks_get_the_new(monkeypatch):
    rig = Rig(monkeypatch, words(35))
    rig.t.transcribe_sync(QUIET_MIC)
    cut, _ = tw._trim_silent_tail(tw.boost_quiet_speech(QUIET_MIC), tw._voice_profile(QUIET_MIC))
    sent = tw._pad_audio_with_silence(cut)                  # what went up: its silent end cut
    assert rig.t.last_speech_seconds == tw._speech_seconds(sent)
    assert rig.t.last_judged_speech_seconds == pytest.approx(tw._speech_for_rate(QUIET_MIC), abs=0.01)
    assert rig.t.last_speech_seconds > 1.8 * rig.t.last_judged_speech_seconds


# ── the quality flag, in the app (tests/_pipeline_harness.py) ───────────────

import _pipeline_harness as H  # noqa: E402


class Measured(H.FakeTranscriber):
    def __init__(self, text, loose, judged):
        super().__init__(text=text)
        self.loose, self.judged = loose, judged

    def transcribe_sync(self, audio_bytes):
        out = super().transcribe_sync(audio_bytes)
        self.last_speech_seconds = self.loose
        self.last_judged_speech_seconds = self.judged
        return out


def dictate(tmp_path, transcriber, monkeypatch):
    H.fast_limits(monkeypatch)
    p = H.make_pipeline(tmp_path, transcriber=transcriber)
    t = H.run_process(p, 1)
    t.join(10)
    assert not t.is_alive()
    return p


def test_the_quality_flag_judges_by_the_same_measure(tmp_path, monkeypatch):
    p = dictate(tmp_path, Measured(words(30), loose=40.0, judged=18.0), monkeypatch)
    entry = H.history(p)[-1]
    assert "low_word_rate" not in (entry.get("quality") or {}).get("flags", [])
    assert entry["speech_s"] == 40.0                            # History keeps the old measure
    row = json.loads((tmp_path / "quality.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["speech_s"] == 40.0 and row["rate_speech_s"] == 18.0
    assert row["words_per_speech_second"] == pytest.approx(30 / 18.0, abs=0.01)


def test_a_real_loss_is_still_flagged(tmp_path, monkeypatch):
    p = dictate(tmp_path, Measured(words(12), loose=40.0, judged=30.0), monkeypatch)
    assert "low_word_rate" in H.history(p)[-1]["quality"]["flags"]


def test_a_transcriber_that_does_not_say_is_judged_as_before(tmp_path, monkeypatch):
    plain = H.FakeTranscriber(text=words(12))
    plain_sync = plain.transcribe_sync

    def sync(audio):
        out = plain_sync(audio)
        plain.last_speech_seconds = 30.0
        return out
    plain.transcribe_sync = sync
    p = dictate(tmp_path, plain, monkeypatch)
    assert "low_word_rate" in H.history(p)[-1]["quality"]["flags"]
    _, info = p._transcribe_with_provenance(b"x")
    assert info["last_judged_speech_seconds"] is None
