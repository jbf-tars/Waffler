"""Action shortcuts (S7): the combination checker, the registry that decides
which action a key press fires, and the thin listeners that feed it.

The decisions are pure and run here with fakes. The Quartz and Win32 pieces
are thin; the Mac reader is also run once against a real in-memory key event
(nothing is posted to the system), and skips where there is no Quartz.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_hotkeys as ah  # noqa: E402

MAC, WIN = ah.MAC, ah.WINDOWS
mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS (Quartz)")


def isolated(code):
    """Run ``code`` in a fresh interpreter, so the real Quartz is used even
    when another test has put a stand-in in sys.modules. Prints JSON."""
    code = f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n" + code
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          env=dict(os.environ), timeout=60)
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout.strip().splitlines()[-1])


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


def registry(platform=MAC):
    clock = Clock()
    r = ah.ActionHotkeys(platform, clock=clock, spawn=lambda fn: fn())
    return r, clock


# ── the combination checker ──────────────────────────────────────────────────

def test_a_good_combination_is_stored_in_one_order_with_its_name():
    assert ah.check(["t", "cmd", "option"], MAC) == {
        "ok": True, "keys": ["option", "cmd", "t"], "display": "Option + Command + T"}
    assert ah.check(["control", "shift", "7"], MAC)["keys"] == ["control", "shift", "7"]
    assert ah.check(["ctrl", "alt", "f5"], MAC)["keys"] == ["control", "option", "f5"], "other spellings"
    assert ah.check(["T", " Control ", "OPTION"], MAC)["keys"] == ["control", "option", "t"]
    win = ah.check(["y", "shift", "ctrl"], WIN)
    assert win == {"ok": True, "keys": ["ctrl", "shift", "y"], "display": "Ctrl + Shift + Y"}
    assert ah.check(["shift", "ctrl", "9"], WIN)["keys"] == ["ctrl", "shift", "9"]
    assert ah.check(["control", "shift", "y"], WIN)["keys"] == ["ctrl", "shift", "y"], "Windows reads Control as Ctrl"
    # Ctrl and Alt are AltGr on many keyboards (S11a): a letter with them is refused, so
    # these Windows examples use Ctrl with Shift instead of Ctrl with Alt.
    assert ah.check(["t", "alt", "ctrl"], WIN)["ok"] is False


BAD = [
    ("nothing", [], MAC, "Hold the keys"),
    ("not a list", "cmd+t", MAC, "Hold the keys"),
    ("blank key", ["cmd", " "], MAC, "Hold the keys"),
    ("no letter", ["control", "option"], MAC, "Add a letter"),
    ("two letters", ["control", "option", "t", "y"], MAC, "one letter"),
    ("a letter alone", ["t"], MAC, "Control or Command"),
    ("one modifier", ["cmd", "t"], MAC, "Control or Command"),
    ("shift and option only", ["shift", "option", "t"], MAC, "Control or Command"),
    ("fn", ["fn", "control", "t"], MAC, "Fn"),
    ("space", ["control", "option", "space"], MAC, "Space can't be used"),
    ("escape", ["control", "option", "esc"], MAC, "can't be used"),
    ("the Windows key on a Mac", ["win", "control", "t"], MAC, "Windows key"),
    ("a Mac key on Windows", ["cmd", "ctrl", "t"], WIN, "Mac key"),
    ("option on Windows", ["option", "ctrl", "t"], WIN, "Mac key"),
    ("Windows: shift and alt", ["alt", "shift", "t"], WIN, "Ctrl or the Windows key"),
    ("Windows: one modifier", ["ctrl", "t"], WIN, "Ctrl or the Windows key"),
]


@pytest.mark.parametrize("label,keys,platform,fragment", BAD, ids=[b[0] for b in BAD])
def test_a_bad_combination_says_why_in_one_sentence(label, keys, platform, fragment):
    got = ah.check(keys, platform)
    assert got["ok"] is False and fragment in got["error"], got
    assert got["error"].endswith(".") and "—" not in got["error"] and "–" not in got["error"]


def test_combinations_the_system_or_most_apps_use_are_refused():
    for keys in (["control", "cmd", "q"], ["cmd", "shift", "q"], ["cmd", "shift", "4"], ["cmd", "option", "d"]):
        assert "already use" in ah.check(keys, MAC)["error"], keys
    for keys in (["win", "shift", "s"], ["ctrl", "shift", "t"], ["win", "ctrl", "d"]):
        assert "already use" in ah.check(keys, WIN)["error"], keys
    assert ah.check(["control", "option", "t"], MAC)["ok"]


def test_a_combination_that_overlaps_the_dictation_hotkey_is_refused_either_way():
    assert "dictation hotkey" in ah.check(["control", "shift", "t"], MAC, ["control", "shift"])["error"]
    assert ah.check(["control", "option", "t"], MAC, ["control", "shift"])["ok"]
    assert ah.check(["control", "option", "t"], MAC, ["fn"])["ok"]
    assert ah.check(["control", "option", "t"], MAC, ["cmd", "shift", "space"])["ok"]
    assert "dictation hotkey" in ah.check(["win", "ctrl", "shift", "y"], WIN, ["ctrl", "shift"])["error"]
    assert ah.check(["ctrl", "shift", "y"], WIN, ["win", "ctrl"])["ok"]
    # The dictation hotkey is saved with whatever spelling the recorder used.
    assert "dictation hotkey" in ah.check(["control", "option", "t"], MAC, ["ctrl", "alt"])["error"]


def test_the_check_and_the_reverse_check_agree():
    assert ah.overlaps_dictation(["control", "option", "t"], ["control", "option"], MAC)
    assert not ah.overlaps_dictation(["control", "option", "t"], ["control", "shift"], MAC)
    assert not ah.overlaps_dictation([], ["fn"], MAC)
    clash = ah.dictation_clash_message(["control", "option"], [["control", "option", "t"]], MAC)
    assert "Control + Option + T" in clash and clash.endswith(".")
    assert ah.dictation_clash_message(["fn"], [["control", "option", "t"], []], MAC) == ""


def test_another_actions_combination_is_refused():
    got = ah.check(["control", "option", "t"], MAC, others=[["option", "control", "t"]])
    assert got["ok"] is False and "Another shortcut" in got["error"]
    assert ah.check(["control", "option", "t"], MAC, others=[["control", "option", "y"]])["ok"]


def test_the_dictation_hotkey_defaults_per_platform():
    assert ah.dictation_keys({}, MAC) == ["fn"] and ah.dictation_keys({}, WIN) == ["win", "ctrl"]
    assert ah.dictation_keys({"hotkey_keys": ["cmd", "shift", "space"]}, MAC) == ["cmd", "shift", "space"]
    assert ah.dictation_keys({"hotkey_keys": "fn"}, MAC) == ["fn"]
    assert ah.dictation_keys(None, WIN) == ["win", "ctrl"]


def test_every_allowed_key_has_a_code_on_both_platforms():
    for k in "abcdefghijklmnopqrstuvwxyz0123456789":
        assert k in ah.MAC_KEYCODES and k in ah.WINDOWS_VKS
    for i in range(1, 21):
        assert f"f{i}" in ah.MAC_KEYCODES
    for i in range(1, 25):
        assert f"f{i}" in ah.WINDOWS_VKS
    assert len(set(ah.MAC_KEYCODES.values())) == len(ah.MAC_KEYCODES), "no two keys share a code"
    assert ah.check(["control", "option", "f21"], MAC)["ok"] is False
    assert ah.check(["ctrl", "alt", "f21"], WIN)["ok"] is True


@mac_only
def test_the_mac_codes_match_the_dictation_hotkeys_for_the_letters_it_has():
    got = isolated("import json, mac_hotkey_monitor as m; print(json.dumps(m.KEY_TO_KEYCODE))")
    letters = {k: v for k, v in got.items() if len(k) == 1 and k.isalpha()}
    assert len(letters) == 26
    for name, code in letters.items():
        assert ah.MAC_KEYCODES[name] == code, name


# ── the registry ─────────────────────────────────────────────────────────────

def test_a_press_of_the_registered_combination_calls_the_action():
    r, _ = registry()
    seen = []
    assert r.register("transform", ["control", "option", "t"], lambda: seen.append(1))["ok"]
    assert r.key_down("t", ["option", "control"]) == ["transform"]
    assert seen == [1]
    assert r.registered() == {"transform": ["control", "option", "t"]}


def test_only_the_exact_modifiers_fire():
    r, clock = registry()
    seen = []
    r.register("transform", ["control", "option", "t"], lambda: seen.append(1))
    for key, mods in (("t", ["control"]), ("t", ["control", "option", "shift"]), ("t", []),
                      ("t", ["option"]), ("y", ["control", "option"])):
        clock.advance(1)
        assert r.key_down(key, mods) == [], (key, mods)
        r.key_up(key)
    assert seen == []


def test_a_held_key_fires_once():
    r, clock = registry()
    seen = []
    r.register("transform", ["control", "option", "t"], lambda: seen.append(1))
    mods = ["control", "option"]
    r.key_down("t", mods)
    for _ in range(200):                            # six seconds of the system repeating the key
        clock.advance(0.03)
        assert r.key_down("t", mods) == []
    assert seen == [1], "a key that keeps repeating stays held, however long"
    r.key_up("t")
    clock.advance(1)
    assert r.key_down("t", mods) == ["transform"] and seen == [1, 1]


def test_a_repeat_the_system_marked_never_fires_even_as_the_first_event():
    r, _ = registry()
    r.register("transform", ["control", "option", "t"], lambda: None)
    assert r.key_down("t", ["control", "option"], repeat=True) == []


def test_a_key_up_that_never_came_does_not_lock_the_shortcut_out():
    r, clock = registry()
    seen = []
    r.register("transform", ["control", "option", "t"], lambda: seen.append(1))
    r.key_down("t", ["control", "option"])
    clock.advance(ah.HELD_FOR_S + 1)            # no key-up, and no repeats for a while
    assert r.key_down("t", ["control", "option"]) == ["transform"]
    assert seen == [1, 1]


def test_two_presses_in_a_blink_are_one():
    r, clock = registry()
    seen = []
    r.register("transform", ["control", "option", "t"], lambda: seen.append(1))
    r.key_down("t", ["control", "option"])
    r.key_up("t")
    clock.advance(ah.MIN_GAP_S / 2)
    assert r.key_down("t", ["control", "option"]) == []
    r.key_up("t")
    clock.advance(ah.MIN_GAP_S)
    assert r.key_down("t", ["control", "option"]) == ["transform"]
    assert seen == [1, 1]


def test_unregister_stops_it_and_says_whether_it_was_there():
    r, _ = registry()
    r.register("transform", ["control", "option", "t"], lambda: None)
    assert r.unregister("transform") is True and r.unregister("transform") is False
    assert r.key_down("t", ["control", "option"]) == [] and r.registered() == {}


def test_registering_again_replaces_the_action_and_its_combination():
    r, clock = registry()
    seen = []
    r.register("transform", ["control", "option", "t"], lambda: seen.append("old"))
    r.register("transform", ["control", "option", "y"], lambda: seen.append("new"))
    assert r.key_down("t", ["control", "option"]) == []
    clock.advance(1)
    assert r.key_down("y", ["control", "option"]) == ["transform"] and seen == ["new"]
    assert r.registered() == {"transform": ["control", "option", "y"]}


def test_a_refused_combination_registers_nothing_and_keeps_the_old_one():
    r, _ = registry()
    r.register("transform", ["control", "option", "t"], lambda: None)
    bad = r.register("transform", ["t"], lambda: None)
    assert bad["ok"] is False and r.registered() == {"transform": ["control", "option", "t"]}


def test_two_actions_cannot_share_a_combination_but_each_may_change_its_own():
    r, _ = registry()
    assert r.register("transform", ["control", "option", "t"], lambda: None)["ok"]
    clash = r.register("paste_last", ["option", "control", "t"], lambda: None)
    assert clash["ok"] is False and "Another shortcut" in clash["error"]
    assert r.conflict("paste_last", ["control", "option", "t"]) == clash["error"]
    assert r.conflict("paste_last", ["control", "option", "y"]) is None
    assert r.conflict("transform", ["control", "option", "t"]) is None, "an action may keep its own"
    assert r.register("transform", ["option", "control", "t"], lambda: None)["ok"]


def test_registering_checks_the_dictation_hotkey_too():
    r, _ = registry()
    got = r.register("transform", ["control", "shift", "t"], lambda: None, ["control", "shift"])
    assert got["ok"] is False and "dictation hotkey" in got["error"]
    assert r.conflict("transform", ["control", "shift", "t"], ["control", "shift"]) == got["error"]


def test_two_actions_on_different_combinations_each_fire_for_their_own():
    r, clock = registry()
    seen = []
    r.register("a", ["control", "option", "t"], lambda: seen.append("a"))
    r.register("b", ["control", "option", "y"], lambda: seen.append("b"))
    r.key_down("y", ["control", "option"])
    clock.advance(1)
    r.key_down("t", ["control", "option"])
    assert seen == ["b", "a"]


def test_an_action_that_fails_is_logged_and_does_not_stop_the_others():
    log = []
    clock = Clock()
    r = ah.ActionHotkeys(MAC, clock=clock, log=log.append, spawn=lambda fn: fn())

    def boom():
        raise RuntimeError("private words")
    seen = []
    r.register("a", ["control", "option", "t"], boom)
    r.register("b", ["control", "option", "y"], lambda: seen.append(1))
    assert r.key_down("t", ["control", "option"]) == ["a"]
    clock.advance(1)
    assert r.key_down("y", ["control", "option"]) == ["b"] and seen == [1]
    assert log and "RuntimeError" in log[0] and "private words" not in log[0]


def test_the_action_runs_on_a_thread_of_its_own():
    """The key listener's thread must never wait for what the action does."""
    r = ah.ActionHotkeys(MAC)
    started, release, ran_on = threading.Event(), threading.Event(), []

    def slow():
        ran_on.append(threading.current_thread())
        started.set()
        release.wait(5)
    r.register("transform", ["control", "option", "t"], slow)
    t0 = time.monotonic()
    assert r.key_down("t", ["control", "option"]) == ["transform"]
    assert time.monotonic() - t0 < 1.0, "key_down returned while the action was still running"
    assert started.wait(2)
    assert ran_on[0] is not threading.current_thread() and ran_on[0].daemon
    release.set()


