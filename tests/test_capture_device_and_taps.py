#!/usr/bin/env python3
"""Guards for three deterministic data-loss defects found in external review.

All three lose the user's speech with no rare timing needed, and all three
reported success while doing it.

1. The microphone picker did not control capture. `AudioRecorder.__init__`
   took no device argument and had no setter, and `_resolve_input_device()`
   independently resolved the OS default. Selecting a microphone in Settings
   stored an index that never reached stream creation, so the app recorded
   from a different source while telling the user the choice had been applied.

2. Any press under 500 ms was discarded on PRESS DURATION alone, before the
   audio was examined. A deliberate short answer ("Yes", "No", a number) was
   deleted with no transcription, no history, no toast and no debug audio.

3. A failed clipboard write still went on to paste, so whatever unrelated text
   was already on the clipboard replaced the user's selection, and the run was
   reported as successful.
"""

import io
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import audio as audio_mod  # noqa: E402
from audio import AudioRecorder, _resolve_input_device  # noqa: E402
from transcribe_whisper import _speech_seconds  # noqa: E402

SR = 16000


def _wav(seconds, amp=6000):
    t = np.arange(int(seconds * SR)) / SR
    samples = (amp * np.sin(2 * np.pi * 180.0 * t)).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(samples.tobytes())
    return out.getvalue()


def _silence(seconds):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.zeros(int(seconds * SR), dtype=np.int16).tobytes())
    return out.getvalue()


# ── 1. The microphone picker must control capture ───────────────────────────

def test_explicit_selection_wins(monkeypatch):
    """A chosen device is used verbatim, with no default/Bluetooth reasoning."""
    monkeypatch.setattr(audio_mod.sd, "query_devices",
                        lambda *a, **k: {"name": "Yeti", "max_input_channels": 2})
    assert _resolve_input_device(3) == 3


def test_selection_survives_a_bluetooth_default(monkeypatch):
    """The AirPods-avoidance path must not override an explicit choice: the
    user asking for a Bluetooth mic is a decision, not an accident."""
    def q(idx=None, kind=None):
        if idx == 7:
            return {"name": "AirPods Pro", "max_input_channels": 1}
        return {"name": "AirPods Pro", "max_input_channels": 1}
    monkeypatch.setattr(audio_mod.sd, "query_devices", q)
    assert _resolve_input_device(7) == 7


def test_unusable_selection_falls_back_rather_than_failing(monkeypatch):
    """An unplugged or output-only device must not break recording."""
    def q(idx=None, kind=None):
        if idx == 9:
            return {"name": "Speakers", "max_input_channels": 0}
        return {"name": "Built-in Microphone", "max_input_channels": 2}
    monkeypatch.setattr(audio_mod.sd, "query_devices", q)
    assert _resolve_input_device(9) is None


def test_missing_selection_falls_back(monkeypatch):
    def q(idx=None, kind=None):
        if idx == 42:
            raise ValueError("no such device")
        return {"name": "Built-in Microphone", "max_input_channels": 2}
    monkeypatch.setattr(audio_mod.sd, "query_devices", q)
    assert _resolve_input_device(42) is None


def test_no_selection_keeps_default_behaviour(monkeypatch):
    monkeypatch.setattr(audio_mod.sd, "query_devices",
                        lambda *a, **k: {"name": "Built-in Microphone",
                                         "max_input_channels": 2})
    assert _resolve_input_device(None) is None


def test_recorder_accepts_and_updates_device():
    r = AudioRecorder(device_index=4)
    assert r._device_index == 4
    r.set_device(6)
    assert r._device_index == 6
    r.set_device(None)
    assert r._device_index is None


# ── 2. Short presses are judged on audio, not on press duration ─────────────

def test_spoken_word_in_a_short_press_is_real_speech():
    """A deliberate "Yes" is ~0.3s of voiced audio: above the tap threshold,
    so the pipeline must transcribe it rather than discard it."""
    assert _speech_seconds(_wav(0.35)) >= 0.15


def test_accidental_tap_has_essentially_no_speech():
    assert _speech_seconds(_silence(0.3)) < 0.15


def test_threshold_separates_the_two():
    from app_tap_threshold import MIN_TAP_SPEECH_S  # see conftest shim below
    assert _speech_seconds(_silence(0.3)) < MIN_TAP_SPEECH_S <= _speech_seconds(_wav(0.35))


# ── 3. A failed clipboard write must not paste ──────────────────────────────

def test_clipboard_copy_reports_failure(monkeypatch):
    """copy() must return False on failure so the caller can refuse to paste.
    The pipeline previously discarded this value."""
    import clipboard as clip
    monkeypatch.setattr(clip.pyperclip, "copy",
                        lambda t: (_ for _ in ()).throw(RuntimeError("no clipboard")))
    assert clip.ClipboardManager.copy("hello") is False


def test_clipboard_copy_reports_success(monkeypatch):
    import clipboard as clip
    monkeypatch.setattr(clip.pyperclip, "copy", lambda t: None)
    assert clip.ClipboardManager.copy("hello") is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
