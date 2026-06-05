#!/usr/bin/env python3
"""Force-clear key cache on recording stop (Windows phantom-trigger fix).

User repro: finish a Win+Ctrl dictation, and within ~1 s press Ctrl alone ->
Waffler phantom-fires a new recording. Cause: a suppressed Win keydown can
leave Win stuck "held" in the cached key-state (its key-up may never reach the
hook, and GetAsyncKeyState can't see a suppressed key). Then Ctrl alone
completes the combo from stale cache.

Fix: `_clear_key_states()` resets the cache to all-not-held whenever a recording
STOPS (push-to-talk release, sticky cancel, Esc). Pure cache reset — real
keydowns rebuild it — so it can't block a genuine press (unlike the reverted
v3.14.75 GetAsyncKeyState guard).

These tests drive the state machine directly, including simulating a MISSED Win
key-up, to prove: (1) no phantom fire after stop, (2) normal rapid re-record
still works.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

wh = pytest.importorskip("windows_hotkey", reason="windows-only module")


def _make():
    fired = {"press": 0, "release": 0}
    lis = wh.WindowsHotkeyListener(
        on_press=lambda: None, on_release=lambda: None, keys=["win", "ctrl"]
    )
    lis._fire_press = lambda: fired.__setitem__("press", fired["press"] + 1)
    lis._fire_release = lambda: fired.__setitem__("release", fired["release"] + 1)
    return lis, fired


def _press(lis, key):
    """Mimic the hook's keydown path for a configured key (state + combo check).
    The suppress/return-value logic is OS-propagation only and irrelevant to the
    state-machine outcome under test."""
    if not lis._key_states[key]:
        lis._key_states[key] = True
        lis._check_combo_press()


def _release(lis, key):
    lis._key_states[key] = False
    lis._check_release()


def test_no_phantom_after_stop_even_with_missed_win_keyup():
    lis, fired = _make()
    _press(lis, "win")
    _press(lis, "ctrl")
    assert fired["press"] == 1 and lis._state == wh._State.PUSH_TO_TALK

    # Stop by releasing Ctrl. The fix clears the whole cache here.
    _release(lis, "ctrl")
    assert fired["release"] == 1 and lis._state == wh._State.IDLE
    assert lis._key_states == {"win": False, "ctrl": False}

    # Simulate the OS NEVER delivering the Win key-up (the real-world cause).
    # Win must still be cleared (it was, by _clear_key_states on stop).

    # Now the phantom trigger: press Ctrl alone.
    _press(lis, "ctrl")
    assert fired["press"] == 1, "phantom combo fired from stale Win!"
    assert lis._state == wh._State.IDLE


def test_normal_rapid_re_record_still_works():
    lis, fired = _make()
    _press(lis, "win"); _press(lis, "ctrl")          # record 1
    _release(lis, "win"); _release(lis, "ctrl")      # stop
    _press(lis, "win"); _press(lis, "ctrl")          # record 2 immediately
    assert fired["press"] == 2, "second genuine combo did not fire"
    assert lis._state == wh._State.PUSH_TO_TALK


def test_ctrl_alone_never_fires_from_clean_state():
    lis, fired = _make()
    _press(lis, "ctrl")            # only one key of the combo
    assert fired["press"] == 0
    assert lis._state == wh._State.IDLE


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
