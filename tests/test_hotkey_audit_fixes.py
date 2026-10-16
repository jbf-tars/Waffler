"""Keyboard fixes from the security and correctness audit (M4, L4, L5).

* M4: a hold's "up" could be queued before its "down" (the hold was booked
  under the lock but the down was posted after the lock was let go), so Command
  mode recorded for a minute and then acted on it.
* L4: a shortcut key written with a Unicode digit raised from ``int()``.
* L5: after a lost key-up on Windows the next plain press of that letter was
  swallowed; a settings re-apply cleared the swallow memory mid-hold; the tap
  or hook callback started a thread and waited for it; the double-tap setting
  was read twice with a window between; the Windows watcher reset the
  prototypes of the process-wide user32.

Fake clocks and events, no keyboard.
"""
import ast
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_hotkeys as ah  # noqa: E402

MAC, WIN = ah.MAC, ah.WINDOWS
WAIT = 5.0


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


# ── M4: down is queued before up ─────────────────────────────────────────────

def gated(registry, name):
    """Make the first post on the thread called ``name`` wait at the point where
    the old code had already let go of the registry's lock."""
    entered, release = threading.Event(), threading.Event()
    real = registry._serial_for

    def serial_for(action_id):
        if threading.current_thread().name == name and not release.is_set():
            entered.set()
            release.wait(WAIT)
        return real(action_id)
    registry._serial_for = serial_for
    return entered, release


def drain_inline(fn):
    fn()


def run_in(name, fn):
    t = threading.Thread(target=fn, name=name, daemon=True)
    t.start()
    return t


def test_a_key_up_that_comes_while_the_down_is_being_posted_still_runs_after_it():
    order = []
    r = ah.ActionHotkeys(MAC, spawn=drain_inline)
    assert r.register_hold("command", ["control", "option", "c"], lambda: order.append("down"),
                           lambda: order.append("up"))["ok"]
    entered, release = gated(r, "tap")
    tap = run_in("tap", lambda: r.key_down("c", ["control", "option"]))
    assert entered.wait(WAIT), "key_down never reached the point of posting its down"
    settings = run_in("other", lambda: r.key_up("c"))       # the key comes up on another thread, now
    time.sleep(0.15)
    release.set()
    tap.join(WAIT)
    settings.join(WAIT)
    assert order == ["down", "up"], order


def test_a_modifier_hold_released_as_its_timer_fires_runs_down_then_up():
    order, timers = [], []
    r = ah.ActionHotkeys(MAC, spawn=drain_inline, timer=lambda d, fn: timers.append(fn))
    assert r.register_hold("command", ["control", "option"], lambda: order.append("down"),
                           lambda: order.append("up"))["ok"]
    r.modifiers_changed(["control", "option"])
    assert len(timers) == 1
    entered, release = gated(r, "timer")
    fire = run_in("timer", timers[0])
    assert entered.wait(WAIT)
    letting_go = run_in("tap", lambda: r.modifiers_changed([]))
    time.sleep(0.15)
    release.set()
    fire.join(WAIT)
    letting_go.join(WAIT)
    assert order == ["down", "up"], order


def test_replacing_or_removing_a_hold_still_ends_it_after_it_began():
    order = []
    r = ah.ActionHotkeys(MAC, spawn=drain_inline)
    r.register_hold("command", ["control", "option", "c"], lambda: order.append("down"),
                    lambda: order.append("up"))
    r.key_down("c", ["control", "option"])
    r.unregister("command")
    assert order == ["down", "up"]
    r.key_up("c")
    r.register_hold("command", ["control", "option", "c"], lambda: order.append("down2"),
                    lambda: order.append("up2"))
    r.key_down("c", ["control", "option"], repeat=False)
    r.register_hold("command", ["control", "option", "d"], lambda: order.append("down3"),
                    lambda: order.append("up3"))
    assert order[-2:] == ["down2", "up2"]


def test_a_burst_of_downs_and_ups_keeps_every_pair_in_order():
    order = []
    clock = Clock()
    r = ah.ActionHotkeys(MAC, clock=clock)           # real threads, as in the app
    r.register_hold("command", ["control", "option", "c"], lambda: order.append("d"), lambda: order.append("u"))
    for _ in range(40):
        r.key_down("c", ["control", "option"])
        r.key_up("c")
        clock.advance(1.0)
    deadline = time.time() + WAIT
    while len(order) < 80 and time.time() < deadline:
        time.sleep(0.01)
    assert order == ["d", "u"] * 40


# ── L4: a key name with a Unicode digit ──────────────────────────────────────

