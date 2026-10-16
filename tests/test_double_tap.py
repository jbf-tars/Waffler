"""Double-tap to go hands-free (S11b).

The dictation hotkey's two listeners are separate state machines
(src/smart_hotkey.py on a Mac, src/windows_hotkey.py on Windows) and the most
delicate code in the app, so this file first PINS what they do today, key by
key, with the setting off, and only then adds what double-tap changes.

Neither listener is started: no event tap and no hook is installed, no key is
sent. The Mac pieces run in a fresh interpreter (the real Quartz is imported
but never asked for a tap); the Windows module is imported there with its
Win32 calls replaced by stand-ins, so its hook procedure can be driven from
any computer. Time is injected, so no test sleeps for a tap.
"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import double_tap  # noqa: E402
import user_messages  # noqa: E402

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS (Quartz)")


def isolated(code):
    """Run ``code`` in a fresh interpreter and return the JSON on its last line."""
    code = f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n" + code
    done = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ), timeout=120)
    assert done.returncode == 0, done.stderr[-1500:]
    return json.loads(done.stdout.strip().splitlines()[-1])


# A Mac listener whose callbacks run at once and are logged in order. The
# scenario is a string of events: P a press of the hotkey, R its release,
# S Space, E Esc (the cancel key), X reset_state (what the app calls when it
# stops a take itself). `kw` is passed to the listener, so the same scenario
# runs with the setting absent, off or on.
MAC_RIG = """
import json, threading, types
import mac_hotkey_monitor as m
import smart_hotkey

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

def run(steps, **kw):
    del log[:]
    l = make(**kw)
    for s in steps:
        {'P': l._on_hotkey_press, 'R': l._on_hotkey_release, 'S': l._on_space_press,
         'E': l._on_esc_press, 'X': l.reset_state}[s]()
    return [list(log), [l._recording, l._sticky, l._hotkey_held]]
"""

# A Windows listener, hook procedure driven by key events. Win + Ctrl is the
# hotkey; `key(l, vk, down)` is one event, `combo(l, down)` both keys.
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

WIN, CTRL, SPACE, ESC = 0x5B, 0x11, wh.VK_SPACE, wh.VK_ESCAPE
def key(l, vk, down=True, flags=0):
    kb = wh.KBDLLHOOKSTRUCT(vkCode=vk, scanCode=0, flags=flags, time=0)
    l._ll_keyboard_proc(0, wh.WM_KEYDOWN if down else wh.WM_KEYUP, ctypes.addressof(kb))

def run(steps, **kw):
    # P both keys down (Win then Ctrl), R both up (Ctrl then Win), S Space, E Esc,
    # c Ctrl alone down, u Ctrl alone up, r a repeat of Ctrl's key-down.
    del log[:]
    l = make(**kw)
    for s in steps:
        if s == 'P': key(l, WIN); key(l, CTRL)
        elif s == 'R': key(l, CTRL, False); key(l, WIN, False)
        elif s == 'S': key(l, SPACE)
        elif s == 'E': key(l, ESC)
        elif s == 'c': key(l, CTRL)
        elif s == 'u': key(l, CTRL, False)
        elif s == 'r': key(l, CTRL)
    return [list(log), l._state.value]
"""


# ── pinned: the Mac listener today (the setting off, or not there) ───────────

# How a listener is made for the "off" cases: with nothing said about
# double-tap (every build before this one), and, once it exists, with it off.
OFF_MAC = ["", "double_tap=False"]
OFF_WIN = ["", "double_tap=False"]

PINNED_MAC = {
    # a hold: press starts, release stops
    "PR": [["press", "release"], [False, False, False]],
    # Space while holding locks hands-free; the release is ignored
    "PSR": [["press", "lock"], [True, True, False]],
    # in hands-free a bare press of the hotkey does nothing on a Mac: only the
    # hotkey and Space together end it
    "PSRP": [["press", "lock"], [True, True, True]],
    "PSRPR": [["press", "lock"], [True, True, False]],
    "PSRPS": [["press", "lock", "release"], [False, False, True]],
    # two quick taps are two separate takes, never a lock
    "PRPR": [["press", "release", "press", "release"], [False, False, False]],
    "PRPRPR": [["press", "release", "press", "release", "press", "release"], [False, False, False]],
    # the key repeating while held is one press
    "PPPR": [["press", "release"], [False, False, False]],
    # a release with nothing held, and a space with nothing held, are nothing
    "R": [[], [False, False, False]],
    "RR": [[], [False, False, False]],
    "S": [[], [False, False, False]],
    # Esc: cancels a hold and a hands-free take, and is nothing otherwise
    "PE": [["press", "cancel"], [False, False, False]],
    "PSE": [["press", "lock", "cancel"], [False, False, False]],
    "PSRE": [["press", "lock", "cancel"], [False, False, False]],
    "E": [[], [False, False, False]],
    "PRE": [["press", "release"], [False, False, False]],
    # the app stopping a take itself puts the listener back
    "PSRX": [["press", "lock"], [False, False, False]],
    "PSRXPR": [["press", "lock", "press", "release"], [False, False, False]],
    "PX": [["press"], [False, False, False]],
    "PXR": [["press"], [False, False, False]],
}


@mac_only
@pytest.mark.parametrize("extra", OFF_MAC)
def test_pinned_mac_listener_tables_with_the_setting_off(extra):
    script = MAC_RIG + f"""
out = {{}}
for steps in {list(PINNED_MAC)!r}:
    out[steps] = run(steps{', ' + extra if extra else ''})
print(json.dumps(out))
"""
    got = isolated(script)
    for steps, expected in PINNED_MAC.items():
        assert got[steps] == expected, steps


