"""The cancel key (S11a): Esc by default, and a different single key when the
person chooses one.

Esc is read by the dictation hotkey's listener (src/smart_hotkey.py on a Mac,
src/windows_hotkey.py on Windows). That listener's state machine is the most
delicate code in the app, so this file first PINS what it does today with the
default key, then shows that the only thing a chosen key changes is which key
the cancel handler listens for.

Neither listener is started: no event tap and no hook is installed. The Mac
pieces are run in a fresh interpreter (the real Quartz is imported but never
asked for a tap); the Windows module is imported with its Win32 calls replaced
by stand-ins, so the hook procedure can be driven from any computer.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS (Quartz)")


def isolated(code):
    """Run ``code`` in a fresh interpreter and return the JSON on its last line."""
    code = f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n" + code
    done = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ), timeout=60)
    assert done.returncode == 0, done.stderr[-1200:]
    return json.loads(done.stdout.strip().splitlines()[-1])


# A Mac listener whose key events are fakes, and whose callbacks run at once.
MAC_RIG = """
import json, threading, types
import mac_hotkey_monitor as m
import smart_hotkey

class Ev:
    def __init__(self, keycode=0, flags=0):
        self.keycode, self.flags = keycode, flags

m.CGEventGetIntegerValueField = lambda e, f: e.keycode
m.CGEventGetFlags = lambda e: e.flags
m._fire = lambda cb: cb() if cb else None          # no threads: the order is what is shown

class Thread:
    def __init__(self, target=None, args=(), daemon=None, **kw):
        self.target, self.args = target, args
    def start(self):
        self.target(*self.args)
smart_hotkey.threading = types.SimpleNamespace(Thread=Thread, current_thread=threading.current_thread)

log = []
def make(**kw):
    return smart_hotkey.SmartHotkeyListener(
        on_press=lambda: log.append('press'), on_release=lambda: log.append('release'),
        on_cancel=lambda: log.append('cancel'), on_lock=lambda: log.append('lock'), **kw)
"""


# ── pinned: what the Mac listener does today, with the default key ───────────

@mac_only
def test_pinned_the_default_mac_listener_has_the_handlers_it_always_had():
    got = isolated(MAC_RIG + """
fn = make()
custom = make(keys=['cmd', 'shift', 'k'])
def shape(l):
    return [[type(h).__name__, getattr(h, '_keys', None), getattr(h, '_suppress', None)] for h in l._tap._handlers]
print(json.dumps({'fn': shape(fn), 'custom': shape(custom)}))
""")
    assert got["fn"] == [["FnHandler", None, None], ["SpaceHandler", None, None],
                         ["GenericHotkeyHandler", ["esc"], False]]
    assert got["custom"] == [["GenericHotkeyHandler", ["cmd", "shift", "k"], True],
                             ["GenericHotkeyHandler", ["space"], False],
                             ["GenericHotkeyHandler", ["esc"], False]]


@mac_only
def test_pinned_esc_cancels_a_recording_and_is_otherwise_left_alone():
    got = isolated(MAC_RIG + """
ESC = 53
l = make()
esc = l._esc_handler
out = {}
# Not recording, not processing: Esc is nothing, and it is never swallowed.
out['idle_swallowed'] = esc.handle(10, Ev(ESC))
esc.handle(11, Ev(ESC))
out['idle'] = list(log)
# Recording (held): Esc cancels and clears the listener's state.
l._recording, l._hotkey_held, l._sticky = True, True, True
out['rec_swallowed'] = esc.handle(10, Ev(ESC))
esc.handle(11, Ev(ESC))
out['rec'] = list(log)
out['state'] = [l._recording, l._hotkey_held, l._sticky]
# While the pipeline's offer is on screen, Esc stops the wait.
del log[:]
l.set_processing(True)
esc.handle(10, Ev(ESC)); esc.handle(11, Ev(ESC))
out['processing'] = list(log)
# Another key is not Esc.
del log[:]
l._recording = True
esc.handle(10, Ev(49)); esc.handle(10, Ev(0))
out['other_keys'] = list(log)
print(json.dumps(out))
""")
    assert got == {"idle_swallowed": False, "idle": [], "rec_swallowed": False, "rec": ["cancel"],
                   "state": [False, False, False], "processing": ["cancel"], "other_keys": []}


@mac_only
def test_pinned_the_fn_and_generic_hotkeys_still_start_and_stop_a_recording():
    got = isolated(MAC_RIG + """