@pytest.mark.parametrize("name", ["f²", "f٣", "f١٢", "f0001", "f025", "f0", "f25", "f-1", "f_1"])
def test_a_key_that_is_not_an_f_key_is_refused_in_a_sentence_and_never_raises(name):
    for platform, mods in ((MAC, ["control", "option"]), (WIN, ["ctrl", "shift"])):
        verdict = ah.check(mods + [name], platform)
        assert verdict["ok"] is False and isinstance(verdict["error"], str) and verdict["error"]
    assert ah.check([name], MAC)["ok"] is False


@pytest.mark.parametrize("name", ["f1", "f9", "f12", "f13", "f20", "f24"])
def test_the_real_f_keys_are_still_keys(name):
    assert ah._is_key(name) is True


def test_a_hand_edited_settings_file_with_such_a_key_does_not_stop_the_others():
    stored = {"transforms": {"hotkey": ["control", "option", "f²"]},
              "paste_last_hotkey": ["control", "option", "v"]}
    got = ah.saved_shortcuts(stored)
    assert got.get("paste_last") == ["control", "option", "v"]
    for a in ah.ACTIONS:
        ah.stored_keys(stored, a.id)


# ── L5: the swallow memory ───────────────────────────────────────────────────

def win_registry():
    clock = Clock()
    r = ah.ActionHotkeys(WIN, clock=clock, spawn=drain_inline)
    assert r.register("transform", ["ctrl", "shift", "k"], lambda: None)["ok"]
    return r, clock


K = ah.WINDOWS_VKS["k"]
CTRL_SHIFT = {0x11, 0x10}


def key_event(r, down=True, held=CTRL_SHIFT):
    return ah.windows_event(r, K, ah._WM_KEYDOWN if down else ah._WM_KEYUP, 0, lambda vk: vk in held)


def test_windows_still_swallows_the_autorepeat_of_a_held_shortcut():
    r, clock = win_registry()
    assert key_event(r) is True
    for _ in range(40):
        clock.advance(0.03)
        assert key_event(r) is True, "a repeat is part of the same press"
    assert key_event(r, down=False) is True


def test_windows_swallows_a_slow_first_repeat_too():
    r, clock = win_registry()
    key_event(r)
    clock.advance(0.9)                      # the longest delay before the first repeat
    assert key_event(r) is True


def test_windows_a_plain_press_long_after_a_lost_key_up_types_the_letter():
    """The key-up of Ctrl+Shift+K was lost. The person later types a plain k:
    that is a new press, not a repeat, and must reach the app."""
    r, clock = win_registry()
    assert key_event(r) is True             # ...and its key-up never arrives
    clock.advance(1.6)
    assert key_event(r, held=set()) is False, "a plain k, 1.6 s later, was swallowed as a repeat"
    assert key_event(r, down=False, held=set()) is False


def test_windows_a_lost_key_up_does_not_swallow_the_next_plain_press_within_the_old_2_5_seconds():
    r, clock = win_registry()
    key_event(r)
    clock.advance(1.2)
    assert key_event(r, held=set()) is False


def test_windows_the_same_combination_pressed_again_after_a_lost_key_up_is_still_ours():
    r, clock = win_registry()
    assert key_event(r) is True
    clock.advance(1.8)
    assert key_event(r) is True, "the shortcut's own modifiers are held: its key stays swallowed"


def test_a_settings_reapply_does_not_forget_a_key_that_is_being_held():
    """attach_listener runs each time a setting is applied, with the listener
    that is already running; the held key's repeats must still be kept from the
    app, and its key-up too."""
    r = ah.ActionHotkeys(MAC, clock=Clock(), spawn=drain_inline)
    r.register("transform", ["control", "option", "k"], lambda: None)

    class Listener:
        def attach_observer(self, handler):
            pass
    listener = Listener()
    r.attach_listener(listener)
    assert r.swallow_down("k", ["control", "option"]) is True
    r.attach_listener(listener)                 # a setting was applied again
    r.attach_listener(listener)
    assert r.swallow_down("k", ["control", "option"], repeat=True) is True
    assert r.swallow_up("k") is True


def test_a_new_listener_is_a_new_tap_and_starts_with_no_memory():
    r = ah.ActionHotkeys(MAC, clock=Clock(), spawn=drain_inline)
    r.register("transform", ["control", "option", "k"], lambda: None)

    class Listener:
        def attach_observer(self, handler):
            pass
    r.attach_listener(Listener())
    r.swallow_down("k", ["control", "option"])
    r.attach_listener(Listener())
    assert r.is_swallowing("k") is False


# ── L5: callbacks never start a thread on the keyboard's thread ──────────────

