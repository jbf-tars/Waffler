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


# ── Windows: the same gesture through the keyboard hook ──────────────────────
WIN_RIG = """
import ctypes, json, types
from unittest import mock
ctypes.windll = types.SimpleNamespace(user32=mock.MagicMock(), kernel32=mock.MagicMock())
ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
import windows_hotkey as wh

now = [100.0]
log = []
def make(meeting=True, **kw):
    l = wh.WindowsHotkeyListener(on_press=lambda: None, on_release=lambda: None,
                                 on_cancel=lambda: None, on_lock=lambda: None,
                                 clock=lambda: now[0], **kw)
    l._fire_press = lambda: log.append('press')
    l._fire_release = lambda: log.append('release')
    l._fire_cancel = lambda: log.append('cancel')
    l._fire_lock = lambda: log.append('lock')
    if meeting:
        l.set_meeting_double_tap(lambda: None)
        l._fire_meeting_tap = lambda: log.append('meeting')
    return l

WIN, CTRL, SPACE = 0x5B, 0x11, wh.VK_SPACE
def key(l, vk, down=True):
    kb = wh.KBDLLHOOKSTRUCT(vkCode=vk, scanCode=0, flags=0, time=0)
    l._ll_keyboard_proc(0, wh.WM_KEYDOWN if down else wh.WM_KEYUP, ctypes.addressof(kb))

def run(steps, **kw):
    # P both keys down, R both up, S Space, a number = seconds that pass
    del log[:]
    l = make(**kw)
    for s in steps:
        if isinstance(s, (int, float)): now[0] += s
        elif s == 'P': key(l, WIN); key(l, CTRL)
        elif s == 'R': key(l, CTRL, False); key(l, WIN, False)
        elif s == 'S': key(l, SPACE)
    return [list(log), l._state.value]
"""


def win_isolated(code):
    code = f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n" + WIN_RIG + code
    done = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ), timeout=120)
    assert done.returncode == 0, done.stderr[-1500:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_windows_two_quick_taps_toggle_the_meeting():
    log, state = win_isolated("print(json.dumps(run(['P', 0.08, 'R', 0.12, 'P', 0.08, 'R'])))")
    assert log == ["press", "release", "press", "release", "meeting"]
    idle = win_isolated("print(json.dumps(run(['P', 2.0, 'R'])))")[1]
    assert state == idle                                   # back to idle, nothing locked


def test_windows_a_hold_and_two_slow_taps_are_dictations():
    assert win_isolated("print(json.dumps(run(['P', 2.0, 'R'])))")[0] == ["press", "release"]
    assert win_isolated("print(json.dumps(run(['P', 0.08, 'R', 1.5, 'P', 0.08, 'R'])))")[0] == [
        "press", "release", "press", "release"]


def test_windows_meeting_tap_wins_over_hands_free_and_gives_it_back_when_off():
    log, _ = win_isolated("print(json.dumps(run(['P', 0.08, 'R', 0.12, 'P', 0.08, 'R'], double_tap=True)))")
    assert log[-1] == "meeting" and "lock" not in log
    log, _ = win_isolated("print(json.dumps(run(['P', 0.08, 'R', 0.12, 'P', 0.08, 'R'], meeting=False, double_tap=True)))")
    assert "lock" in log and "meeting" not in log


def test_windows_without_either_setting_nothing_changes():
    log, _ = win_isolated("print(json.dumps(run(['P', 0.08, 'R', 0.12, 'P', 0.08, 'R'], meeting=False)))")
    assert log == ["press", "release", "press", "release"]


# ── Finishing a meeting: the bar says so, and no pop-up comes up ─────────────

def test_the_bar_is_told_when_a_meeting_has_just_finished():
    from src.overlay import RecordingOverlay as _Overlay
    sent = []
    o = _Overlay.__new__(_Overlay)
    o._is_alive = lambda: True
    o._send = sent.append
    o._log = lambda *a, **k: None
    o.set_meeting(True, 12)
    o.set_meeting(True, 0, done=True)
    o.set_meeting(False)
    assert [(m["on"], m["done"]) for m in sent] == [(True, False), (True, True), (False, False)]
    assert all(m["type"] == "meeting" for m in sent)


def test_on_a_mac_starting_and_finishing_a_meeting_raise_no_pop_up():
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent
    app = (root / "app.py").read_text(encoding="utf-8")
    body = app[app.index("    def _on_meeting_double_tap(self):"):app.index("    def apply_double_tap(self):")]
    mac = body.index('elif _platform.system() == "Darwin":')
    assert body.index("self._say_meeting_failed(result)") < mac          # an error is still said
    said = app[app.index("    def _say_meeting_failed(self, result):"):]
    said = said[:said.index("\n    # ──")]
    assert '"The meeting couldn\'t be recorded"' in said and "_show_toast_safe(" in said
    # ...once, by the small pill, however the meeting was started: the window
    # no longer says it as well, nor that a meeting started or finished.
    toggle = app[app.index("    def meetings_toggle(self) -> dict:"):]
    toggle = toggle[:toggle.index("\n    def ", 10)]
    assert "_pipeline._say_meeting_failed(result)" in toggle
    page = (root / "ui" / "meetings.js").read_text(encoding="utf-8")
    for gone in ("Recording the meeting", "Meeting finished", "couldn’t be recorded"):
        assert gone not in page, gone
    assert mac < body.index('"Recording the meeting"') and mac < body.index('"Meeting finished"')
    bar = (root / "src" / "overlay_process.py").read_text(encoding="utf-8")
    assert '"Writing the notes"' in bar and "if not _meet_done and x >=" in bar
    assert "set_bar(True, 0, done=True)" in app and "MEETING_DONE_SECONDS" in app
