#!/usr/bin/env python3
"""Holding the hotkey must not start, stop and restart a recording.

The polling fallback runs when the low-level keyboard hook cannot be installed.
It only samples key state, so a combination still being held after a state
change read as a brand new press. Holding Ctrl+Win+Space produced:

    IDLE   + held  -> PUSH_TO_TALK   (press fires)
    PTT    + space -> STICKY
    STICKY + held  -> IDLE           (release fires)   <- same, unmoved keys
    IDLE   + held  -> PUSH_TO_TALK   (press fires)     <- and again

start, stop, start, with the user never moving a finger, which then feeds the
recording-loss paths this release series has been fixing. A held combination is
now one activation until the keys are seen released.

Windows-only: windows_hotkey binds ctypes.WINFUNCTYPE at import.
"""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only hotkey polling", allow_module_level=True)

import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import windows_hotkey as wh  # noqa: E402
from windows_hotkey import WindowsHotkeyListener, _State  # noqa: E402


def _run_poll(key_frames, monkeypatch):
    """Drive the real poll loop over a scripted sequence of key samples.

    Each frame is (combo_held, space_down). Returns the ordered list of
    press/release/cancel events the listener fired.
    """
    events = []
    listener = WindowsHotkeyListener(
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
        on_cancel=lambda: events.append("cancel"),
    )

    frames = list(key_frames)
    state = {"i": 0}

    def fake_key_down(vk):
        idx = min(state["i"], len(frames) - 1)
        held, space = frames[idx]
        if vk == wh.VK_SPACE:
            return space
        if vk == wh.VK_ESCAPE:
            return False
        return held

    monkeypatch.setattr(wh, "_key_down", fake_key_down)

    # Step one frame per loop iteration and stop when the script runs out, so
    # the real state machine is exercised deterministically.
    def step(_seconds):
        state["i"] += 1
        if state["i"] >= len(frames):
            listener._running = False

    monkeypatch.setattr(wh.time, "sleep", step)
    listener._running = True
    listener._poll_fallback()
    return events


def test_holding_the_combo_fires_exactly_one_press(monkeypatch):
    """The reported reproduction: keys held down, unchanged, across polls."""
    frames = [(True, False)] + [(True, True)] * 5
    events = _run_poll(frames, monkeypatch)
    assert events.count("press") == 1, f"held keys fired {events.count('press')} presses: {events}"
    assert "release" not in events, f"held keys produced a stop: {events}"


def test_sticky_cancel_requires_a_release_first(monkeypatch):
    """After entering sticky, the combo must be released and pressed again
    before it means cancel."""
    frames = (
        [(True, False)]        # press combo -> PUSH_TO_TALK
        + [(True, True)] * 2   # add space   -> STICKY (combo still held)
        + [(False, False)] * 2 # let go      -> latch clears, still recording
        + [(True, False)] * 2  # fresh press -> cancel sticky
    )
    events = _run_poll(frames, monkeypatch)
    assert events == ["press", "release"], events


def test_normal_push_to_talk_still_works(monkeypatch):
    frames = [(True, False)] * 3 + [(False, False)] * 2
    events = _run_poll(frames, monkeypatch)
    assert events == ["press", "release"], events


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
