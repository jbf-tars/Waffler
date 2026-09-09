#!/usr/bin/env python3
"""Recording ownership: one dictation must not damage another.

`is_recording` and `_buffer` were global to the recorder with no notion of
WHICH recording owned them, so overlapping presses corrupted each other. All
three defects below were found in external review and reproduced here before
being fixed.

1. An old stop() consumes the NEXT recording's audio and disables its capture.
   stop() sleeps for the post-roll BEFORE taking ownership, so a press landing
   in that window resets the buffer and starts capturing, and then the old
   stop sets is_recording=False and drains the new recording's buffer.

2. A cold-start finalise resurrects a stopped recording. start() waits up to
   2s for live audio outside the stream lock, and its only validity check on
   return is `_stream is not None`, so a stop or cancel during warm-up is
   undone and capture silently restarts.

3. The final in-flight callback misses the snapshot. The callback tested
   `is_recording` OUTSIDE `_lock` and appended INSIDE it, so a callback
   admitted just before stop() could append its chunk to the buffer after
   stop had already snapshotted and cleared it. That audio is then discarded
   by the next start().

These use a fake stream and forced schedules: they establish the defects, not
how often they occur in the field.
"""

import os
import sys
import threading
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import audio as audio_mod  # noqa: E402
from audio import AudioRecorder  # noqa: E402


class _FakeStream:
    active = True
    def start(self): pass
    def stop(self): pass
    def close(self): pass
    def abort(self): pass


def _warm_recorder():
    """A recorder with a live-looking stream, so start() takes the warm path."""
    r = AudioRecorder()
    r._stream = _FakeStream()
    r._callback_active = True
    r._last_press_time = time.time()          # avoid the 30s force-recycle
    r._preroll.append(np.zeros((1024, 1), dtype=np.int16))
    return r


def _chunk(value):
    return np.full((1024, 1), value, dtype=np.int16)


# ── 1. An old stop must not eat the next recording ──────────────────────────

def test_old_stop_does_not_disable_the_next_recording(monkeypatch):
    r = _warm_recorder()
    r.start()
    r._buffer = [_chunk(100)]                 # recording A's audio

    started_b = threading.Event()

    def _sleep_then_start_b(_secs):
        """Force the exact schedule: B begins during A's post-roll."""
        r.start()                              # recording B
        r._buffer = [_chunk(200)]              # B's audio
        started_b.set()

    monkeypatch.setattr(audio_mod.time, "sleep", _sleep_then_start_b)
    r.stop()                                   # A's stop, arriving late

    assert started_b.is_set()
    assert r.is_recording is True, "A's stop disabled B's capture"
    assert r._buffer, "A's stop drained B's buffer"
    assert int(r._buffer[0][0][0]) == 200, "A's stop stole B's audio"


def test_superseded_stop_returns_empty_rather_than_another_recording(monkeypatch):
    r = _warm_recorder()
    r.start()
    r._buffer = [_chunk(100)]

    def _sleep_then_start_b(_secs):
        r.start()
        r._buffer = [_chunk(200)]

    monkeypatch.setattr(audio_mod.time, "sleep", _sleep_then_start_b)
    out = r.stop()
    assert out == b"", "a superseded stop must not return the newer recording's audio"


# ── 2. A stopped recording must not be resurrected by a slow warm-up ────────

def test_cold_start_cannot_resurrect_after_stop(monkeypatch):
    """stop() during the cold-start warm-up must win: capture stays off."""
    r = AudioRecorder()
    r._callback_active = True

    def _fake_create():
        r._stream = _FakeStream()
        r._preroll.append(np.zeros((1024, 1), dtype=np.int16))

    monkeypatch.setattr(r, "_create_stream", _fake_create)

    # Force the warm-up loop to exit immediately, and stop the recorder while
    # start() is between releasing the lock and finalising.
    real_sleep = time.sleep
    def _warmup_sleep(_secs):
        with r._stream_lock:
            r._session += 1          # what a concurrent stop/cancel does
        r.is_recording = False
    monkeypatch.setattr(audio_mod.time, "sleep", _warmup_sleep)

    r.start()
    assert r.is_recording is False, "a stopped recording was resurrected by start()"


# ── 3. The last callback must not append after the snapshot ────────────────

def test_callback_cannot_append_after_stop_snapshot(monkeypatch):
    """A callback admitted just before stop must either land in the returned
    WAV or be dropped, never be appended to the cleared buffer."""
    r = _warm_recorder()
    r.start()
    r._buffer = [_chunk(50)]

    leaked = []

    def _sleep_then_callback(_secs):
        # Simulate the in-flight callback racing the snapshot.
        r._callback(_chunk(99), 1024, None, None)

    monkeypatch.setattr(audio_mod.time, "sleep", _sleep_then_callback)
    r.stop()

    # After the stop completes, the buffer must not be holding a chunk that
    # was neither returned nor dropped.
    for c in r._buffer:
        if int(c[0][0]) == 99:
            leaked.append(c)
    assert not leaked, "a callback appended to the buffer after it was cleared"


def test_callback_during_recording_still_captured():
    """The guard must not break normal capture."""
    r = _warm_recorder()
    r.start()
    before = len(r._buffer)
    r._callback(_chunk(7), 1024, None, None)
    assert len(r._buffer) == before + 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