def test_the_keyboards_thread_hands_work_to_a_long_lived_worker():
    r = ah.ActionHotkeys(MAC)
    seen = []
    done = threading.Event()
    r.register("transform", ["control", "option", "k"], lambda: (seen.append(threading.current_thread()), done.set()))
    starts = []
    real = threading.Thread.start

    def counting_start(self):
        starts.append(threading.current_thread())
        return real(self)
    try:
        threading.Thread.start = counting_start
        r.key_down("k", ["control", "option"])
        keyboard_thread = threading.current_thread()
        assert done.wait(WAIT)
    finally:
        threading.Thread.start = real
    assert keyboard_thread not in starts, "a thread was started on the thread that heard the key"
    assert seen[0] is not keyboard_thread


def test_a_worker_that_is_busy_does_not_hold_up_another_action():
    r = ah.ActionHotkeys(MAC, clock=Clock())
    blocked, released = threading.Event(), threading.Event()
    r.register("paste_last", ["control", "option", "v"], lambda: (blocked.set(), released.wait(WAIT)))
    other = threading.Event()
    r.register("copy_last", ["control", "option", "c"], other.set)
    r.key_down("v", ["control", "option"])
    assert blocked.wait(WAIT)
    r.key_down("c", ["control", "option"])
    assert other.wait(WAIT), "one slow action held up the next"
    released.set()


# ── L5: the Windows watcher's own user32 ─────────────────────────────────────

def test_the_windows_watcher_does_not_touch_the_shared_user32():
    src = (ROOT / "src" / "action_hotkeys.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    run = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run"
               and any(isinstance(c, ast.Name) and c.id == "KBDLLHOOKSTRUCT" or
                       (isinstance(c, ast.ClassDef) and c.name == "KBDLLHOOKSTRUCT") for c in ast.walk(n)))
    body = ast.unparse(run)
    assert "ctypes.windll" not in body, "the watcher sets prototypes on the process-wide user32"
    assert "WinDLL" in body


# ── L5: the double-tap setting is read once ─────────────────────────────────

sys.path.insert(0, str(ROOT / "tests"))
from test_double_tap import MAC_RIG, WIN_RIG, isolated, mac_only  # noqa: E402

GATE = """
class Gate:
    def press(self): pass
    def release(self): return False
    def is_bounce(self): return False
    def reset(self): pass

def flapping(base):
    # base, with a double-tap setting that is switched off between any two reads of it
    class Racy(base):
        reads = 0
        @property
        def _double_tap(self):
            Racy.reads += 1
            return Gate() if Racy.reads % 2 == 1 else None
        @_double_tap.setter
        def _double_tap(self, value):
            pass
    return Racy
"""


@mac_only
def test_a_mac_listener_reads_the_double_tap_setting_once_per_decision():
    errors = isolated(MAC_RIG + GATE + """
Racy = flapping(smart_hotkey.SmartHotkeyListener)
errors = []
for steps in ('PR', 'PSRP', 'PRPR', 'PSR', 'PXPR', 'PR' * 6):
    for flip in range(2):
        l = Racy(on_press=lambda: None, on_release=lambda: None, on_cancel=lambda: None, on_lock=lambda: None)
        Racy.reads = flip
        try:
            for s in steps:
                {'P': l._on_hotkey_press, 'R': l._on_hotkey_release, 'S': l._on_space_press,
                 'X': l.reset_state}[s]()
            l.reset_state()
        except Exception as e:
            errors.append([steps, type(e).__name__, str(e)[:60]])
print(json.dumps(errors))
""")
    assert errors == []


def test_a_windows_listener_reads_the_double_tap_setting_once_per_decision():
    errors = isolated(WIN_RIG + GATE + """
Racy = flapping(wh.WindowsHotkeyListener)
errors = []
for steps in ('PR', 'PSRP', 'PRPR', 'PSR', 'PR' * 6, 'PRPRP'):
    for flip in range(2):
        l = Racy(on_press=lambda: None, on_release=lambda: None, on_cancel=lambda: None, on_lock=lambda: None)
        l._fire_press = l._fire_release = l._fire_cancel = l._fire_lock = lambda: None
        Racy.reads = flip
        try:
            for s in steps:
                if s == 'P': key(l, WIN); key(l, CTRL)
                elif s == 'R': key(l, CTRL, False); key(l, WIN, False)
                elif s == 'S': key(l, SPACE)
        except Exception as e:
            errors.append([steps, type(e).__name__, str(e)[:60]])
print(json.dumps(errors))
""")
    assert errors == []


# ── Windows: the app can put the listener back after it ends a take itself ───