# ── the Mac listener ─────────────────────────────────────────────────────────

class FakeEvent:
    def __init__(self, keycode, flags=0, repeat=0):
        self.fields = {ah._FIELD_KEYCODE: keycode, ah._FIELD_AUTOREPEAT: repeat}
        self.flags = flags


def observer(r):
    return ah.MacComboObserver(r, get_flags=lambda e: e.flags, get_field=lambda e, f: e.fields[f])


CTRL, OPT, SHIFT, CMD = 0x40000, 0x80000, 0x20000, 0x100000
T = ah.MAC_KEYCODES["t"]


def test_the_mac_observer_turns_key_events_into_presses_and_swallows_only_the_shortcuts_key():
    r, clock = registry()
    seen = []
    r.register("transform", ["control", "option", "t"], lambda: seen.append(1))
    obs = observer(r)
    assert obs.handle(ah._KEY_DOWN, FakeEvent(T, CTRL | OPT)) is True, "the app in front does not get the key"
    assert seen == [1]
    assert obs.handle(ah._KEY_DOWN, FakeEvent(T, CTRL | OPT, repeat=1)) is True and seen == [1]
    assert obs.handle(ah._KEY_UP, FakeEvent(T, CTRL | OPT)) is True
    assert obs.handle(ah._KEY_UP, FakeEvent(T, CTRL | OPT)) is False, "a second key-up is not ours"
    clock.advance(1)
    # Caps Lock, Fn and the numeric-pad bits do not count as modifiers.
    assert obs.handle(ah._KEY_DOWN, FakeEvent(T, CTRL | OPT | 0x10000 | 0x800000 | 0x200000)) is True
    assert seen == [1, 1]
    # The modifier keys themselves always pass.
    assert obs.handle(ah._FLAGS_CHANGED, FakeEvent(0, CTRL | OPT)) is False
    assert obs.handle(ah._FLAGS_CHANGED, FakeEvent(0, 0)) is False


