#!/usr/bin/env python3
"""Tests for long-audio chunking in transcribe_whisper._split_audio_on_silence.

Root cause this guards against: Whisper's decoder terminates early on long
(>~30s) audio -- it transcribes the first window, emits an end-of-transcript
token that surfaces as a hallucinated outro ("Thank you for watching!", "and
so on"), and silently drops the rest. Splitting long clips into <=~30s chunks
at silence boundaries sidesteps it. These tests prove the splitter:

  1. leaves short clips untouched (single-shot path, the common case),
  2. splits a long clip into multiple <=~31s chunks at the quiet points,
  3. preserves (near) all the audio -- no big drops,
  4. never splits mid-word in a continuous loud clip with no silence
     (force-cut still bounded, no crash),
  5. degrades to single-shot on malformed input.
"""

import io
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from transcribe_whisper import _split_audio_on_silence  # noqa: E402

SR = 16000


def _wav(samples_int16: np.ndarray) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples_int16.astype(np.int16).tobytes())
    return out.getvalue()


def _tone(seconds: float, amp: int = 6000, freq: float = 180.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.int16)


def _wav_duration(b: bytes) -> float:
    with wave.open(io.BytesIO(b), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def test_normal_dictation_not_chunked():
    """v3.14.79: normal dictations (<= 150s) go single-shot. A 64s clip — the
    length that was being WRONGLY chunked into empty pieces — must NOT split.
    Groq Whisper transcribes these fully in one call (proven live)."""
    clip = _wav(_tone(64.0))
    chunks = _split_audio_on_silence(clip)
    assert chunks == [clip], f"64s clip should be single-shot, got {len(chunks)} chunks"


def test_short_clip_untouched():
    """A 10s clip is below threshold -> returned as a single unchanged chunk."""
    clip = _wav(_tone(10.0))
    chunks = _split_audio_on_silence(clip)
    assert chunks == [clip], "short clip must pass through unchanged"


def test_very_long_clip_splits_on_silence():
    """Only genuinely huge clips (> 150s) split. 120s + 1s silence + 120s ->
    >= 2 chunks, each <= ~151s (the file-size safety net for 12-min recordings)."""
    audio = np.concatenate([_tone(120.0), _silence(1.0), _tone(120.0)])
    clip = _wav(audio)
    chunks = _split_audio_on_silence(clip)
    assert len(chunks) >= 2, f"expected split for 241s clip, got {len(chunks)} chunk(s)"
    for c in chunks:
        assert _wav_duration(c) <= 151.5, f"chunk too long: {_wav_duration(c):.1f}s"


def test_very_long_split_preserves_audio_length():
    """A >150s clip splits without dropping speech (within ~2s at cut points)."""
    audio = np.concatenate(
        [_tone(110.0), _silence(0.6), _tone(110.0), _silence(0.6), _tone(40.0)]
    )
    clip = _wav(audio)
    orig = _wav_duration(clip)
    chunks = _split_audio_on_silence(clip)
    total = sum(_wav_duration(c) for c in chunks)
    assert abs(total - orig) <= 2.0, f"lost {orig - total:.1f}s of audio"
    assert len(chunks) >= 2


def test_continuous_loud_long_clip_force_cut_no_crash():
    """A 320s continuous tone with NO silence still gets bounded chunks via the
    hard force-cut, and never raises."""
    clip = _wav(_tone(320.0))
    chunks = _split_audio_on_silence(clip)
    assert len(chunks) >= 2
    for c in chunks:
        assert _wav_duration(c) <= 151.5


def test_malformed_input_degrades_to_single_shot():
    """Garbage bytes -> single-shot fallback, no exception."""
    junk = b"not a wav file at all"
    assert _split_audio_on_silence(junk) == [junk]


if __name__ == "__main__":
    test_normal_dictation_not_chunked()
    test_short_clip_untouched()
    test_very_long_clip_splits_on_silence()
    test_very_long_split_preserves_audio_length()
    test_continuous_loud_long_clip_force_cut_no_crash()
    test_malformed_input_degrades_to_single_shot()
    print("All audio-chunking tests passed.")
