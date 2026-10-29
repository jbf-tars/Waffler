"""3.16.3: a retry the segments show is not needed is not sent.

The incomplete-transcript retry fires when a transcript has under 1.5 words
for each second of measured speech. That measure counts a lifted room's hiss
as speech, so on a quiet microphone a slow, complete take looked short: in two
weeks of the owner's logs the retry fired 13 times, every one "retry no
better", each costing 2 to 8 seconds.

The check itself is unchanged. When it fires and Groq's segments came with
the transcript, Waffler first looks for clear voice (3x the floor) in the
upload that no segment covers, allowing half a second either side of each.
A transcript that dropped speech leaves voiced audio with no segment over it;
a slow speaker or a lifted room does not. Under 1.0 s of it, the retry is not
sent; otherwise, and whenever there are no usable segments, it is, as before.
So the rule can only remove retries the old one would send.

Measured on the owner's ten takes: complete transcripts left at most 0.54 s
uncovered; the three retries the old rule sent (one slow take, as recorded and
with 2 or 4 s of room noise) left 0.00 s. With the last half of each answer's
segments dropped, the old rule fired on 8 and so did the new one; with the
middle third dropped, 2 and 2. Uncovered voice in those was 1.5 s or more.

Synthetic audio and fake providers: no network.
"""
import io
import os
import sys
import types
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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


# Five 5 s phrases, each followed by 3 s of room: phrase k runs from 8k to 8k + 5 s.
PHRASES = [(8.0 * k, 8.0 * k + 5.0) for k in range(5)]


def take(speech_db=-30, room_db=-75):
    parts = []
    for i in range(5):
        parts += [_speech(5.0, speech_db, seed=i), np.zeros(int(3.0 * SR))]
    a = np.concatenate(parts)
    return _wav(a + _noise(len(a) / SR, room_db, seed=3))


CLEAN = take()


def voiced_in(prof, start, end):
    return tw._voiced_between(prof, start, end, tw._STRICT_VOICE_RATIO)


# ── the coverage arithmetic ─────────────────────────────────────────────────

def test_segments_over_every_phrase_leave_nothing_uncovered():
    assert tw._uncovered_voice(CLEAN, PHRASES) == 0.0


def test_no_segments_leave_all_the_clear_voice_uncovered():
    assert tw._uncovered_voice(CLEAN, []) == pytest.approx(tw._voiced_seconds(CLEAN), abs=0.01)
    assert tw._voiced_seconds(CLEAN) == pytest.approx(25.0, abs=1.0)


def test_half_a_second_either_side_counts_as_covered():
    """Whisper's times are loose: a segment that ends 0.4 s before its words
    do still covers them; one that ends 1.2 s early leaves 0.7 s."""
    short = [(s, e - 0.4) for s, e in PHRASES]
    assert tw._uncovered_voice(CLEAN, short) == pytest.approx(0.0, abs=0.04)
    late_start = [(s + 0.4, e) for s, e in PHRASES]
    assert tw._uncovered_voice(CLEAN, late_start) == pytest.approx(0.0, abs=0.04)
    one_early = PHRASES[:4] + [(PHRASES[4][0], PHRASES[4][1] - 1.2)]
    assert tw._uncovered_voice(CLEAN, one_early) == pytest.approx(0.7, abs=0.1)
    assert tw._uncovered_voice(CLEAN, one_early, margin=0.0) == pytest.approx(1.2, abs=0.1)


def test_overlapping_segments_are_counted_once():
    split = [(0.0, 3.0), (2.0, 5.0)] + PHRASES[1:]
    assert tw._uncovered_voice(CLEAN, split) == 0.0
    doubled = PHRASES + PHRASES
    assert tw._uncovered_voice(CLEAN, doubled) == 0.0