FN = 0x800000
l = make()
fn = l._fn_handler
fn.handle(12, Ev(0, FN))
import time; time.sleep(0.01)
out = {'after_press': list(log)}
l._on_hotkey_release()
out['after_release'] = list(log)
print(json.dumps(out))
""")
    assert got == {"after_press": ["press"], "after_release": ["press", "release"]}


# ── pinned: what the Windows listener does today, with the default key ───────

# windows_hotkey needs Win32 at import. Here its calls are stand-ins, so the
# hook procedure can be driven with key events from any computer. The hook is
# never installed.
WIN_RIG = """
import ctypes, json, types
from unittest import mock
ctypes.windll = types.SimpleNamespace(user32=mock.MagicMock(), kernel32=mock.MagicMock())
ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
import windows_hotkey as wh

log = []
def make(**kw):
    l = wh.WindowsHotkeyListener(on_press=lambda: None, on_release=lambda: None,
                                 on_cancel=lambda: None, on_lock=lambda: None, **kw)
    l._fire_press = lambda: log.append('press')
    l._fire_release = lambda: log.append('release')
    l._fire_cancel = lambda: log.append('cancel')
    l._fire_lock = lambda: log.append('lock')
    return l

def key(l, vk, down=True, flags=0):
    kb = wh.KBDLLHOOKSTRUCT(vkCode=vk, scanCode=0, flags=flags, time=0)
    l._ll_keyboard_proc(0, wh.WM_KEYDOWN if down else wh.WM_KEYUP, ctypes.addressof(kb))
"""


def test_pinned_windows_esc_cancels_a_recording_and_is_otherwise_left_alone():
    got = isolated(WIN_RIG + """
l = make()
out = {}
key(l, wh.VK_ESCAPE)                    # idle: nothing
out['idle'] = list(log)
key(l, 0x5B); key(l, 0x11)              # Win + Ctrl: push to talk
out['talk'] = [list(log), l._state.value]
key(l, wh.VK_ESCAPE)
out['esc'] = [list(log), l._state.value, dict(l._key_states)]
del log[:]
l.set_processing(True)                  # the "still working" offer
key(l, wh.VK_ESCAPE)
out['processing'] = list(log)
del log[:]
l.set_processing(False)
key(l, 0x70)                            # F1 is not Esc
out['other'] = list(log)
print(json.dumps(out))
""")
    assert got == {"idle": [], "talk": [["press"], "push_to_talk"],
                   "esc": [["press", "cancel"], "idle", {"win": False, "ctrl": False}],
                   "processing": ["cancel"], "other": []}


def test_pinned_windows_space_still_locks_hands_free():
    got = isolated(WIN_RIG + """
l = make()
key(l, 0x5B); key(l, 0x11); key(l, wh.VK_SPACE)
print(json.dumps([log, l._state.value]))
""")
    assert got == [["press", "lock"], "sticky"]


# ── a chosen cancel key: the only thing that changes is which key is listened for ─

@mac_only
def test_a_chosen_f_key_cancels_a_recording_and_esc_no_longer_does():
    got = isolated(MAC_RIG + """
F16 = 106
l = make(cancel_key='f16')
esc = l._esc_handler
out = {'shape': [type(h).__name__ for h in l._tap._handlers], 'keys': esc._keys, 'suppress': esc._suppress}
l._recording, l._hotkey_held = True, True
esc.handle(10, Ev(53))                      # Esc
esc.handle(11, Ev(53))
out['esc'] = list(log)
out['esc_swallowed'] = esc.handle(10, Ev(53))
out['f16_swallowed'] = esc.handle(10, Ev(F16))
esc.handle(11, Ev(F16))
out['f16'] = list(log)
out['state'] = [l._recording, l._hotkey_held, l._sticky]
print(json.dumps(out))
""")
    assert got == {"shape": ["FnHandler", "SpaceHandler", "GenericHotkeyHandler"], "keys": ["f16"],
                   "suppress": False, "esc": [], "esc_swallowed": False, "f16_swallowed": False,
                   "f16": ["cancel"], "state": [False, False, False]}


@mac_only
def test_a_chosen_cancel_key_is_the_one_handler_the_default_one_was_and_changes_nothing_else():
    got = isolated(MAC_RIG + """
def shape(l):
    return [[type(h).__name__, getattr(h, '_keys', None), getattr(h, '_suppress', None)] for h in l._tap._handlers]
