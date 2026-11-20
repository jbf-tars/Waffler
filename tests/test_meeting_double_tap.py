"""Two quick taps of the hotkey start or finish a meeting (src/smart_hotkey.py).

The listener is never started: no event tap, no key sent. Time is injected.
Same rig as tests/test_double_tap.py, with a clock the scenario can move.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS (Quartz)")

RIG = """
import json, threading, types
import mac_hotkey_monitor as m
import smart_hotkey

m._fire = lambda cb: cb() if cb else None

class Thread:
    def __init__(self, target=None, args=(), daemon=None, **kw):
        self.target, self.args = target, args
    def start(self):
        self.target(*self.args)
smart_hotkey.threading = types.SimpleNamespace(Thread=Thread, current_thread=threading.current_thread)

now = [100.0]
log = []
def run(steps, meeting=True, **kw):
    # P press, R release, S Space, a number = seconds that pass.
    del log[:]
    l = smart_hotkey.SmartHotkeyListener(
        on_press=lambda: log.append('press'), on_release=lambda: log.append('release'),
        on_cancel=lambda: log.append('cancel'), on_lock=lambda: log.append('lock'),
        clock=lambda: now[0], **kw)
    if meeting:
        l.set_meeting_double_tap(lambda: log.append('meeting'))
    for s in steps:
        if isinstance(s, (int, float)):
            now[0] += s
        else:
            {'P': l._on_hotkey_press, 'R': l._on_hotkey_release, 'S': l._on_space_press}[s]()
    return [list(log), [l._recording, l._sticky]]
"""


def isolated(code):
    code = f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n" + RIG + code
    done = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ), timeout=120)
    assert done.returncode == 0, done.stderr[-1500:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@mac_only
def test_two_quick_taps_toggle_the_meeting_and_paste_nothing_extra():
    log, state = isolated("print(json.dumps(run(['P', 0.08, 'R', 0.12, 'P', 0.08, 'R'])))")
    # each tap is an ordinary short take (the app drops those); the second also toggles
    assert log == ["press", "release", "press", "release", "meeting"]
    assert state == [False, False]                      # nothing left recording or locked


@mac_only
def test_a_hold_is_still_a_dictation():
    log, state = isolated("print(json.dumps(run(['P', 2.0, 'R'])))")
    assert log == ["press", "release"] and state == [False, False]


@mac_only
def test_two_slow_taps_are_two_dictations():
    log, _ = isolated("print(json.dumps(run(['P', 0.08, 'R', 1.5, 'P', 0.08, 'R'])))")
    assert log == ["press", "release", "press", "release"]


@mac_only
def test_space_still_locks_hands_free_with_meetings_on():
    log, state = isolated("print(json.dumps(run(['P', 0.5, 'S', 0.2, 'R'])))")
    assert "lock" in log and "meeting" not in log
    assert state == [True, True]                        # still recording, locked on


@mac_only
def test_meeting_tap_wins_over_the_hands_free_double_tap_setting():
    log, state = isolated("print(json.dumps(run(['P', 0.08, 'R', 0.12, 'P', 0.08, 'R'], double_tap=True)))")
    assert log[-1] == "meeting" and "lock" not in log and state == [False, False]


@mac_only
def test_switched_off_gives_double_tap_back_to_hands_free():
    log, state = isolated("print(json.dumps(run(['P', 0.08, 'R', 0.12, 'P', 0.08, 'R'], meeting=False, double_tap=True)))")
    assert "lock" in log and "meeting" not in log and state == [True, True]


@mac_only
def test_without_either_setting_nothing_changes():
    log, _ = isolated("print(json.dumps(run(['P', 0.08, 'R', 0.12, 'P', 0.08, 'R'], meeting=False)))")
    assert log == ["press", "release", "press", "release"]