def test_a_phrase_in_a_gap_between_segments_is_uncovered():
    gap = PHRASES[:2] + PHRASES[3:]       # the middle phrase has no segment; its
    prof = tw._voice_profile(CLEAN)       # neighbours' margins end 2.5 s short of it
    expected = voiced_in(prof, *PHRASES[2])
    assert tw._uncovered_voice(CLEAN, gap) == pytest.approx(expected, abs=0.1)
    assert expected == pytest.approx(5.0, abs=0.1)


def test_segments_past_the_end_of_the_audio_are_clamped():
    past = PHRASES[:4] + [(PHRASES[4][0], 500.0), (600.0, 700.0), (-5.0, 0.2)]
    assert tw._uncovered_voice(CLEAN, past) == 0.0


def test_soft_sound_outside_the_segments_is_not_clear_voice():
    """Only the strict bar counts: 5 dB over the room is not a dropped word."""
    t = np.arange(int(2.0 * SR)) / SR
    hum = np.sin(2 * np.pi * 200 * t) * np.sqrt(2) * 10 ** (-70 / 20)
    a = np.concatenate([_speech(5.0, -30), hum, np.zeros(int(3.0 * SR))])
    clip = _wav(a + _noise(len(a) / SR, -75, seed=4))
    assert tw._uncovered_voice(clip, [(0.0, 5.0)]) == 0.0


def test_unreadable_audio_says_nothing():
    assert tw._uncovered_voice(b"not a wav", PHRASES) is None


def test_the_margin_and_the_bar_are_the_chosen_ones():
    assert tw._COVER_MARGIN_S == 0.5 and tw._MIN_UNCOVERED_VOICE_S == 1.0


# ── the gate: the old check AND voice the segments missed ───────────────────

def heard(text, spans):
    h = tw._Heard(text)
    h.spans = spans
    return h


def words(n):
    return " ".join(["word"] * n)


class Rig:
    def __init__(self, monkeypatch, alt=None):
        self.calls = []
        rig = self

        def dispatch(self, audio, exclude=None):
            rig.calls.append(exclude)
            return alt if alt is not None else words(80)

        monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", dispatch)
        t = object.__new__(WhisperTranscriber)
        t._backend = "groq"
        t._cloud_order = ["groq", "openai"]
        t._groq_skip_until = 0.0
        t.client = object()
        t._groq_client = object()
        t._last_cloud_provider = "groq"
        t.last_retry_fired = False
        t.last_retry_rejected = False
        self.t = t

    def retry(self, transcript, audio=CLEAN):
        self.t._retry_if_incomplete(audio, transcript)
        return bool(self.calls)


SLOW = 30                    # 30 words for ~25-40 s of speech: under 1.5 a second


def one_early(seconds):
    """Every phrase covered, the last segment ending ``seconds`` early."""
    s, e = PHRASES[4]
    return PHRASES[:4] + [(s, e - seconds)]


@pytest.mark.parametrize("label,transcript,retried", [
    ("old check quiet, even with voice uncovered", heard(words(80), PHRASES[:1]), False),
    ("old check fires, no segments: the old rule alone", words(SLOW), True),
    ("old check fires, segments with no times", heard(words(SLOW), None), True),
    ("old check fires, every phrase covered", heard(words(SLOW), PHRASES), False),
    ("old check fires, two phrases with no segment", heard(words(SLOW), PHRASES[:3]), True),
    ("old check fires, 1.3 s uncovered", heard(words(SLOW), one_early(1.8)), True),
    ("old check fires, 0.8 s uncovered", heard(words(SLOW), one_early(1.3)), False),
])
def test_the_retry_needs_the_old_check_and_voice_the_segments_missed(monkeypatch, label, transcript, retried):
    rig = Rig(monkeypatch)
    assert rig.retry(transcript) is retried, label


def test_a_derailed_ending_is_retried_however_well_covered(monkeypatch):
    """"...and the rest of the team." replaces a window's words but its
    segment sits over that window's voice: coverage cannot see that loss."""
    rig = Rig(monkeypatch)
    text = words(120) + ". And the rest of the team."
    assert rig.retry(heard(text, PHRASES)) is True