PINNED_WIN = {
    "PR": [["press", "release"], "idle"],
    "PS": [["press", "lock"], "sticky"],
    "PSR": [["press", "lock"], "sticky"],
    # Windows: pressing the combination again ends hands-free
    "PSRP": [["press", "lock", "release"], "idle"],
    "PSRPR": [["press", "lock", "release"], "idle"],
    "PSRPRP": [["press", "lock", "release", "press"], "push_to_talk"],
    # two quick taps are two takes
    "PRPR": [["press", "release", "press", "release"], "idle"],
    "PRPRPR": [["press", "release", "press", "release", "press", "release"], "idle"],
    # key repeat is not a press, and Ctrl alone is not the combination
    "Prr": [["press"], "push_to_talk"],
    "PrrR": [["press", "release"], "idle"],
    "cu": [[], "idle"],
    # Esc cancels a hold and a hands-free take, and is nothing otherwise
    "PE": [["press", "cancel"], "idle"],
    "PSE": [["press", "lock", "cancel"], "idle"],
    "PSRE": [["press", "lock", "cancel"], "idle"],
    "E": [[], "idle"],
    "R": [[], "idle"],
    "S": [[], "idle"],
}


@pytest.mark.parametrize("extra", OFF_WIN)
def test_pinned_windows_listener_tables_with_the_setting_off(extra):
    script = WIN_RIG + f"""
out = {{}}
for steps in {list(PINNED_WIN)!r}:
    out[steps] = run(steps{', ' + extra if extra else ''})
print(json.dumps(out))
"""
    got = isolated(script)
    for steps, expected in PINNED_WIN.items():
        assert got[steps] == expected, steps


# The polling fallback (used when the hook cannot be installed) is a third,
# smaller state machine. Each frame is (combo held, space down).
POLL_RIG = WIN_RIG + """
def poll(frames, **kw):
    del log[:]
    l = make(**kw)
    state = {'i': 0}
    def fake_key_down(vk):
        held, space = frames[min(state['i'], len(frames) - 1)]
        if vk == wh.VK_SPACE:
            return space
        if vk == wh.VK_ESCAPE:
            return False
        return held
    wh._key_down = fake_key_down
    def step(_s):
        state['i'] += 1
        if state['i'] >= len(frames):
            l._running = False
    wh.time.sleep = step
    l._running = True
    l._poll_fallback()
    return [list(log), l._state.value]
"""

PINNED_POLL = {
    "hold": [[(True, False)] * 4 + [(False, False)] * 2, [["press", "release"], "idle"]],
    "tap_tap": [[(True, False), (False, False), (True, False), (False, False)],
                [["press", "release", "press", "release"], "idle"]],
    "lock": [[(True, False), (True, True), (False, False), (False, False)], [["press", "lock"], "sticky"]],
    "lock_then_press": [[(True, False), (True, True), (False, False), (True, False), (False, False)],
                        [["press", "lock", "release"], "idle"]],
}


@pytest.mark.parametrize("extra", OFF_WIN)
def test_pinned_windows_polling_fallback_with_the_setting_off(extra):
    script = POLL_RIG + f"""
frames = {PINNED_POLL!r}
out = {{k: poll([tuple(f) for f in v[0]]{', ' + extra if extra else ''}) for k, v in frames.items()}}
print(json.dumps(out))
"""
    got = isolated(script)
    for name, (_frames, expected) in PINNED_POLL.items():
        assert got[name] == expected, name


@mac_only
def test_pinned_mac_a_new_listener_remembers_nothing_of_the_old_one():
    got = isolated(MAC_RIG + """
a = make()
a._on_hotkey_press(); a._on_hotkey_release()
b = make()
print(json.dumps([b._recording, b._sticky, b._hotkey_held]))
""")
    assert got == [False, False, False]


# ═══ what double-tap adds ════════════════════════════════════════════════════

# ── the gate: when are two presses a double-tap? (pure, nothing to start) ────

class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t

    def at(self, t):
        self.t = t
        return self


def tap(gate, clock, down, up):
    """One press of the key, from ``down`` to ``up`` (clock times). Returns
    what the gate said at the release."""
    clock.at(down)
    gate.press()
    clock.at(up)
    return gate.release()


# Times that are exact in binary, so a boundary is a boundary: a tap is up to a
# quarter of a second, and the second must follow within half a second but not
# inside an eighth of a second (a bouncing switch).
def exact_gate(clock, lag=0.0):
    return double_tap.DoubleTapGate(clock, window_s=0.5, tap_max_s=0.25, bounce_s=0.125,
                                    release_lag_s=lag)


def test_the_constants_are_the_ones_the_page_and_the_notes_say():
    assert double_tap.WINDOW_S == 0.35
    assert double_tap.TAP_MAX_S == 0.30
    assert double_tap.BOUNCE_S == 0.04
    assert double_tap.SETTING == "double_tap_handsfree"


def test_a_lone_tap_is_nothing_and_two_quick_taps_are_a_double_tap():
    c = Clock()
    g = exact_gate(c)
    assert tap(g, c, 10.0, 10.0625) is False
    assert tap(g, c, 10.25, 10.3125) is True


def test_the_two_are_used_up_so_a_third_tap_is_the_first_of_the_next_pair():
    c = Clock()
    g = exact_gate(c)
    assert [tap(g, c, 10.0, 10.0625), tap(g, c, 10.25, 10.3125),
            tap(g, c, 10.5, 10.5625), tap(g, c, 10.75, 10.8125)] == [False, True, False, True]


def test_a_tap_is_a_press_of_up_to_the_limit_and_no_longer():
    c = Clock()
    g = exact_gate(c)
    assert tap(g, c, 10.0, 10.25) is False              # first: exactly the limit, a tap
    assert tap(g, c, 10.5, 10.75) is True               # second: exactly the limit, a tap
    g = exact_gate(c)
    tap(g, c, 20.0, 20.25)
    assert tap(g, c, 20.5, 20.8125) is False            # the second is held: push to talk
    g = exact_gate(c)
    assert tap(g, c, 30.0, 30.3125) is False            # the first is held
    assert tap(g, c, 30.5, 30.5625) is False            # so the next is a first tap, not a second


def test_the_second_press_must_follow_within_the_window_but_not_bounce():
    c = Clock()
    for gap, expect in ((0.5, True), (0.5625, False), (0.125, True), (0.0625, False), (0.0, False)):
        g = exact_gate(c)
        tap(g, c, 10.0, 10.0625)
        assert tap(g, c, 10.0625 + gap, 10.0625 + gap + 0.0625) is expect, gap


