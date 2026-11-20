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


# ── Two FAST taps of Fn arrive as one press (mac_hotkey_monitor.FnHandler) ────
# The second tap lands inside the 150 ms hold-quiet window, so the listener
# never sees the first release. The handler recognises the shape instead.
FN_RIG = """
import json, types
import mac_hotkey_monitor as m

now = [50.0]
m.time = types.SimpleNamespace(monotonic=lambda: now[0])
m._fire = lambda cb: cb() if cb else None
m.CGEventGetFlags = lambda flags: flags            # the 'event' is just its flags

timers = []
class Timer:
    def __init__(self, delay, fn, args=()):
        self.fn, self.args, self.dead, self.daemon = fn, args, False, True
    def start(self): timers.append(self)
    def cancel(self): self.dead = True
m.threading = types.SimpleNamespace(Timer=Timer, Lock=__import__('threading').Lock,
                                    current_thread=__import__('threading').current_thread)

log = []
h = m.FnHandler(on_press=lambda: log.append('press'),
                on_release=lambda: log.append('release:' + ('double' if h.take_double_tap() else 'single')))
DOWN, UP = m._FN_FLAG | 0x100, 0x100

def run(steps):
    # 'D' Fn down, 'U' Fn up, a number = seconds that pass (a pending release fires after 0.15 quiet)
    for s in steps:
        if isinstance(s, (int, float)):
            end = now[0] + s
            while timers and not timers[0].dead and now[0] + 0.15 <= end + 1e-9 and False:
                pass
            # fire the pending release if it has been quiet for 150 ms within this wait
            live = [t for t in timers if not t.dead]
            if live and s >= 0.15:
                now[0] += 0.15
                t = live[-1]; del timers[:]; t.fn(*t.args)
                now[0] = end
            else:
                now[0] = end
        else:
            h.handle(m.kCGEventFlagsChanged, DOWN if s == 'D' else UP)
    return log
"""


def fn_isolated(code):
    code = f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n" + FN_RIG + code
    done = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ), timeout=120)
    assert done.returncode == 0, done.stderr[-1500:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@mac_only
def test_two_fast_fn_taps_merged_into_one_press_are_seen_as_a_double_tap():
    # down 90 ms, up 80 ms (inside the hold-quiet window), down 90 ms, up for good
    assert fn_isolated("print(json.dumps(run(['D', 0.09, 'U', 0.08, 'D', 0.09, 'U', 0.3])))") == ["press", "release:double"]


@mac_only
def test_one_short_fn_tap_is_single():
    assert fn_isolated("print(json.dumps(run(['D', 0.09, 'U', 0.3])))") == ["press", "release:single"]


@mac_only
def test_a_flicker_during_a_long_hold_is_not_a_double_tap():
    # held 2 s, the OS drops the flag for 80 ms, held another 2 s
    assert fn_isolated("print(json.dumps(run(['D', 2.0, 'U', 0.08, 'D', 2.0, 'U', 0.3])))") == ["press", "release:single"]


@mac_only
def test_a_flicker_at_the_start_of_a_hold_is_not_a_double_tap():
    # a short first stretch, a dip, then a real hold: still one dictation
    assert fn_isolated("print(json.dumps(run(['D', 0.1, 'U', 0.08, 'D', 1.5, 'U', 0.3])))") == ["press", "release:single"]


@mac_only
def test_a_few_millisecond_bounce_is_not_a_double_tap():
    assert fn_isolated("print(json.dumps(run(['D', 0.09, 'U', 0.01, 'D', 0.09, 'U', 0.3])))") == ["press", "release:single"]


@mac_only
def test_three_dips_are_not_a_double_tap():
    assert fn_isolated("print(json.dumps(run(['D', 0.09, 'U', 0.08, 'D', 0.09, 'U', 0.08, 'D', 0.09, 'U', 0.3])))") == ["press", "release:single"]