def test_the_mac_observer_ignores_what_is_not_a_registered_key():
    r, clock = registry()
    seen = []
    r.register("transform", ["control", "option", "t"], lambda: seen.append(1))
    obs = observer(r)
    assert obs.handle(ah._KEY_DOWN, FakeEvent(T, CMD | OPT)) is False          # other modifiers
    assert obs.handle(ah._KEY_DOWN, FakeEvent(999, CTRL | OPT)) is False       # a key with no name here
    assert obs.handle(12, FakeEvent(T, CTRL | OPT)) is False                   # not a key event (flags changed)
    assert seen == []


def test_the_mac_observer_cannot_raise_into_the_taps_other_handlers():
    class Broken:
        def key_down(self, *a, **k):
            raise RuntimeError("x")

        key_up = key_down
    obs = ah.MacComboObserver(Broken(), get_flags=lambda e: 0, get_field=lambda e, f: ah.MAC_KEYCODES["t"])
    assert obs.handle(ah._KEY_DOWN, object()) is False
    assert obs.handle(ah._KEY_UP, object()) is False

    def bad_field(e, f):
        raise ValueError("no such field")
    assert ah.MacComboObserver(Broken(), get_flags=lambda e: 0, get_field=bad_field).handle(ah._KEY_DOWN, 1) is False


