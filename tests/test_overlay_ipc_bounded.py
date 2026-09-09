#!/usr/bin/env python3
"""A wedged overlay must not be able to stall a dictation.

The overlay is a separate process driven over stdin. A child that has DIED
raises BrokenPipeError immediately and was always handled. A child that is
ALIVE but has stopped reading is the dangerous case: the pipe buffer fills and
write/flush block forever while holding the send lock.

That mattered because `on_hotkey_release` called `overlay.hide()` BEFORE
spawning the processing thread, so a stuck child meant the audio was never
snapshotted and a finished recording was lost. Two changes:

  * processing is started before the overlay is touched, so keeping the user's
    words never depends on the UI being responsive;
  * every stdin write is bounded, and a child that will not drain is killed
    rather than allowed to wedge the app.

Level updates run ~30 times a second and are cosmetic, so they take the lock
only if it is free and are dropped otherwise.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import overlay as ov  # noqa: E402
from overlay import RecordingOverlay  # noqa: E402


class _WedgedStdin:
    """Alive, but never drains: every write blocks until released."""
    def __init__(self):
        self.release = threading.Event()
    def write(self, _data):
        self.release.wait(30)
    def flush(self):
        self.release.wait(30)


class _FakeProc:
    def __init__(self, stdin):
        self.stdin = stdin
        self.killed = False
    def poll(self):
        return None            # alive
    def kill(self):
        self.killed = True
        try:
            self.stdin.release.set()   # killing breaks the pipe
        except Exception:
            pass


def _overlay_with(stdin):
    o = object.__new__(RecordingOverlay)
    o._process = _FakeProc(stdin)
    o._send_lock = threading.Lock()
    o._visible = False
    o._log = lambda *a, **k: None
    return o


def test_write_to_a_wedged_child_gives_up_rather_than_blocking(monkeypatch):
    monkeypatch.setattr(ov, "_WRITE_TIMEOUT_S", 0.3)
    stdin = _WedgedStdin()
    o = _overlay_with(stdin)
    t0 = time.time()
    ok = o._write_bounded("hello\n")
    elapsed = time.time() - t0
    stdin.release.set()
    assert ok is False
    assert elapsed < 3.0, f"blocked for {elapsed:.1f}s"
    assert o._process.killed, "a child that will not drain must be terminated"


def test_healthy_child_write_succeeds():
    class _OK:
        def __init__(self): self.data = []
        def write(self, d): self.data.append(d)
        def flush(self): pass
    stdin = _OK()
    o = _overlay_with(stdin)
    assert o._write_bounded("payload\n") is True
    assert stdin.data == ["payload\n"]


def test_write_errors_still_propagate():
    """A genuinely dead child must keep raising, so the existing restart path
    continues to fire."""
    class _Dead:
        def write(self, _d): raise BrokenPipeError("gone")
        def flush(self): pass
    o = _overlay_with(_Dead())
    with pytest.raises(BrokenPipeError):
        o._write_bounded("x\n")


def test_level_update_is_dropped_when_the_lock_is_busy(monkeypatch):
    """Cosmetic frames must never queue up behind a stalled writer."""
    o = _overlay_with(_WedgedStdin())
    o._send_lock.acquire()                       # simulate a writer in progress
    try:
        t0 = time.time()
        sent = o._send({"type": "level", "value": 0.5}, best_effort=True)
        assert sent is False
        assert time.time() - t0 < 0.5, "a level frame waited on a busy lock"
    finally:
        o._send_lock.release()


def test_non_level_command_gives_up_on_a_busy_lock(monkeypatch):
    """Important commands wait, but not forever."""
    monkeypatch.setattr(ov, "_SEND_LOCK_TIMEOUT_S", 0.3)
    o = _overlay_with(_WedgedStdin())
    o._send_lock.acquire()
    try:
        t0 = time.time()
        sent = o._send({"type": "hide"})
        elapsed = time.time() - t0
        assert sent is False
        assert elapsed < 3.0, f"waited {elapsed:.1f}s on a busy lock"
    finally:
        o._send_lock.release()


def test_lock_is_released_after_a_send():
    """A leaked lock would stall every later command."""
    class _OK:
        def write(self, d): pass
        def flush(self): pass
    o = _overlay_with(_OK())
    o._send({"type": "hide"})
    assert o._send_lock.acquire(blocking=False), "send lock was not released"
    o._send_lock.release()


def test_processing_starts_before_the_overlay_is_touched():
    """Structural guard on the release path: nothing about keeping the user's
    words may depend on the overlay being responsive."""
    import re, pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def on_hotkey_release"):]
    body = body[:body.index("def toggle_pause")]
    spawn = body.index("self._process(current_id)")
    hide = body.index("self.overlay.hide()")
    assert spawn < hide, "overlay.hide() runs before processing is spawned"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