def test_tap_then_hold_then_tap_is_never_a_double_tap():
    c = Clock()
    g = exact_gate(c)
    assert tap(g, c, 10.0, 10.0625) is False
    assert tap(g, c, 10.25, 11.5) is False              # held for a second and a bit
    assert tap(g, c, 11.625, 11.6875) is False          # the hold cleared the memory


def test_a_hold_before_a_tap_leaves_nothing_behind():
    c = Clock()
    g = exact_gate(c)
    assert tap(g, c, 10.0, 12.0) is False
    assert tap(g, c, 12.125, 12.1875) is False


def test_a_release_with_no_press_is_nothing_and_remembers_nothing():
    c = Clock()
    g = exact_gate(c)
    assert g.release() is False
    assert tap(g, c, 10.0, 10.0625) is False            # still a first tap


def test_a_press_that_never_got_its_release_is_replaced_by_the_next():
    c = Clock()
    g = exact_gate(c)
    c.at(5.0)
    g.press()
    assert tap(g, c, 10.0, 10.0625) is False            # measured from 10.0, not 5.0
    assert tap(g, c, 10.25, 10.3125) is True


def test_a_reset_forgets_the_last_tap():
    c = Clock()
    g = exact_gate(c)
    tap(g, c, 10.0, 10.0625)
    g.reset()
    assert tap(g, c, 10.25, 10.3125) is False


def test_the_press_right_after_a_release_is_bounce_and_later_is_not():
    c = Clock()
    g = exact_gate(c)
    assert g.is_bounce() is False                       # nothing has come up yet
    tap(g, c, 10.0, 10.0625)
    assert g.is_bounce() is True                        # now: the release itself
    c.at(10.1875)
    assert g.is_bounce() is False                       # an eighth of a second on: a press
    c.at(10.15625)
    assert g.is_bounce() is True                        # just inside
    g.reset()
    assert g.is_bounce() is False


def test_the_fn_keys_late_release_is_taken_off_before_a_tap_is_measured():
    """The Mac's Fn key reports its release 0.15 s after the finger leaves
    (hold-quiet). A 0.0625 s tap arrives as 0.2125 s."""
    c = Clock()
    g = exact_gate(c, lag=0.15)
    assert tap(g, c, 10.0, 10.2125) is False            # physical 0.0625: a tap, remembered
    assert tap(g, c, 10.4625, 10.675) is True           # physical gap ~0.25, tap 0.0625
    g = exact_gate(c, lag=0.15)
    assert tap(g, c, 10.0, 10.4) is False               # physical 0.25 = limit: a tap
    g = exact_gate(c, lag=0.15)
    assert tap(g, c, 10.0, 10.5) is False               # physical 0.35: a hold
    assert tap(g, c, 10.75, 10.9625) is False


def test_the_gate_is_safe_to_call_from_many_threads():
    g = double_tap.DoubleTapGate()
    errors = []

    def hammer():
        try:
            for _ in range(500):
                g.press()
                g.release()
                g.is_bounce()
        except Exception as e:      # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_the_default_gate_uses_the_monotonic_clock():
    import time
    g = double_tap.DoubleTapGate()
    g.press()
    time.sleep(0.01)
    assert g.release() is False and g.is_bounce() is True


# ── the setting: off unless exactly true ─────────────────────────────────────

@pytest.mark.parametrize("stored, want", [
    ({"double_tap_handsfree": True}, True), ({"double_tap_handsfree": False}, False),
    ({}, False), (None, False), ([], False), ("x", False),
    ({"double_tap_handsfree": "true"}, False), ({"double_tap_handsfree": 1}, False),
    ({"double_tap_handsfree": None}, False), ({"double_tap_handsfree": [True]}, False),
])
def test_it_is_off_unless_the_settings_say_true(stored, want):
    assert double_tap.enabled(stored) is want


@pytest.mark.parametrize("good", [True, False])
def test_a_switch_is_accepted(good):
    assert double_tap.settings_problem({"double_tap_handsfree": good}) is None
    assert double_tap.touches({"double_tap_handsfree": good}) is True


@pytest.mark.parametrize("bad", ["true", "false", "", 0, 1, None, [True], {"a": 1}])
def test_anything_else_is_refused_with_a_plain_sentence(bad):
    assert double_tap.settings_problem({"double_tap_handsfree": bad}) == user_messages.DOUBLE_TAP_UNKNOWN


def test_a_payload_without_it_is_nothing_to_check():
    assert double_tap.settings_problem({"language": "en"}) is None
    assert double_tap.settings_problem(None) is None
    assert double_tap.touches({"language": "en"}) is False and double_tap.touches("x") is False


def test_the_sentence_has_no_dash_and_no_jargon():
    assert "—" not in user_messages.DOUBLE_TAP_UNKNOWN and "–" not in user_messages.DOUBLE_TAP_UNKNOWN
    assert user_messages.DOUBLE_TAP_UNKNOWN == "Waffler doesn't know that choice for double-tap. Use the switch."


# ── the Mac listener with double-tap on: a decision table with a clock ───────

# Events: P press, R release, S Space, E Esc, X reset_state (the app stopping
# the take itself), T / U the setting switched on / off while running, and
# +s seconds going by. The listener is made with `kw`; the answer is the order
# the callbacks fired in, and [recording, sticky, hotkey held, locked by a
# double-tap, double-tap on].
MAC_CLOCK_RIG = MAC_RIG + """
class Clk:
    def __init__(self):
        self.t = 100.0
    def __call__(self):
        return self.t

def go(spec, **kw):
    clk = Clk()
    del log[:]
    l = make(clock=clk, **kw)
    for tok in spec.split():
        if tok[0] == '+':
            clk.t += float(tok[1:])
        elif tok == 'T':
            l.set_double_tap(True)
        elif tok == 'U':
            l.set_double_tap(False)
        else:
            {'P': l._on_hotkey_press, 'R': l._on_hotkey_release, 'S': l._on_space_press,
             'E': l._on_esc_press, 'X': l.reset_state}[tok]()
    return [list(log), [l._recording, l._sticky, l._hotkey_held, l._locked_by_double_tap,
                        l.double_tap_enabled]]
"""

