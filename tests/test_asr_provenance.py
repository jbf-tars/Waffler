#!/usr/bin/env python3
"""The untouched speech-recognition response must remain recoverable.

Waffler keeps four distinct artifacts:

    captured audio -> ASR response -> filtered transcript -> formatted output

Before v3.14.86 only the last two survived. ``transcribe_sync`` applied the
hallucination filter, the vocab-echo discard and the boilerplate discard, then
returned the FILTERED text; ``app.py`` saved that as history ``text``, which
the UI presents as "Show original". So the provider's actual response was
never stored anywhere, and any filtering mistake was silent and permanently
unrecoverable — exactly the situation that made the 2026-09-03 truncation
report impossible to adjudicate.

The transcriber now exposes the untouched response alongside the filtered
text. These tests pin that it survives every filtering path, including the
paths that return an empty string.

Pure offline tests: no API keys, no private history, no microphone.
"""

import io
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from transcribe_whisper import WhisperTranscriber  # noqa: E402

SR = 16000


def _wav(seconds, amp=6000, freq=180.0):
    t = np.arange(int(seconds * SR)) / SR
    samples = (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(samples.tobytes())
    return out.getvalue()


def _silent_wav(seconds):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.zeros(int(seconds * SR), dtype=np.int16).tobytes())
    return out.getvalue()


def _transcriber(monkeypatch, provider_text):
    t = object.__new__(WhisperTranscriber)
    t._backend = "api"
    t._cloud_order = ["groq", "openai"]
    t._groq_skip_until = 0.0
    t.client = object()
    t._groq_client = object()
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: provider_text)
    monkeypatch.setattr(WhisperTranscriber, "_retry_if_incomplete",
                        lambda self, ab, tr: tr)
    return t


def test_raw_response_preserved_when_filter_edits_text(monkeypatch):
    """Filtering trims an appended outro; the untouched response survives."""
    provider = "So that is the plan for the sprint. Thanks for watching!"
    t = _transcriber(monkeypatch, provider)
    out = t.transcribe_sync(_wav(12.0))
    assert out == "So that is the plan for the sprint."      # filtered
    assert t.last_asr_response == provider                   # untouched
    assert t.last_asr_filtered is True


def test_raw_response_preserved_when_filter_returns_empty(monkeypatch):
    """The worst case: filtering discards everything. The provider's words
    must still be recoverable rather than gone forever."""
    provider = "Thank you."
    t = _transcriber(monkeypatch, provider)
    out = t.transcribe_sync(_silent_wav(0.6))
    assert out == ""
    assert t.last_asr_response == provider
    assert t.last_asr_filtered is True


def test_unfiltered_text_reports_no_filtering(monkeypatch):
    provider = "Can we move the review to Thursday at ten?"
    t = _transcriber(monkeypatch, provider)
    out = t.transcribe_sync(_wav(9.0))
    assert out == provider
    assert t.last_asr_response == provider
    assert t.last_asr_filtered is False


def test_raw_response_is_reset_per_recording(monkeypatch):
    """A stale raw response from an earlier recording must never be attributed
    to a later one."""
    t = _transcriber(monkeypatch, "First recording content here.")
    t.transcribe_sync(_wav(8.0))
    assert t.last_asr_response == "First recording content here."
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: "Second recording content.")
    t.transcribe_sync(_wav(8.0))
    assert t.last_asr_response == "Second recording content."


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
