#!/usr/bin/env python3
"""Guard against the stale-cached-modifier phantom-trigger bug (Windows).

Repro the user hit: the hotkey is Win+Ctrl. The low-level hook missed a Win
key-up, so `_key_states['win']` stayed stuck True in the cache. Then pressing
Ctrl alone made `_all_keys_held()` return True from stale cache and fired the
combo — popping a phantom recording overlay with no real recording.

The fix re-polls the ACTUAL hardware state (GetAsyncKeyState) in
`_check_combo_press`; if a configured key isn't physically down, it resyncs and
bails. These tests prove:
  1. stale cache (win cached-held but physically up) + Ctrl press -> NO fire,
  2. genuine combo (both physically down) -> fires normally.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

# windows_hotkey imports ctypes.windll at module load — Windows only.
wh = pytest.importorskip("windows_hotkey", reason="windows-only module")


def _make_listener():
    fired = {"press": 0, "release": 0}
    lis = wh.WindowsHotkeyListener(
        on_press=lambda: fired.__setitem__("press", fired["press"] + 1),
        on_release=lambda: fired.__setitem__("release", fired["release"] + 1),
        keys=["win", "ctrl"],
    )
    # _fire_press spawns a thread + calls on_press; count synchronously instead.
    lis._fire_press = lambda: fired.__setitem__("press", fired["press"] + 1)
    lis._fire_release = lambda: fired.__setitem__("release", fired["release"] + 1)
    return lis, fired


def test_stale_cached_win_does_not_fire_on_ctrl_alone(monkeypatch):
    lis, fired = _make_listener()
    # Stale cache: both marked held, but hardware says only Ctrl is down.
    lis._key_states = {"win": True, "ctrl": True}
    win_vks = set(wh.KEY_TO_VK["win"])
    monkeypatch.setattr(wh, "_key_down", lambda vk: vk not in win_vks)

    lis._check_combo_press()

    assert fired["press"] == 0, "phantom press fired from stale cache"
    assert lis._state == wh._State.IDLE
    # Cache must have been resynced: win corrected to not-held.
    assert lis._key_states["win"] is False


def test_genuine_combo_fires(monkeypatch):
    lis, fired = _make_listener()
    lis._key_states = {"win": True, "ctrl": True}
    # Hardware agrees: every configured key is physically down.
    monkeypatch.setattr(wh, "_key_down", lambda vk: True)

    lis._check_combo_press()

    assert fired["press"] == 1, "genuine combo did not fire"
    assert lis._state == wh._State.PUSH_TO_TALK


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
