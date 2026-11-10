"""Hotkey+Space going hands-free, and ending it, is in app.log (Mac).

A hands-free take read in the log as a press, a release "IGNORED
(recording=True sticky=True)", a second press "IGNORED (already recording)" and
then "Recording stopped": nothing said that Space had locked the take or ended
it, because those two lines were only printed, and a packaged app's prints go
nowhere. On one Mac 62 of 80 takes were hands-free. Space is typed all day, so
only the two presses that change something are logged, never Space itself.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS (Quartz)")

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

lines = []
smart_hotkey._diag_log = lines.append

def run(steps):
    l = smart_hotkey.SmartHotkeyListener(on_press=lambda: None, on_release=lambda: None,
                                         on_cancel=lambda: None, on_lock=lambda: None)
    del lines[:]
    for s in steps:
        {'P': l._on_hotkey_press, 'R': l._on_hotkey_release, 'S': l._on_space_press}[s]()
    return [x.split('] ', 1)[1] for x in lines if 'Space' in x]
"""


def isolated(code):
    code = f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n" + RIG + code
    done = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ), timeout=120)
    assert done.returncode == 0, done.stderr[-1500:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_space_locking_a_take_and_ending_it_are_each_one_line():
    got = isolated("print(json.dumps(run('PSRPS')))")
    assert got == ["→ hotkey+Space: hands-free ON (fire_lock)",
                   "→ hotkey+Space: hands-free OFF (fire_release)"]


def test_space_typed_at_any_other_time_is_not_logged():
    got = isolated("print(json.dumps([run('S'), run('SS'), run('PRS'), run('PSRS')]))")
    assert got == [[], [], [], ["→ hotkey+Space: hands-free ON (fire_lock)"]]