class FakeListener:
    def __init__(self):
        self.observers = []

    def attach_observer(self, handler):
        self.observers.append(handler)


def test_the_registry_joins_the_dictation_listeners_tap_each_time_it_is_rebuilt():
    r, _ = registry()
    first, second = FakeListener(), FakeListener()
    assert r.attach_listener(first) is True and r.attach_listener(second) is True
    assert len(first.observers) == 1 and len(second.observers) == 1
    assert first.observers[0] is second.observers[0], "one observer, handed to each new tap"
    assert isinstance(first.observers[0], ah.MacComboObserver)


def test_the_same_listener_is_joined_once_however_often_it_is_asked():
    r, _ = registry()
    listener = FakeListener()
    for _ in range(4):
        assert r.attach_listener(listener) is True
    assert len(listener.observers) == 1


def test_a_listener_with_no_tap_to_join_is_a_quiet_no():
    r, _ = registry()
    assert r.attach_listener(object()) is False
    assert r.attach_listener(None) is False

    class Refusing:
        def attach_observer(self, handler):
            raise RuntimeError("tap is gone")
    log = []
    r2 = ah.ActionHotkeys(MAC, log=log.append)
    assert r2.attach_listener(Refusing()) is False and "RuntimeError" in log[0]


def test_windows_does_not_use_a_mac_listener_and_a_mac_has_no_watcher_thread():
    w, _ = registry(WIN)
    assert w.attach_listener(FakeListener()) is False
    m, _ = registry(MAC)
    assert m.start() is False, "a Mac listens through the dictation hotkey's tap"
    m.stop()