TT = "P +0.08 R +0.15 P +0.08 R"          # a double tap: 0.08 s taps, 0.15 s apart
LOCKED = [["press", "release", "press", "lock"], [True, True, False, True, True]]
TWO_TAKES = [["press", "release", "press", "release"], [False, False, False, False, True]]

# name -> (events, [log, state]); `custom` is a hotkey that is not Fn (the
# generic handler, release reported when it happens).
MAC_ON = {
    "two quick taps lock hands-free at the second release": (TT, LOCKED),
    "a gap just inside the window": ("P +0.08 R +0.34 P +0.08 R", LOCKED),
    "a gap just outside the window": ("P +0.08 R +0.36 P +0.08 R", TWO_TAKES),
    "taps just short enough": ("P +0.29 R +0.1 P +0.29 R", LOCKED),
    "a first press held too long": ("P +0.31 R +0.1 P +0.08 R", TWO_TAKES),
    "a second press held too long": ("P +0.08 R +0.1 P +0.31 R", TWO_TAKES),
    "tap then hold is push to talk, as it always was": ("P +0.08 R +0.15 P +0.8 R", TWO_TAKES),
    "hold then tap is not a double tap": ("P +1.0 R +0.1 P +0.08 R", TWO_TAKES),
    "tap, a long wait, tap": ("P +0.08 R +2.0 P +0.08 R", TWO_TAKES),
    "a hold is a hold": ("P +1.0 R", [["press", "release"], [False, False, False, False, True]]),
    "a single tap is the tap it always was": ("P +0.08 R", [["press", "release"], [False, False, False, False, True]]),
    "the third tap ends the hands-free take": (
        TT + " +0.2 P +0.08 R",
        [["press", "release", "press", "lock", "release"], [False, False, False, False, True]]),
    "taps four and five start the next one": (
        TT + " +0.2 P +0.08 R +0.2 P +0.08 R +0.15 P +0.08 R",
        [["press", "release", "press", "lock", "release", "press", "release", "press", "lock"],
         [True, True, False, True, True]]),
    "a switch bouncing is not two taps": ("P +0.005 R +0.01 P +0.005 R", TWO_TAKES),
    "the key bouncing after the lock does not end it": (TT + " +0.01 P +0.005 R", LOCKED),
    "a real press after the bounce does": (
        TT + " +0.01 P +0.005 R +0.5 P",
        [["press", "release", "press", "lock", "release"], [False, False, True, False, True]]),
    "key repeat inside a tap changes nothing": ("P +0.05 P +0.05 P +0.05 R +0.15 P +0.05 P +0.03 R", LOCKED),
    "Esc cancels the hands-free take": (
        TT + " E", [["press", "release", "press", "lock", "cancel"], [False, False, False, False, True]]),
    "Esc forgets the taps before it": (
        TT + " E +0.1 P +0.08 R",
        [["press", "release", "press", "lock", "cancel", "press", "release"], [False, False, False, False, True]]),
    "Esc between two taps changes nothing (it is not recording)": (
        "P +0.08 R +0.1 E +0.1 P +0.08 R", LOCKED),
    "the app stopping the take forgets the taps": (
        TT + " X +0.1 P +0.08 R",
        [["press", "release", "press", "lock", "press", "release"], [False, False, False, False, True]]),
    "Space still locks and the lock cue is the same": (
        "P S R", [["press", "lock"], [True, True, False, False, True]]),
    "a press ends a hands-free take that Space locked": (
        "P S R +0.3 P", [["press", "lock", "release"], [False, False, True, False, True]]),
    "and the old way to end it still ends it": (
        "P S R +0.3 P S", [["press", "lock", "release"], [False, False, True, False, True]]),
    "taps before a Space lock are forgotten by it": (
        "P +0.08 R +0.1 P S R +0.3 P", [["press", "release", "press", "lock", "release"],
                                         [False, False, True, False, True]]),
}


@mac_only
def test_mac_listener_double_tap_decision_table():
    script = MAC_CLOCK_RIG + f"""
out = {{}}
for name, spec in {[(n, v[0]) for n, v in MAC_ON.items()]!r}:
    out[name] = go(spec, keys=['cmd', 'shift', 'k'], double_tap=True)
print(json.dumps(out))
"""
    got = isolated(script)
    for name, (_events, expected) in MAC_ON.items():
        assert got[name] == expected, name


# The Fn key: the generic table, but the listener hears the release 0.15 s
# after the finger leaves, so every figure below is that much later.
MAC_FN = {
    "a double tap of Fn": ("P +0.23 R +0.05 P +0.23 R", LOCKED),
    "a tap the Fn key reports late is still a tap": ("P +0.44 R +0.05 P +0.23 R", LOCKED),
    "a tap held 0.31 s is a hold": ("P +0.46 R +0.05 P +0.23 R", TWO_TAKES),
    "a finger gap of 0.34 s": ("P +0.23 R +0.19 P +0.23 R", LOCKED),
    "a finger gap of 0.40 s is too slow": ("P +0.23 R +0.25 P +0.23 R", TWO_TAKES),
    "Fn and Space lock, a press of Fn ends it": (
        "P S R +0.3 P", [["press", "lock", "release"], [False, False, True, False, True]]),
}


@mac_only
def test_mac_fn_listener_measures_taps_as_the_finger_made_them():
    script = MAC_CLOCK_RIG + f"""
out = {{}}
for name, spec in {[(n, v[0]) for n, v in MAC_FN.items()]!r}:
    out[name] = go(spec, double_tap=True)
print(json.dumps(out))
"""
    got = isolated(script)
    for name, (_events, expected) in MAC_FN.items():
        assert got[name] == expected, name