def test_unreadable_audio_keeps_the_old_rule(monkeypatch):
    rig = Rig(monkeypatch)
    odd = b"not a wav at all"
    rig.t._measured_speech = (odd, 30.0)
    rig.t._retry_if_incomplete(odd, heard(words(SLOW), PHRASES))
    assert rig.calls == ["groq"]


def test_a_skipped_retry_is_logged_without_words(monkeypatch):
    lines = []
    monkeypatch.setattr(tw, "_wlog", lines.append)
    rig = Rig(monkeypatch)
    rig.retry(heard(words(SLOW), PHRASES))
    assert any("no retry" in line for line in lines)
    assert not any("word word" in line for line in lines)
    assert rig.t.last_retry_fired is False


# ── where the spans come from ───────────────────────────────────────────────

def seg(text, start, end, nsp=0.05):
    return {"text": text, "start": start, "end": end, "no_speech_prob": nsp}


def answer(*segments, text=None):
    joined = "".join(s["text"] for s in segments)
    return types.SimpleNamespace(text=joined if text is None else text, segments=list(segments))


def test_groq_words_carry_their_segments_spans():
    out = tw._groq_words(answer(seg(" Ship it.", 0.3, 2.0), seg(" Then tell Malak.", 2.0, 3.5)),
                         tw._pad_audio_with_silence(CLEAN))
    assert out.spans == [(0.3, 2.0), (2.0, 3.5)]


@pytest.mark.parametrize("response", [
    "plain text",                                                       # an SDK that sends text
    answer(seg(" Ship it.", 0.3, 2.0), text=" Something else."),         # segments not the text
    types.SimpleNamespace(text=" Ship it.", segments="nope"),            # unreadable segments
    answer(seg(" Ship it.", None, 2.0)),                                 # a segment with no times
])
def test_without_usable_segments_there_are_no_spans(response):
    out = tw._groq_words(response, tw._pad_audio_with_silence(CLEAN))
    assert out.spans is None


def test_a_dropped_invented_ending_is_not_a_span():
    up = tw._pad_audio_with_silence(_wav(np.concatenate(
        [_speech(2.0, -30), np.zeros(int(2.0 * SR))]) + _noise(4.0, -75, seed=5)))
    out = tw._groq_words(answer(seg(" Ship it.", 0.3, 2.3), seg(" Thank you.", 2.4, 4.0, 0.1)), up)
    assert out == "Ship it." and out.spans == [(0.3, 2.3)]


# ── a take in pieces ────────────────────────────────────────────────────────

def test_pieces_add_up_their_uncovered_voice(monkeypatch):
    first, second = CLEAN, take(speech_db=-32)
    answers = iter([heard(words(40), PHRASES[:4]), heard(words(40), PHRASES[:3])])
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, audio, exclude=None: next(answers))
    t = object.__new__(WhisperTranscriber)
    out = t._dispatch_pieces([first, second])
    expected = tw._uncovered_voice(first, PHRASES[:4]) + tw._uncovered_voice(second, PHRASES[:3])
    assert out.uncovered_s == pytest.approx(expected, abs=0.01) and expected > 5.0


def test_a_piece_without_segments_leaves_the_take_on_the_old_rule(monkeypatch):
    answers = iter([heard(words(40), PHRASES), words(40)])
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, audio, exclude=None: next(answers))
    t = object.__new__(WhisperTranscriber)
    assert t._dispatch_pieces([CLEAN, CLEAN]).uncovered_s is None


def test_a_take_in_pieces_is_gated_by_the_sum(monkeypatch):
    rig = Rig(monkeypatch)
    covered = tw._Heard(words(SLOW))
    covered.uncovered_s = 0.4
    assert rig.retry(covered) is False
    missed = tw._Heard(words(SLOW))
    missed.uncovered_s = 6.0
    assert rig.retry(missed) is True