@mac_only
def test_the_mac_observer_reads_a_real_key_event():
    """An in-memory event from Quartz, never posted: its key code and flags
    are read the way the tap's callback reads them."""
    got = isolated("""
import json, action_hotkeys as ah
from Quartz import CGEventCreateKeyboardEvent, CGEventSetFlags, kCGEventKeyDown, kCGEventKeyUp
import os
from Quartz import CGEventGetIntegerValueField, kCGEventFlagsChanged, kCGEventSourceUnixProcessID
seen = []
r = ah.ActionHotkeys('Darwin', spawn=lambda fn: fn())
r.register('transform', ['control', 'option', 't'], lambda: seen.append(1))
# An event made here carries this process's id (the keyboard's carries none),
# which is how Waffler's own Cmd+C and Cmd+V are told from the person's keys.
# This one is read as if the keyboard had sent it.
obs = ah.MacComboObserver(r, own_pid=-1)
T = ah.MAC_KEYCODES['t']
down = CGEventCreateKeyboardEvent(None, T, True)
CGEventSetFlags(down, 0x40000 | 0x80000)
answer = obs.handle(kCGEventKeyDown, down)
up = CGEventCreateKeyboardEvent(None, T, False)
mine = ah.MacComboObserver(r)
print(json.dumps({'constants': [kCGEventKeyDown, kCGEventKeyUp, kCGEventFlagsChanged, kCGEventSourceUnixProcessID]
                  == [ah._KEY_DOWN, ah._KEY_UP, ah._FLAGS_CHANGED, ah._FIELD_SOURCE_PID],
                  'swallowed': answer, 'fired': seen, 'up': obs.handle(kCGEventKeyUp, up),
                  'own_pid_is_read': CGEventGetIntegerValueField(down, ah._FIELD_SOURCE_PID) == os.getpid(),
                  'own_events_ignored': mine.handle(kCGEventKeyDown, down) is False and seen == [1]}))
""")
    assert got == {"constants": True, "swallowed": True, "fired": [1], "up": True,
                   "own_pid_is_read": True, "own_events_ignored": True}