default = make()
chosen = make(cancel_key='f9')
print(json.dumps({'same_but_the_last': shape(default)[:-1] == shape(chosen)[:-1],
                  'default_last': shape(default)[-1], 'chosen_last': shape(chosen)[-1],
                  'custom': shape(make(keys=['cmd', 'shift', 'k'], cancel_key='f9'))}))
""")
    assert got["same_but_the_last"] is True
    assert got["default_last"] == ["GenericHotkeyHandler", ["esc"], False]
    assert got["chosen_last"] == ["GenericHotkeyHandler", ["f9"], False]
    assert got["custom"] == [["GenericHotkeyHandler", ["cmd", "shift", "k"], True],
                             ["GenericHotkeyHandler", ["space"], False],
                             ["GenericHotkeyHandler", ["f9"], False]]


@mac_only
@pytest.mark.parametrize("bad", ["'a'", "'space'", "'f21'", "''", "None", "5", "'fn'", "'escape'", "['f9']"])
def test_anything_but_esc_or_an_f_key_is_esc(bad):
    got = isolated(MAC_RIG + f"""
l = make(cancel_key={bad})
print(json.dumps([l._esc_handler._keys, l._esc_handler._suppress]))
""")
    assert got == [["esc"], False]


@mac_only
def test_the_hotkeys_own_key_table_is_untouched_by_a_cancel_key():
    got = isolated(MAC_RIG + """
before = dict(m.KEY_TO_KEYCODE)
make(cancel_key='f9'); make(cancel_key='f20')
print(json.dumps(before == m.KEY_TO_KEYCODE and 'f9' not in m.KEY_TO_KEYCODE and 'f20' not in m.KEY_TO_KEYCODE))
""")
    assert got is True


@mac_only
def test_a_cancel_key_works_with_the_generic_handlers_other_arguments_left_as_they_were():
    """GenericHotkeyHandler takes an optional table of key codes; the hotkey,
    the Space trigger and the default Esc handler never pass one."""
    got = isolated("""
import json, mac_hotkey_monitor as m
h = m.GenericHotkeyHandler(keys=['esc'], on_press=lambda: None, on_release=lambda: None, suppress=False)
g = m.GenericHotkeyHandler(keys=['f9'], on_press=lambda: None, on_release=lambda: None, suppress=False,
                           key_codes={'f9': 101})
print(json.dumps([h._codes is m.KEY_TO_KEYCODE, h._regular_keys, g._codes, g._regular_keys]))
""")
    assert got == [True, ["esc"], {"f9": 101}, ["f9"]]


def test_windows_a_chosen_f_key_cancels_and_esc_no_longer_does():
    got = isolated(WIN_RIG + """
l = make(cancel_key='f9')
out = {'vk': l._cancel_vk}
key(l, 0x5B); key(l, 0x11)              # Win + Ctrl: push to talk
del log[:]
key(l, wh.VK_ESCAPE)
out['esc'] = [list(log), l._state.value]
key(l, 0x78)                            # F9
out['f9'] = [list(log), l._state.value, dict(l._key_states)]
print(json.dumps(out))
""")
    assert got == {"vk": 0x78, "esc": [[], "push_to_talk"], "f9": [["cancel"], "idle", {"win": False, "ctrl": False}]}


def test_windows_the_default_is_esc_and_anything_else_is_esc():
    got = isolated(WIN_RIG + """
print(json.dumps([make()._cancel_vk, make(cancel_key='esc')._cancel_vk, make(cancel_key='a')._cancel_vk,
                  make(cancel_key=None)._cancel_vk, make(cancel_key='fn')._cancel_vk,
                  make(cancel_key='f1')._cancel_vk, make(cancel_key='F12')._cancel_vk, wh.VK_ESCAPE]))
""")
    assert got == [0x1B, 0x1B, 0x1B, 0x1B, 0x1B, 0x70, 0x7B, 0x1B]


def test_windows_a_cancel_key_that_is_part_of_the_hotkey_is_the_hotkeys_key_first():
    """Not reachable through Settings (the hotkey may not include the cancel
    key), but if a settings file were edited by hand the hotkey still wins."""
    got = isolated(WIN_RIG + """
l = make(keys=['shift', 'f9'], cancel_key='f9')
key(l, 0x10); key(l, 0x78)
print(json.dumps([log, l._state.value]))
""")
    assert got == [["press"], "push_to_talk"]