@mac_only
def test_mac_the_setting_can_be_switched_while_the_listener_runs():
    got = isolated(MAC_CLOCK_RIG + f"""
k = dict(keys=['cmd', 'shift', 'k'])
out = {{}}
out['off'] = go({TT!r}, **k)
out['switched_on_first'] = go('T ' + {TT!r}, **k)
out['first_tap_before_it_was_on_is_unseen'] = go('P +0.08 R T +0.1 P +0.08 R', **k)
out['switched_off_between_the_taps'] = go('T P +0.08 R U +0.1 P +0.08 R', **k)
out['switched_off_while_locked_a_press_still_ends_it'] = go('T ' + {TT!r} + ' U +0.5 P', **k)
out['switched_off_then_on_again_is_clean'] = go('T P +0.08 R U T +0.1 P +0.08 R', **k)
out['a_space_lock_while_off_is_not_ended_by_a_press'] = go('P S R U +0.5 P', **k)
print(json.dumps(out))
""")
    assert got["off"] == [["press", "release", "press", "release"], [False, False, False, False, False]]
    assert got["switched_on_first"] == LOCKED
    assert got["first_tap_before_it_was_on_is_unseen"] == [["press", "release", "press", "release"],
                                                           [False, False, False, False, True]]
    assert got["switched_off_between_the_taps"] == [["press", "release", "press", "release"],
                                                    [False, False, False, False, False]]
    assert got["switched_off_while_locked_a_press_still_ends_it"] == [
        ["press", "release", "press", "lock", "release"], [False, False, True, False, False]]
    # Off forgets; on starts clean: the tap before the switch does not count.
    assert got["switched_off_then_on_again_is_clean"] == [["press", "release", "press", "release"],
                                                          [False, False, False, False, True]]
    # Off, a bare press in a Space-locked take does nothing on a Mac, as pinned above.
    assert got["a_space_lock_while_off_is_not_ended_by_a_press"] == [["press", "lock"],
                                                                      [True, True, True, False, False]]


@mac_only
def test_mac_a_listener_made_again_remembers_no_taps():
    """The app makes the listener again when the hotkey or the cancel key
    changes (Api._restart_hotkey_listener): the new one has seen nothing."""
    got = isolated(MAC_CLOCK_RIG + """
clk = Clk()
del log[:]
a = make(keys=['cmd', 'shift', 'k'], double_tap=True, clock=clk)
a._on_hotkey_press(); clk.t += 0.08; a._on_hotkey_release(); clk.t += 0.1
b = make(keys=['cmd', 'shift', 'k'], double_tap=True, clock=clk)
b._on_hotkey_press(); clk.t += 0.08; b._on_hotkey_release()
print(json.dumps([list(log), [b._recording, b._sticky], a.double_tap_enabled, b.double_tap_enabled]))
""")
    assert got == [["press", "release", "press", "release"], [False, False], True, True]


@mac_only
def test_mac_with_double_tap_off_no_line_asks_the_module_anything():
    got = isolated(MAC_CLOCK_RIG + """
import double_tap
calls = []
real = double_tap.DoubleTapGate
class Spy(real):
    def __init__(self, *a, **k):
        calls.append('made'); super().__init__(*a, **k)
double_tap.DoubleTapGate = Spy
smart_hotkey.double_tap.DoubleTapGate = Spy
out = {}
for steps in ['PR', 'PSR', 'PRPR', 'PSRPS', 'PE', 'PSE', 'PX']:
    out[steps] = run(steps, double_tap=False)
out['made'] = list(calls)
l = make()
out['default'] = [l.double_tap_enabled, l._double_tap]
print(json.dumps(out))
""")
    assert got["made"] == [] and got["default"] == [False, None]
    for steps in ("PR", "PSR", "PRPR", "PSRPS", "PE", "PSE", "PX"):
        assert got[steps] == PINNED_MAC[steps], steps


@mac_only
def test_mac_the_pinned_tables_hold_with_double_tap_on_except_a_press_in_a_locked_take():
    """With the clock standing still every press is bounce-close, so nothing
    reads as a double-tap: what the off table says is what the on table says,
    except that a bare press now ends a hands-free take."""
    script = MAC_CLOCK_RIG + f"""
out = {{}}
for steps in {list(PINNED_MAC)!r}:
    out[steps] = run(steps, keys=['cmd', 'shift', 'k'], double_tap=True, clock=Clk())
print(json.dumps(out))
"""
    got = isolated(script)
    differ = {"PSRP", "PSRPR"}
    for steps, expected in PINNED_MAC.items():
        if steps in differ:
            continue
        assert got[steps] == expected, steps
    # The two that differ: the press ends the take.
    assert got["PSRP"] == [["press", "lock", "release"], [False, False, True]]
    assert got["PSRPR"] == [["press", "lock", "release"], [False, False, False]]


@mac_only
def test_mac_the_real_generic_handler_drives_a_double_tap():
    """The same table, one level lower: key events through the hotkey's own
    handler (Cmd+Shift+K), so the press and release callbacks are the ones the
    event tap calls."""
    got = isolated(MAC_CLOCK_RIG + """
class Ev:
    def __init__(self, keycode=0, flags=0):
        self.keycode, self.flags = keycode, flags
m.CGEventGetIntegerValueField = lambda e, f: e.keycode
m.CGEventGetFlags = lambda e: e.flags
CMD, SHIFT, K = 0x100000, 0x20000, 40
clk = Clk()
del log[:]
l = make(keys=['cmd', 'shift', 'k'], double_tap=True, clock=clk)
h = l._hotkey_handler
def down():
    h.handle(m.kCGEventFlagsChanged, Ev(0, CMD | SHIFT)); h.handle(m.kCGEventKeyDown, Ev(K, CMD | SHIFT))
def up():
    h.handle(m.kCGEventKeyUp, Ev(K, CMD | SHIFT)); h.handle(m.kCGEventFlagsChanged, Ev(0, 0))
down(); h.handle(m.kCGEventKeyDown, Ev(K, CMD | SHIFT)); clk.t += 0.08; up()     # a repeat inside the first tap
clk.t += 0.15
down(); clk.t += 0.08; up()
out = [list(log), [l._recording, l._sticky]]
clk.t += 0.4
down(); clk.t += 0.05; up()
out.append(list(log))
print(json.dumps(out))
""")
    assert got[0] == ["press", "release", "press", "lock"] and got[1] == [True, True]
    assert got[2] == ["press", "release", "press", "lock", "release"]


