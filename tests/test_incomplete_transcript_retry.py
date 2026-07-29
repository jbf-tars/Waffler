#!/usr/bin/env python3
"""Tests for the incomplete-transcript detector + cross-provider retry.

The bug this guards against (found live, 2026-07-29): a 73.8s recording with
57 MEASURED seconds of speech came back from the transcription API as 18 words
(0.32 words per speech-second) — ~85% of the dictation silently vanished at
the Whisper layer. Capture was proven complete (wall-clock == captured audio)
and styling kept 100% of its input, so the loss is entirely server-side.

Real speech is never slower than ~1 word/sec sustained, so when
words / speech_seconds collapses below that, the transcript is near-certainly
incomplete. Waffler now detects this and retries on the alternate cloud
provider, keeping whichever transcript has more words.

Also pins the Groq upload-size gate: a ~23MB near-max recording made Groq
fail with a connection error after a long stall (proven live with a 23.2MB
clip; a 7.7MB clip worked). Clips over the gate skip Groq and go straight to
OpenAI instead of wasting a doomed upload.
"""

import io
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import transcribe_whisper as tw  # noqa: E402
from transcribe_whisper import WhisperTranscriber, _speech_seconds  # noqa: E402

SR = 16000


def _wav(samples: np.ndarray) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.astype(np.int16).tobytes())
    return out.getvalue()


def _tone(seconds, amp=6000, freq=180.0):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _silence(seconds):
    return np.zeros(int(seconds * SR), dtype=np.int16)


# ── _speech_seconds ──────────────────────────────────────────────────────────

def test_speech_seconds_measures_loud_portion():
    clip = _wav(np.concatenate([_tone(10.0), _silence(5.0), _tone(5.0)]))
    s = _speech_seconds(clip)
    assert 12.0 <= s <= 16.5, s  # ~15s of tone, tolerance for windowing


def test_speech_seconds_all_silence_is_zero():
    assert _speech_seconds(_wav(_silence(8.0))) <= 0.5


def test_speech_seconds_malformed_is_zero():
    assert _speech_seconds(b"not a wav") == 0.0


# ── the retry decision ───────────────────────────────────────────────────────

def _styler_free_transcriber():
    """A bare instance with just the attributes the retry logic touches."""
    t = object.__new__(WhisperTranscriber)
    t._backend = "api"
    t._cloud_order = ["groq", "openai"]
    t._groq_skip_until = 0.0
    t.client = object()        # OpenAI present
    t._groq_client = object()  # Groq present
    return t


def test_incomplete_transcript_triggers_retry_and_takes_better(monkeypatch):
    """57s of speech -> 18 words is impossible; the alternate provider's full
    transcript (200 words) must win."""
    t = _styler_free_transcriber()
    audio = _wav(_tone(60.0))

    short = " ".join(["word"] * 18)
    full = " ".join(["word"] * 200)

    calls = []

    def fake_dispatch(self, ab, exclude=None):
        calls.append(exclude)
        return full if exclude == "groq" else short

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", fake_dispatch)
    t._last_cloud_provider = "groq"  # set by the real first dispatch
    out = t._retry_if_incomplete(audio, short)
    assert out == full
    assert calls == ["groq"], calls  # retried excluding the provider that failed


def test_retry_keeps_first_when_alternate_is_worse(monkeypatch):
    t = _styler_free_transcriber()
    audio = _wav(_tone(60.0))
    short = " ".join(["word"] * 18)

    def fake_dispatch(self, ab, exclude=None):
        return " ".join(["word"] * 10)  # alternate even worse

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", fake_dispatch)
    t._last_cloud_provider = "groq"
    out = t._retry_if_incomplete(audio, short)
    assert out == short


def test_healthy_transcript_never_retries(monkeypatch):
    """1.5+ words per speech-second is normal slow speech — no retry."""
    t = _styler_free_transcriber()
    audio = _wav(_tone(60.0))
    healthy = " ".join(["word"] * 100)  # ~1.7 wps on 60s tone

    def boom(self, ab, exclude=None):
        raise AssertionError("must not re-dispatch a healthy transcript")

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", boom)
    t._last_cloud_provider = "groq"
    assert t._retry_if_incomplete(audio, healthy) == healthy


def test_short_clips_exempt(monkeypatch):
    """<10s of speech is too little signal — a curt 'yes' must not retry."""
    t = _styler_free_transcriber()
    audio = _wav(_tone(6.0))

    def boom(self, ab, exclude=None):
        raise AssertionError("short clips must not retry")

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", boom)
    t._last_cloud_provider = "groq"
    assert t._retry_if_incomplete(audio, "yes") == "yes"


def test_local_backends_exempt(monkeypatch):
    """mlx / faster-whisper have no alternate provider — never retry."""
    t = _styler_free_transcriber()
    t._backend = "mlx"
    audio = _wav(_tone(60.0))

    def boom(self, ab, exclude=None):
        raise AssertionError("local backends must not retry")

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", boom)
    assert t._retry_if_incomplete(audio, "tiny") == "tiny"


def test_retry_failure_keeps_first_result(monkeypatch):
    """If the alternate provider errors, the original stump still comes back."""
    t = _styler_free_transcriber()
    audio = _wav(_tone(60.0))
    short = " ".join(["word"] * 18)

    def fail(self, ab, exclude=None):
        raise RuntimeError("alternate provider down")

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", fail)
    t._last_cloud_provider = "groq"
    assert t._retry_if_incomplete(audio, short) == short


# ── the Groq upload-size gate ────────────────────────────────────────────────

def test_dispatch_skips_groq_for_oversized_upload(monkeypatch):
    """23MB+ uploads make Groq stall and die (proven live) — go straight to
    OpenAI."""
    t = _styler_free_transcriber()
    big = b"\x00" * (tw._GROQ_MAX_UPLOAD_BYTES + 1)
    used = []
    monkeypatch.setattr(
        WhisperTranscriber, "_transcribe_groq",
        lambda self, ab: used.append("groq") or "groq text")
    monkeypatch.setattr(
        WhisperTranscriber, "_transcribe_api",
        lambda self, ab: used.append("openai") or "openai text")
    out = t._dispatch_one(big)
    assert out == "openai text"
    assert used == ["openai"], used


def test_dispatch_uses_groq_for_normal_size(monkeypatch):
    t = _styler_free_transcriber()
    small = b"\x00" * (1024 * 1024)
    used = []
    monkeypatch.setattr(
        WhisperTranscriber, "_transcribe_groq",
        lambda self, ab: used.append("groq") or "groq text")
    monkeypatch.setattr(
        WhisperTranscriber, "_transcribe_api",
        lambda self, ab: used.append("openai") or "openai text")
    out = t._dispatch_one(small)
    assert out == "groq text"
    assert used == ["groq"], used


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