@mac_only
def test_the_dictation_listener_hands_the_observer_to_its_one_tap():
    got = isolated("""
import json, smart_hotkey, action_hotkeys as ah

class Tap:
    def __init__(self):
        self.handlers = []
    def add_handler(self, h):
        self.handlers.append(h)

listener = object.__new__(smart_hotkey.SmartHotkeyListener)
listener._tap = Tap()
marker = object()
listener.attach_observer(marker)
first = listener._tap.handlers == [marker]
r = ah.ActionHotkeys('Darwin')
ok = r.attach_listener(listener)
again = r.attach_listener(listener)
print(json.dumps({'first': first, 'attached': ok, 'again': again, 'count': len(listener._tap.handlers)}))
""")
    assert got == {"first": True, "attached": True, "again": True, "count": 2}


# ── the Windows watcher ──────────────────────────────────────────────────────

def test_a_windows_key_event_reads_the_modifiers_at_the_moment_it_goes_down():
    r, clock = registry(WIN)
    seen = []
    r.register("transform", ["ctrl", "alt", "f5"], lambda: seen.append(1))      # AltGr types no letter with an F key
    held = {0x11, 0x12}                      # Ctrl and Alt
    vk_t = ah.WINDOWS_VKS["f5"]
    ah.windows_event(r, vk_t, ah._WM_KEYDOWN, 0, lambda vk: vk in held)
    assert seen == [1]
    ah.windows_event(r, vk_t, ah._WM_KEYDOWN, 0, lambda vk: vk in held)      # auto-repeat
    assert seen == [1]
    ah.windows_event(r, vk_t, ah._WM_KEYUP, 0, lambda vk: vk in held)
    clock.advance(1)
    ah.windows_event(r, vk_t, ah._WM_SYSKEYDOWN, 0, lambda vk: vk in held)   # Alt makes it a "system" key
    assert seen == [1, 1]


def test_a_windows_key_with_the_wrong_modifiers_or_sent_by_a_program_does_nothing():
    r, clock = registry(WIN)
    seen = []
    r.register("transform", ["ctrl", "alt", "f5"], lambda: seen.append(1))
    vk_t = ah.WINDOWS_VKS["f5"]
    ah.windows_event(r, vk_t, ah._WM_KEYDOWN, 0, lambda vk: vk == 0x11)             # Ctrl only
    ah.windows_event(r, vk_t, ah._WM_KEYDOWN, ah._LLKHF_INJECTED, lambda vk: vk in (0x11, 0x12))
    ah.windows_event(r, 0x1B, ah._WM_KEYDOWN, 0, lambda vk: True)                   # Esc has no name here
    assert seen == []


def test_the_windows_key_counts_for_either_side():
    r, _ = registry(WIN)
    seen = []
    r.register("transform", ["win", "ctrl", "t"], lambda: seen.append(1))
    ah.windows_event(r, ah.WINDOWS_VKS["t"], ah._WM_KEYDOWN, 0, lambda vk: vk in (0x5C, 0x11))   # right Windows key
    assert seen == [1]


def test_the_windows_watcher_is_not_started_anywhere_else():
    r, _ = registry(WIN)
    if sys.platform != "win32":
        assert r.start() is False
    r.stop()