FN_RIG = MAC_CLOCK_RIG + """
class Ev:
    def __init__(self, keycode=0, flags=0):
        self.keycode, self.flags = keycode, flags
m.CGEventGetIntegerValueField = lambda e, f: e.keycode
m.CGEventGetFlags = lambda e: e.flags
FN = 0x800000

class FakeTimer:
    # The Fn handler's hold-quiet timer: it fires when the test says so.
    pending = []
    daemon = True
    def __init__(self, interval, fn, args=()):
        self.interval, self.fn, self.args, self.cancelled = interval, fn, args, False
        FakeTimer.pending.append(self)
    def start(self):
        pass
    def cancel(self):
        self.cancelled = True
m.threading = types.SimpleNamespace(Timer=FakeTimer, Thread=threading.Thread, Lock=threading.Lock,
                                    current_thread=threading.current_thread)

clk = Clk()
m.time = types.SimpleNamespace(monotonic=clk)

def fn_listener(**kw):
    del log[:]
    del FakeTimer.pending[:]
    clk.t = 100.0
    return make(clock=clk, **kw)

def at(t):
    clk.t = 100.0 + t

def fn_down(l):
    l._fn_handler.handle(m.kCGEventFlagsChanged, Ev(0, FN))
def fn_up(l):
    l._fn_handler.handle(m.kCGEventFlagsChanged, Ev(0, 0))
def hold_quiet_ends(t):
    # The 0.15 s timer started at the Fn release runs out.
    at(t)
    for timer in list(FakeTimer.pending):
        if not timer.cancelled:
            timer.fn(*timer.args)
    del FakeTimer.pending[:]
"""


@mac_only
def test_mac_fn_through_its_own_handler_and_hold_quiet_timer():
    """The Fn key end to end, with the handler's real debounce and hold-quiet
    timer (the timer fired by the test, the clock turned by the test): the
    release reaches the listener 0.15 s after the finger leaves."""
    got = isolated(FN_RIG + """
out = {}
l = fn_listener(double_tap=True)
out['lag'] = l._double_tap._lag
fn_down(l)                       # t = 0: the finger goes down
at(0.08); fn_up(l)               # the finger leaves; the release is held back
hold_quiet_ends(0.23)            # ... and reaches the listener 0.15 s later
at(0.28); fn_down(l)             # the finger comes back after 0.2 s
at(0.36); fn_up(l)
hold_quiet_ends(0.51)
out['double'] = [list(log), [l._recording, l._sticky]]

# A gap under 0.15 s is the hold-quiet timer's chatter, not two taps: one hold, as before.
l = fn_listener(double_tap=True)
fn_down(l)
at(0.08); fn_up(l)
at(0.14); fn_down(l)             # back before the timer ran out: still the same hold
at(0.22); fn_up(l)
hold_quiet_ends(0.37)
out['merged'] = [list(log), [l._recording, l._sticky]]

# And a double tap that is too slow is two takes.
l = fn_listener(double_tap=True)
fn_down(l)
at(0.08); fn_up(l); hold_quiet_ends(0.23)
at(0.55); fn_down(l)
at(0.63); fn_up(l); hold_quiet_ends(0.78)
out['slow'] = [list(log), [l._recording, l._sticky]]

# Off, the same finger movements are two takes, exactly as before.
l = fn_listener()
fn_down(l)
at(0.08); fn_up(l); hold_quiet_ends(0.23)
at(0.28); fn_down(l)
at(0.36); fn_up(l); hold_quiet_ends(0.51)
out['off'] = [list(log), [l._recording, l._sticky]]
print(json.dumps(out))
""")
    assert got["lag"] == 0.15
    assert got["double"] == [["press", "release", "press", "lock"], [True, True]]
    assert got["merged"] == [["press", "release"], [False, False]]
    assert got["slow"] == [["press", "release", "press", "release"], [False, False]]
    assert got["off"] == [["press", "release", "press", "release"], [False, False]]


# ── the Windows listener with double-tap on ──────────────────────────────────

# Events: P Win and Ctrl down, R both up, S Space, E Esc, r Ctrl's key repeating,
# T / U the setting switched on / off, +s seconds going by. The answer is the
# order the callbacks fired in and the listener's state.
WIN_CLOCK_RIG = WIN_RIG + """
class Clk:
    def __init__(self):
        self.t = 100.0
    def __call__(self):
        return self.t

def go(spec, **kw):
    clk = Clk()
    del log[:]
    l = make(clock=clk, **kw)
    for tok in spec.split():
        if tok[0] == '+':
            clk.t += float(tok[1:])
        elif tok == 'T':
            l.set_double_tap(True)
        elif tok == 'U':
            l.set_double_tap(False)
        elif tok == 'P': key(l, WIN); key(l, CTRL)
        elif tok == 'R': key(l, CTRL, False); key(l, WIN, False)
        elif tok == 'S': key(l, SPACE)
        elif tok == 'E': key(l, ESC)
        elif tok == 'r': key(l, CTRL)
    return [list(log), l._state.value, l.double_tap_enabled]
"""

W_LOCKED = [["press", "release", "press", "lock"], "sticky", True]
W_TWO = [["press", "release", "press", "release"], "idle", True]

