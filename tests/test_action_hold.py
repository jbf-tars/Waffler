"""Hold-to-talk shortcuts (S8): key-down starts, any part of the combination
coming up ends, in order, once; and the combination may not clash with the
dictation hotkey or the Transform shortcut.

The decisions are pure and run here with fakes (``spawn`` runs a callback at
once). The Mac observer is fed fake events; the Windows reader is fed the
parameters the hook receives. Nothing listens to a real keyboard.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_hotkeys as ah  # noqa: E402

MAC, WIN = ah.MAC, ah.WINDOWS
CTRL, OPT, SHIFT, CMD = 0x40000, 0x80000, 0x20000, 0x100000
C = ah.MAC_KEYCODES["c"]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


class Rig:
    def __init__(self, platform=MAC, keys=("control", "option", "c"), **kw):
        self.clock = Clock()
        self.log = []
        self.events = []
        self.r = ah.ActionHotkeys(platform, clock=self.clock, log=self.log.append,
                                  spawn=lambda fn: fn(), **kw)
        self.verdict = self.r.register_hold("command", list(keys),
                                            lambda: self.events.append("down"),
                                            lambda: self.events.append("up"))

    def down(self, key="c", mods=("control", "option"), **kw):
        return self.r.key_down(key, list(mods), **kw)


def test_a_hold_calls_down_when_the_combination_goes_down_and_up_when_the_key_comes_up():
    rig = Rig()
    assert rig.verdict["ok"] and rig.verdict["keys"] == ["control", "option", "c"]
    assert rig.down() == ["command"]
    assert rig.events == ["down"]
    rig.clock.advance(3)                                     # a long instruction
    rig.r.key_up("c")
    assert rig.events == ["down", "up"]
    rig.r.key_up("c")                                        # a second key-up is nothing
    assert rig.events == ["down", "up"]


def test_a_modifier_coming_up_first_ends_the_hold_and_the_key_coming_up_after_adds_nothing():
    rig = Rig()
    rig.down()
    rig.r.modifiers_changed(["option"])                      # Control let go first
    assert rig.events == ["down", "up"]
    rig.r.key_up("c")
    assert rig.events == ["down", "up"], "one up, however the keys come off"


def test_any_modifier_of_the_combination_ends_it_but_an_extra_one_does_not():
    for released in (["control"], ["option"], ["control", "option"]):
        rig = Rig()
        rig.down()
        rig.r.modifiers_changed(["control", "option", "shift"])      # Shift joined: still held
        assert rig.events == ["down"]
        rig.r.modifiers_changed([m for m in ("control", "option") if m not in released])
        assert rig.events == ["down", "up"], released


def test_modifier_changes_with_nothing_held_are_nothing():
    rig = Rig()
    rig.r.modifiers_changed([])
    rig.r.modifiers_changed(["control"])
    rig.r.modifier_up("control")
    assert rig.events == []


def test_a_key_that_keeps_repeating_while_held_never_starts_a_second_hold():
    rig = Rig()
    rig.down()
    for _ in range(300):
        rig.clock.advance(0.03)
        assert rig.down() == [], "auto-repeat without the repeat flag (Windows)"
        assert rig.down(repeat=True) == []
    assert rig.events == ["down"]
    rig.r.key_up("c")
    assert rig.events == ["down", "up"]


def test_a_long_hold_with_no_repeats_is_still_one_hold_whatever_else_is_typed():
    rig = Rig()
    rig.down()
    rig.clock.advance(ah.HELD_FOR_S + 5)             # the system sends no repeats; the stale-key cleanup runs
    rig.r.key_down("x", ["control", "option"])       # some other key
    assert rig.down() == [], "the combination's key is still down: it is not a new press"
    assert rig.events == ["down"]
    rig.r.key_up("c")
    assert rig.events == ["down", "up"]


def test_only_the_exact_modifiers_start_a_hold():
    rig = Rig()
    for mods in (["control"], ["control", "option", "shift"], [], ["option"]):
        rig.clock.advance(1)
        assert rig.down(mods=mods) == []
        rig.r.key_up("c")
    assert rig.events == []


def test_two_presses_in_a_blink_are_one_hold_and_the_second_ones_key_up_adds_nothing():
    rig = Rig()
    rig.down()
    rig.r.key_up("c")
    rig.clock.advance(ah.MIN_GAP_S / 2)
    assert rig.down() == []
    rig.r.key_up("c")
    assert rig.events == ["down", "up"]
    rig.clock.advance(ah.MIN_GAP_S)
    assert rig.down() == ["command"]


def test_a_new_hold_can_start_once_the_last_has_ended():
    rig = Rig()
    for _ in range(3):
        rig.clock.advance(1)
        assert rig.down() == ["command"]
        rig.r.key_up("c")
    assert rig.events == ["down", "up"] * 3


def test_unregistering_during_a_hold_ends_it_once_and_nothing_starts_again():
    rig = Rig()
    rig.down()
    assert rig.r.unregister("command") is True
    assert rig.events == ["down", "up"]
    rig.r.key_up("c")
    rig.clock.advance(1)
    assert rig.down() == [] and rig.events == ["down", "up"]


def test_registering_again_during_a_hold_ends_the_old_one():
    rig = Rig()
    rig.down()
    again = rig.r.register_hold("command", ["control", "option", "y"],
                                lambda: rig.events.append("down2"), lambda: rig.events.append("up2"))
    assert again["ok"] and rig.events == ["down", "up"]
    rig.clock.advance(1)
    assert rig.down("y") == ["command"] and rig.events == ["down", "up", "down2"]


def test_a_callback_that_fails_is_logged_without_its_text_and_the_hold_still_ends():
    rig = Rig()
    boom = []

    def down():
        boom.append(1)
        raise RuntimeError("private words")
    rig.r.register_hold("command", ["control", "option", "c"], down, lambda: rig.events.append("up"))
    rig.down()
    rig.r.key_up("c")
    assert boom == [1] and rig.events == ["up"]
    assert rig.log and "RuntimeError" in rig.log[0] and "private words" not in " ".join(rig.log)


def test_a_hold_and_a_press_action_live_side_by_side():
    rig = Rig()
    pressed = []
    assert rig.r.register("transform", ["control", "option", "t"], lambda: pressed.append(1))["ok"]
    rig.clock.advance(1)
    rig.r.key_down("t", ["control", "option"])
    assert pressed == [1] and rig.events == []
    rig.r.key_up("t")
    rig.clock.advance(1)
    rig.down()
    rig.r.key_up("c")
    assert rig.events == ["down", "up"] and pressed == [1]
    assert rig.r.registered() == {"command": ["control", "option", "c"], "transform": ["control", "option", "t"]}


# ── conflicts ────────────────────────────────────────────────────────────────

def test_a_hold_is_refused_when_it_overlaps_the_dictation_hotkey():
    rig = Rig(keys=("control", "option", "c"))
    got = rig.r.register_hold("command", ["control", "shift", "c"], lambda: None, lambda: None, ["control", "shift"])
    assert got["ok"] is False and "dictation hotkey" in got["error"]
    assert rig.r.registered() == {"command": ["control", "option", "c"]}, "the old one is kept"
    assert rig.r.conflict("command", ["control", "shift", "c"], ["control", "shift"]) == got["error"]


def test_a_hold_may_not_use_the_transform_combination_or_a_nested_one():
    r = ah.ActionHotkeys(MAC, spawn=lambda fn: fn())
    assert r.register("transform", ["control", "option", "t"], lambda: None)["ok"]
    same = r.register_hold("command", ["option", "control", "t"], lambda: None, lambda: None)
    assert same["ok"] is False and "Another shortcut" in same["error"]
    nested = r.register_hold("command", ["control", "option", "shift", "t"], lambda: None, lambda: None)
    assert nested["ok"] is False and "overlaps" in nested["error"]
    assert r.register_hold("command", ["control", "option", "y"], lambda: None, lambda: None)["ok"]
    # ...and the Transform shortcut may not take the hold's either.
    back = r.register("transform", ["control", "option", "shift", "y"], lambda: None)
    assert back["ok"] is False and "overlaps" in back["error"]


def test_check_keeps_the_exact_match_message_and_adds_one_for_nested_shortcuts():
    got = ah.check(["control", "option", "t"], MAC, others=[["option", "control", "t"]])
    assert got["ok"] is False and "Another shortcut" in got["error"]
    nested = ah.check(["control", "option", "shift", "t"], MAC, others=[["control", "option", "t"]])
    assert nested["ok"] is False and "overlaps" in nested["error"] and nested["error"].endswith(".")
    assert "—" not in nested["error"] and "–" not in nested["error"]
    assert ah.check(["control", "option", "t"], MAC, others=[["control", "option", "y"]])["ok"]
    assert ah.check(["control", "option", "t"], MAC, others=[["control", "shift", "y"]])["ok"]


def test_the_saved_shortcuts_are_read_from_settings_with_any_spelling_and_junk_ignored():
    stored = {"transforms": {"hotkey": ["control", "option", "t"]},
              "command_mode": {"hotkey": ["ctrl", "alt", "c"]}}
    assert ah.saved_shortcuts(stored) == {"transform": ["control", "option", "t"],
                                          "command": ["ctrl", "alt", "c"]}
    assert ah.saved_shortcuts(stored, skip="command") == {"transform": ["control", "option", "t"]}
    assert ah.saved_shortcuts({"transforms": {"hotkey": "cmd+t"}, "command_mode": {"hotkey": [1, None]}}) == {}
    assert ah.saved_shortcuts({"transforms": "no"}) == {} and ah.saved_shortcuts(None) == {}


def test_a_new_dictation_hotkey_is_checked_against_both_shortcuts():
    stored = {"transforms": {"hotkey": ["control", "option", "t"]},
              "command_mode": {"hotkey": ["control", "shift", "c"]}}
    shortcuts = list(ah.saved_shortcuts(stored).values())
    assert "Control + Option + T" in ah.dictation_clash_message(["control", "option"], shortcuts, MAC)
    assert "Control + Shift + C" in ah.dictation_clash_message(["control", "shift"], shortcuts, MAC)
    assert ah.dictation_clash_message(["fn"], shortcuts, MAC) == ""


# ── the order down then up ───────────────────────────────────────────────────

def test_down_and_up_run_in_order_on_the_actions_own_thread_even_when_down_is_slow():
    """The key listener posts both within a few milliseconds. If each had its
    own thread, up could run before down had started the recording."""
    r = ah.ActionHotkeys(MAC)
    order, release = [], threading.Event()

    def down():
        order.append("down-start")
        release.wait(5)
        order.append("down-end")

    r.register_hold("command", ["control", "option", "c"], down, lambda: order.append("up"))
    t0 = time.monotonic()
    r.key_down("c", ["control", "option"])
    r.key_up("c")
    assert time.monotonic() - t0 < 1.0, "the key listener's thread never waits for the action"
    time.sleep(0.15)
    assert order == ["down-start"], "up waits for down to finish"
    release.set()
    deadline = time.monotonic() + 3
    while len(order) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert order == ["down-start", "down-end", "up"]


def test_the_hold_callbacks_never_run_on_the_listeners_thread():
    r = ah.ActionHotkeys(MAC)
    seen, done = [], threading.Event()
    r.register_hold("command", ["control", "option", "c"],
                    lambda: seen.append(threading.current_thread()), lambda: done.set())
    r.key_down("c", ["control", "option"])
    r.key_up("c")
    assert done.wait(3)
    assert seen[0] is not threading.current_thread() and seen[0].daemon


# ── the Mac observer ─────────────────────────────────────────────────────────

class FakeEvent:
    def __init__(self, keycode, flags=0, repeat=0):
        self.fields = {ah._FIELD_KEYCODE: keycode, ah._FIELD_AUTOREPEAT: repeat}
        self.flags = flags


def observer(r):
    return ah.MacComboObserver(r, get_flags=lambda e: e.flags, get_field=lambda e, f: e.fields[f])


def test_the_mac_observer_turns_key_and_flag_events_into_a_hold():
    rig = Rig()
    obs = observer(rig.r)
    assert obs.handle(ah._FLAGS_CHANGED, FakeEvent(0, CTRL)) is False
    assert obs.handle(ah._FLAGS_CHANGED, FakeEvent(0, CTRL | OPT)) is False
    assert obs.handle(ah._KEY_DOWN, FakeEvent(C, CTRL | OPT)) is False
    assert rig.events == ["down"]
    assert obs.handle(ah._KEY_DOWN, FakeEvent(C, CTRL | OPT, repeat=1)) is False
    assert obs.handle(ah._FLAGS_CHANGED, FakeEvent(0, CTRL | OPT | SHIFT)) is False     # an extra key
    assert rig.events == ["down"]
    assert obs.handle(ah._FLAGS_CHANGED, FakeEvent(0, OPT)) is False                      # Control let go
    assert rig.events == ["down", "up"]
    assert obs.handle(ah._KEY_UP, FakeEvent(C, OPT)) is False
    assert rig.events == ["down", "up"]


def test_the_mac_observer_ends_a_hold_on_the_keys_own_release_too():
    rig = Rig()
    obs = observer(rig.r)
    obs.handle(ah._KEY_DOWN, FakeEvent(C, CTRL | OPT))
    obs.handle(ah._KEY_UP, FakeEvent(C, CTRL | OPT))
    assert rig.events == ["down", "up"]
    obs.handle(ah._FLAGS_CHANGED, FakeEvent(0, 0))
    assert rig.events == ["down", "up"]


def test_the_mac_observer_ignores_keys_a_program_posted_itself():
    """Waffler's own Cmd+C and Cmd+V come back through the tap; a Cmd+C key-up
    must not end a hold on C."""
    rig = Rig(keys=("control", "option", "c"))
    me = 4242
    obs = ah.MacComboObserver(rig.r, get_flags=lambda e: e.flags,
                              get_field=lambda e, f: e.fields.get(f, 0), own_pid=me)
    obs.handle(ah._KEY_DOWN, FakeEvent(C, CTRL | OPT))
    posted = FakeEvent(C, CMD)
    posted.fields[ah._FIELD_SOURCE_PID] = me
    obs.handle(ah._KEY_DOWN, posted)
    obs.handle(ah._KEY_UP, posted)
    assert rig.events == ["down"], "its own key-up is not the person letting go"
    real_up = FakeEvent(C, CTRL | OPT)
    obs.handle(ah._KEY_UP, real_up)
    assert rig.events == ["down", "up"]


def test_the_mac_observer_cannot_raise_into_the_taps_other_handlers_for_flag_changes():
    class Broken:
        def modifiers_changed(self, *a, **k):
            raise RuntimeError("x")

        key_down = key_up = modifiers_changed
    obs = ah.MacComboObserver(Broken(), get_flags=lambda e: 0, get_field=lambda e, f: 0)
    assert obs.handle(ah._FLAGS_CHANGED, object()) is False


# ── the Windows reader ───────────────────────────────────────────────────────

def test_windows_a_key_up_or_a_modifier_up_ends_the_hold():
    rig = Rig(WIN, keys=("ctrl", "alt", "c"))
    held = {0x11, 0x12}
    vk_c = ah.WINDOWS_VKS["c"]
    state = lambda vk: vk in held      # noqa: E731
    ah.windows_event(rig.r, vk_c, ah._WM_KEYDOWN, 0, state)
    ah.windows_event(rig.r, vk_c, ah._WM_KEYDOWN, 0, state)             # auto-repeat
    assert rig.events == ["down"]
    held.discard(0x12)
    ah.windows_event(rig.r, 0xA4, ah._WM_KEYUP, 0, state)               # left Alt up (SYSKEYUP in practice)
    assert rig.events == ["down", "up"]
    ah.windows_event(rig.r, vk_c, ah._WM_KEYUP, 0, state)
    assert rig.events == ["down", "up"]

    rig.clock.advance(1)
    held.update({0x11, 0x12})
    ah.windows_event(rig.r, vk_c, ah._WM_SYSKEYDOWN, 0, state)
    ah.windows_event(rig.r, vk_c, ah._WM_SYSKEYUP, 0, state)
    assert rig.events == ["down", "up", "down", "up"]


@pytest.mark.parametrize("vk,name", [(0xA2, "ctrl"), (0xA3, "ctrl"), (0x11, "ctrl"), (0xA0, "shift"),
                                     (0xA1, "shift"), (0xA4, "alt"), (0xA5, "alt"), (0x5B, "win"), (0x5C, "win")])
def test_windows_either_side_of_a_modifier_counts(vk, name):
    keys = {"ctrl": ("ctrl", "alt", "c"), "alt": ("ctrl", "alt", "c"), "shift": ("ctrl", "shift", "y"),
            "win": ("win", "ctrl", "c")}[name]
    rig = Rig(WIN, keys=keys)
    mods = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
    held = {mods[m] for m in keys if m in mods}
    ah.windows_event(rig.r, ah.WINDOWS_VKS[keys[-1]], ah._WM_KEYDOWN, 0, lambda v: v in held)
    assert rig.events == ["down"]
    ah.windows_event(rig.r, vk, ah._WM_KEYUP, 0, lambda v: False)
    assert rig.events == ["down", "up"]


def test_windows_a_modifier_that_is_not_part_of_the_combination_changes_nothing():
    rig = Rig(WIN, keys=("ctrl", "alt", "c"))
    held = {0x11, 0x12}
    ah.windows_event(rig.r, ah.WINDOWS_VKS["c"], ah._WM_KEYDOWN, 0, lambda v: v in held)
    ah.windows_event(rig.r, 0xA0, ah._WM_KEYUP, 0, lambda v: v in held)        # Shift was never part of it
    ah.windows_event(rig.r, 0x5B, ah._WM_KEYUP, 0, lambda v: v in held)
    assert rig.events == ["down"]


def test_windows_keys_a_program_sent_are_ignored_so_its_own_paste_never_ends_a_hold():
    rig = Rig(WIN, keys=("ctrl", "alt", "c"))
    held = {0x11, 0x12}
    ah.windows_event(rig.r, ah.WINDOWS_VKS["c"], ah._WM_KEYDOWN, 0, lambda v: v in held)
    ah.windows_event(rig.r, 0xA2, ah._WM_KEYUP, ah._LLKHF_INJECTED, lambda v: False)     # its Ctrl+V's Ctrl
    ah.windows_event(rig.r, ah.WINDOWS_VKS["c"], ah._WM_KEYUP, ah._LLKHF_INJECTED, lambda v: False)
    assert rig.events == ["down"]