def test_windows_reset_state_puts_the_listener_back_to_idle_like_the_macs():
    """After the pill's stop button or the 20-minute stop the app ends the take
    itself. The listener still believed one was running, so the next press of the
    combination was taken for 'end hands-free' and absorbed."""
    got = isolated(WIN_RIG + """
out = {}
# A hands-free take (Space while holding), ended by the pill's stop button.
l = make()
for s in 'PSR':
    if s == 'P': key(l, WIN); key(l, CTRL)
    elif s == 'S': key(l, SPACE)
    elif s == 'R': key(l, CTRL, False); key(l, WIN, False)
before = [list(log), l._state.value]
l.reset_state()
del log[:]
key(l, WIN); key(l, CTRL)                   # the next press of the combination
out['sticky_then_stopped'] = [before, [list(log), l._state.value]]
# A push-to-talk take ended by the app (the keys are still down in the cache).
l = make()
del log[:]
key(l, WIN); key(l, CTRL)
l.reset_state()
state_after = [l._state.value, dict(l._key_states)]
key(l, CTRL, False); key(l, WIN, False)
del log[:]
key(l, WIN); key(l, CTRL)
out['held_then_stopped'] = [state_after, list(log)]
# Without it (what the listener did before): the same press is absorbed.
l = make()
for s in 'PSR':
    if s == 'P': key(l, WIN); key(l, CTRL)
    elif s == 'S': key(l, SPACE)
    elif s == 'R': key(l, CTRL, False); key(l, WIN, False)
del log[:]
key(l, WIN); key(l, CTRL)
out['no_reset'] = list(log)
# reset_state on an idle listener is nothing, and survives double-tap being on.
l = make(double_tap=True)
l.reset_state()
out['idle'] = [l._state.value, l.double_tap_enabled]
print(json.dumps(out))
""")
    assert got["sticky_then_stopped"] == [[["press", "lock"], "sticky"], [["press"], "push_to_talk"]]
    assert got["held_then_stopped"] == [["idle", {"win": False, "ctrl": False}], ["press", "press"]] \
        or got["held_then_stopped"][0] == ["idle", {"win": False, "ctrl": False}]
    assert got["no_reset"] == ["release"], "this is the absorbed press the reset exists to prevent"
    assert got["idle"] == ["idle", True]


def test_windows_a_listener_stopped_before_it_began_never_installs_its_hook():
    got = isolated(WIN_RIG + """
wh.user32.GetMessageW.return_value = 0          # were it to start, its loop would end at once
l = make()
l.stop()
wh.user32.SetWindowsHookExW.reset_mock()
l.start()
print(json.dumps({'installed': wh.user32.SetWindowsHookExW.called, 'running': l._running}))
""")
    assert got == {"installed": False, "running": False}


def test_windows_a_listener_that_is_started_still_installs_its_hook():
    got = isolated(WIN_RIG + """
wh.user32.GetMessageW.return_value = 0          # the loop ends at once
l = make()
l.start()
print(json.dumps({'installed': wh.user32.SetWindowsHookExW.called, 'removed': wh.user32.UnhookWindowsHookEx.called}))
""")
    assert got == {"installed": True, "removed": True}


@mac_only
def test_a_mac_tap_stopped_while_it_is_being_made_does_not_go_on_to_run():
    got = isolated("""
import json
import mac_hotkey_monitor as m
calls = []
tap = m.MacEventTap()
fake = object()

def create(*a, **k):
    tap.stop()                  # a newer change stops this listener as its tap is being made
    return fake
m.CGEventTapCreate = create
m.CFMachPortCreateRunLoopSource = lambda *a: object()
m.CFRunLoopGetCurrent = lambda: object()
m.CFRunLoopAddSource = lambda *a: calls.append('add')
m.CGEventTapEnable = lambda t, on: calls.append(('enable', t is fake, on))
m.CFRunLoopRun = lambda: calls.append('RAN')
m.CFRunLoopStop = lambda r: calls.append('stop')
tap._stopped = False
tap._run()
print(json.dumps({'ran': 'RAN' in calls, 'disabled': ('enable', True, False) in calls}))
""")
    assert got == {"ran": False, "disabled": True}


# ── L11: nothing suggests VoiceOver's own keys ───────────────────────────────

def test_the_modifier_only_examples_do_not_offer_the_voiceover_keys():
    import shortcut_settings as ss
    bad = ah.check(["shift"], MAC, hold=True)["error"]
    assert "Control and Command" in bad and "Control and Option" not in bad
    lines = ss.rules(MAC)["lines"]
    modifier_line = next(l for l in lines if "modifier keys held together" in l)
    assert "such as Control and Command" in modifier_line
    assert "VoiceOver" in modifier_line and "Control and Option are the keys VoiceOver uses" in modifier_line
    logic = (ROOT / "ui" / "logic.js").read_text(encoding="utf-8")
    assert "such as Control and Option" not in logic
    for line in lines + [bad]:
        assert "—" not in line and "–" not in line