WIN_ON = {
    "two quick taps lock hands-free at the second release": (TT, W_LOCKED),
    "a gap just inside the window": ("P +0.08 R +0.34 P +0.08 R", W_LOCKED),
    "a gap just outside the window": ("P +0.08 R +0.36 P +0.08 R", W_TWO),
    "taps just short enough": ("P +0.29 R +0.1 P +0.29 R", W_LOCKED),
    "a first press held too long": ("P +0.31 R +0.1 P +0.08 R", W_TWO),
    "a second press held too long": ("P +0.08 R +0.1 P +0.31 R", W_TWO),
    "tap then hold is push to talk, as it always was": ("P +0.08 R +0.15 P +0.8 R", W_TWO),
    "hold then tap is not a double tap": ("P +1.0 R +0.1 P +0.08 R", W_TWO),
    "tap, a long wait, tap": ("P +0.08 R +2.0 P +0.08 R", W_TWO),
    "a hold is a hold": ("P +1.0 R", [["press", "release"], "idle", True]),
    "a single tap is the tap it always was": ("P +0.08 R", [["press", "release"], "idle", True]),
    "the third tap ends the hands-free take": (
        TT + " +0.2 P +0.08 R", [["press", "release", "press", "lock", "release"], "idle", True]),
    "taps four and five start the next one": (
        TT + " +0.2 P +0.08 R +0.2 P +0.08 R +0.15 P +0.08 R",
        [["press", "release", "press", "lock", "release", "press", "release", "press", "lock"],
         "sticky", True]),
    "a switch bouncing is not two taps": ("P +0.005 R +0.01 P +0.005 R", W_TWO),
    "the keys bouncing after the lock do not end it": (TT + " +0.01 P +0.005 R", W_LOCKED),
    "a real press after the bounce does": (
        TT + " +0.01 P +0.005 R +0.5 P", [["press", "release", "press", "lock", "release"], "idle", True]),
    "key repeat inside a tap changes nothing": ("P +0.05 r +0.05 r +0.05 R +0.15 P +0.05 r +0.03 R", W_LOCKED),
    "Esc cancels the hands-free take": (
        TT + " E", [["press", "release", "press", "lock", "cancel"], "idle", True]),
    "Esc forgets the taps before it": (
        TT + " E +0.1 P +0.08 R",
        [["press", "release", "press", "lock", "cancel", "press", "release"], "idle", True]),
    "Esc between two taps changes nothing (it is not recording)": (
        "P +0.08 R +0.1 E +0.1 P +0.08 R", W_LOCKED),
    "Space still locks, and taps before it are forgotten": (
        "P +0.08 R +0.1 P S R +0.3 P", [["press", "release", "press", "lock", "release"], "idle", True]),
    "a press ends a hands-free take that Space locked, as it always did": (
        "P S R +0.3 P", [["press", "lock", "release"], "idle", True]),
}


@pytest.mark.parametrize("name", list(WIN_ON))
def test_windows_listener_double_tap_decision_table(name):
    events, expected = WIN_ON[name]
    got = isolated(WIN_CLOCK_RIG + f"print(json.dumps(go({events!r}, double_tap=True)))")
    assert got == expected, name


def test_windows_the_setting_can_be_switched_while_the_listener_runs():
    got = isolated(WIN_CLOCK_RIG + f"""
out = {{}}
out['off'] = go({TT!r})
out['switched_on_first'] = go('T ' + {TT!r})
out['first_tap_before_it_was_on_is_unseen'] = go('P +0.08 R T +0.1 P +0.08 R')
out['switched_off_between_the_taps'] = go('T P +0.08 R U +0.1 P +0.08 R')
out['switched_off_while_locked_a_press_still_ends_it'] = go('T ' + {TT!r} + ' U +0.5 P')
print(json.dumps(out))
""")
    assert got["off"] == [["press", "release", "press", "release"], "idle", False]
    assert got["switched_on_first"] == W_LOCKED
    assert got["first_tap_before_it_was_on_is_unseen"] == W_TWO
    assert got["switched_off_between_the_taps"] == [["press", "release", "press", "release"], "idle", False]
    assert got["switched_off_while_locked_a_press_still_ends_it"] == [
        ["press", "release", "press", "lock", "release"], "idle", False]


def test_windows_a_listener_made_again_remembers_no_taps():
    got = isolated(WIN_CLOCK_RIG + """
clk = Clk()
del log[:]
a = make(double_tap=True, clock=clk)
key(a, WIN); key(a, CTRL); clk.t += 0.08; key(a, CTRL, False); key(a, WIN, False); clk.t += 0.1
b = make(double_tap=True, clock=clk)
key(b, WIN); key(b, CTRL); clk.t += 0.08; key(b, CTRL, False); key(b, WIN, False)
print(json.dumps([list(log), b._state.value]))
""")
    assert got == [["press", "release", "press", "release"], "idle"]


def test_windows_with_double_tap_off_no_line_asks_the_module_anything():
    got = isolated(WIN_CLOCK_RIG + """
import double_tap
made = []
real = double_tap.DoubleTapGate
class Spy(real):
    def __init__(self, *a, **k):
        made.append(1); super().__init__(*a, **k)
wh.double_tap.DoubleTapGate = Spy
out = {}
for steps in ['PR', 'PS', 'PSRP', 'PRPR', 'PE', 'PSE']:
    out[steps] = run(steps, double_tap=False)
out['made'] = len(made)
l = make()
out['default'] = [l.double_tap_enabled, l._double_tap]
print(json.dumps(out))
""")
    assert got["made"] == 0 and got["default"] == [False, None]
    for steps in ("PR", "PS", "PSRP", "PRPR", "PE", "PSE"):
        assert got[steps] == PINNED_WIN[steps], steps


def test_windows_the_pinned_tables_hold_with_double_tap_on():
    """With the clock standing still every press is bounce-close, so nothing
    reads as a double-tap: the off table is the on table. (Windows already
    ended a hands-free take with a press of the keys.)"""
    got = isolated(WIN_CLOCK_RIG + f"""
out = {{}}
for steps in {list(PINNED_WIN)!r}:
    out[steps] = run(steps, double_tap=True, clock=Clk())
print(json.dumps(out))
""")
    for steps, expected in PINNED_WIN.items():
        assert got[steps] == expected, steps


# The polling fallback: frames of (combo held, space down), 30 ms apart.
POLL_CLOCK_RIG = POLL_RIG + """
def poll_clock(frames, **kw):
    del log[:]
    clk = types.SimpleNamespace(t=100.0)
    l = make(clock=lambda: clk.t, **kw)
    state = {'i': 0}
    def fake_key_down(vk):
        held, space = frames[min(state['i'], len(frames) - 1)]
        if vk == wh.VK_SPACE:
            return space
        if vk == wh.VK_ESCAPE:
            return False
        return held
    wh._key_down = fake_key_down
    def step(_s):
        state['i'] += 1
        clk.t += 0.03
        if state['i'] >= len(frames):
            l._running = False
    wh.time.sleep = step
    l._running = True
    l._poll_fallback()
    return [list(log), l._state.value]

def held(n): return [(True, False)] * n
def free(n): return [(False, False)] * n
"""


def test_windows_polling_fallback_locks_on_a_double_tap_too():
    got = isolated(POLL_CLOCK_RIG + """
out = {}
tap_tap = held(3) + free(5) + held(3) + free(2)
out['off'] = poll_clock(tap_tap)
out['on'] = poll_clock(tap_tap, double_tap=True)
out['slow'] = poll_clock(held(3) + free(14) + held(3) + free(2), double_tap=True)
out['tap_hold'] = poll_clock(held(3) + free(5) + held(20) + free(2), double_tap=True)
out['third_tap_ends'] = poll_clock(tap_tap + free(5) + held(3) + free(2), double_tap=True)
out['hold'] = poll_clock(held(30) + free(2), double_tap=True)
print(json.dumps(out))
""")
    assert got["off"] == [["press", "release", "press", "release"], "idle"]
    assert got["on"] == [["press", "release", "press", "lock"], "sticky"]
    assert got["slow"] == [["press", "release", "press", "release"], "idle"]
    assert got["tap_hold"] == [["press", "release", "press", "release"], "idle"]
    assert got["third_tap_ends"] == [["press", "release", "press", "lock", "release"], "idle"]
    assert got["hold"] == [["press", "release"], "idle"]


# ── the listener and the real pipeline together (a Mac listener, no tap) ─────

@mac_only
def test_a_double_tap_end_to_end_through_the_real_pipeline(tmp_path, monkeypatch):
    """Tap (nothing said), tap again (hands-free locks), speak, tap once: the
    first tap is dropped as a brush of the key, the take of the second tap
    keeps recording and its words are written and pasted when the third tap
    ends it. The listener's callbacks are the pipeline's own methods; the
    event tap is never made and the audio is a fake."""
    import time
    import hold_hint
    import smart_hotkey
    import text_context
    from _pipeline_harness import make_pipeline, speech_wav, silence_wav, wait_for, history

    monkeypatch.setattr(text_context, "frontmost_app",
                        lambda read_title=None: text_context.make_context("", "Notes"))
    p = make_pipeline(tmp_path)

    class SequencedAudio:
        """What the recorder hands back at each stop: a brush of the key, then speech."""
        def __init__(self):
            self.wavs = [silence_wav(0.3), speech_wav(3.0)]
            self.starts = self.stops = 0
            self.deafened = []

        def deafen_for(self, seconds):
            self.deafened.append(seconds)

        def start(self):
            self.starts += 1

        def get_level(self):
            return 0.3

        def stop(self):
            self.stops += 1
            return self.wavs.pop(0)

        def force_rebuild(self):
            pass

    p.audio = SequencedAudio()
    clk = Clock()
    listener = smart_hotkey.SmartHotkeyListener(
        on_press=p.on_hotkey_press, on_release=p.on_hotkey_release, on_cancel=p._on_hotkey_cancel,
        on_lock=p._on_hands_free_lock, keys=["cmd", "shift", "k"], double_tap=True, clock=clk)
    p.hotkey_listener = listener

    # First tap: a brush of the key. It starts a take and stops it, as it always did.
    listener._on_hotkey_press()
    assert wait_for(lambda: p.is_recording)
    clk.t += 0.08
    listener._on_hotkey_release()
    assert wait_for(lambda: p.audio.stops == 1 and not p.is_recording)
    assert wait_for(lambda: not p._watchdog.active())
    assert history(p) == [], "a brush of the key writes nothing"

    # Second tap, 0.15 s after: a take starts and, at its release, locks on.
    clk.t += 0.15
    listener._on_hotkey_press()
    assert wait_for(lambda: p.is_recording)
    pressed_id = p._current_press_id
    clk.t += 0.08
    listener._on_hotkey_release()
    assert wait_for(lambda: p._hands_free_press_id == pressed_id), "the lock came through on_lock"
    time.sleep(0.2)
    assert p.is_recording and p.audio.stops == 1, "still recording: nothing stopped it"
    assert listener._sticky and listener._recording

    # The person speaks, then taps once: the take ends and is processed.
    clk.t += 5.0
    listener._on_hotkey_press()
    assert wait_for(lambda: len(history(p)) == 1, timeout=10)
    assert not p.is_recording and p.audio.stops == 2
    entry = history(p)[0]
    assert entry["text"] == "ok so ship it on monday"
    assert p.clipboard.pastes, "and it was pasted"
    clk.t += 0.08
    listener._on_hotkey_release()                                   # the release of that press: nothing
    assert not listener._recording and not listener._sticky
    # The first tap would have said "Don't tap, hold" after a pause to see whether a second
    # press followed (src/hold_hint.py SETTLE_S): one did, so it says nothing.
    time.sleep(hold_hint.SETTLE_S + 0.3)
    assert not p.overlay.toasts(), "no \"Don't tap, hold\" hint, no error"


@mac_only
@pytest.mark.parametrize("on", [False, True])
def test_a_single_tap_is_the_tap_it_always_was_with_double_tap_on_or_off(tmp_path, monkeypatch, on):
    """One brush of the key: dropped, and "Don't tap, hold" comes once the
    pause for a second press has passed. Nothing waited for a second tap."""
    import time
    import hold_hint
    import smart_hotkey
    import text_context
    from _pipeline_harness import make_pipeline, silence_wav, wait_for, history, FakeAudio

    monkeypatch.setattr(text_context, "frontmost_app",
                        lambda read_title=None: text_context.make_context("", "Notes"))
    p = make_pipeline(tmp_path, wav=silence_wav(0.3))
    p.audio = FakeAudio(silence_wav(0.3))
    clk = Clock()
    listener = smart_hotkey.SmartHotkeyListener(
        on_press=p.on_hotkey_press, on_release=p.on_hotkey_release, on_cancel=p._on_hotkey_cancel,
        on_lock=p._on_hands_free_lock, keys=["cmd", "shift", "k"], double_tap=on, clock=clk)
    p.hotkey_listener = listener
    started = time.monotonic()
    listener._on_hotkey_press()
    assert wait_for(lambda: p.is_recording)
    clk.t += 0.08
    listener._on_hotkey_release()
    # The stop is not held back for a second tap: the recorder is stopped at once.
    assert wait_for(lambda: p.audio.stops == 1 and not p.is_recording, timeout=2)
    assert time.monotonic() - started < 0.5
    assert history(p) == []
    assert wait_for(lambda: len(p.overlay.toasts()) == 1, timeout=hold_hint.SETTLE_S + 3)
    assert p.overlay.toasts()[0]["heading"] == "Don't tap, hold"
