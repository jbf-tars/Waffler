"""One registry of shortcut actions, one validator, and the swallow table (S11a).

The actions that can have a shortcut (Transform, Command mode, Paste last, Copy
last) are one list in src/action_hotkeys.py. This file holds the rules every
shortcut passes (``check`` and ``validate``), the decision about which keys the
app in front must not receive (``swallow_down``, ``swallow_up``) and the hold
that is modifier keys alone. All of it is pure: fake clocks, fake timers and
fake key events; nothing here listens to a keyboard or installs a tap.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_hotkeys as ah  # noqa: E402

MAC, WIN = ah.MAC, ah.WINDOWS


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


class Timers:
    """A timer that waits for the test to say it has gone off."""

    def __init__(self):
        self.pending = []

    def __call__(self, delay, fn):
        self.pending.append((delay, fn))
        return None

    def fire(self):
        due, self.pending = self.pending, []
        for _delay, fn in due:
            fn()


def registry(platform=MAC):
    clock, timers = Clock(), Timers()
    r = ah.ActionHotkeys(platform, clock=clock, spawn=lambda fn: fn(), timer=timers)
    return r, clock, timers


def no_dashes(text):
    return "—" not in text and "–" not in text


# ── the list of actions ──────────────────────────────────────────────────────

def test_the_actions_are_four_with_a_kind_a_label_and_a_place_in_settings():
    assert [a.id for a in ah.ACTIONS] == ["transform", "command", "paste_last", "copy_last"]
    assert {a.id: a.kind for a in ah.ACTIONS} == {"transform": "press", "command": "hold",
                                                  "paste_last": "press", "copy_last": "press"}
    assert [a.label for a in ah.ACTIONS] == ["Transform", "Command mode", "Paste last", "Copy last"]
    for a in ah.ACTIONS:
        assert a.blurb.endswith(".") and no_dashes(a.blurb), a.id
    # The old settings keep their places: nothing was moved to add the new two.
    assert ah.SPECS["transform"].path == ("transforms", "hotkey")
    assert ah.SPECS["command"].path == ("command_mode", "hotkey")
    assert ah.SPECS["paste_last"].path == ("paste_last_hotkey",)
    assert ah.SPECS["copy_last"].path == ("copy_last_hotkey",)


def test_no_action_has_a_shortcut_until_one_is_chosen():
    assert ah.saved_shortcuts({}) == {}
    assert ah.saved_shortcuts(None) == {}
    for a in ah.ACTIONS:
        assert ah.stored_keys({}, a.id) == []
    assert ah.stored_keys({}, "nothing") == []


def test_saved_shortcuts_reads_all_four_with_any_spelling_and_ignores_junk():
    stored = {"transforms": {"hotkey": ["control", "option", "t"]},
              "command_mode": {"hotkey": ["ctrl", "alt", "c"]},
              "paste_last_hotkey": ["control", "option", "v"],
              "copy_last_hotkey": ["f16"]}
    assert ah.saved_shortcuts(stored) == {
        "transform": ["control", "option", "t"], "command": ["ctrl", "alt", "c"],
        "paste_last": ["control", "option", "v"], "copy_last": ["f16"]}
    assert ah.saved_shortcuts(stored, skip="paste_last").keys() == {"transform", "command", "copy_last"}
    junk = {"paste_last_hotkey": "cmd+v", "copy_last_hotkey": [1, None], "transforms": 5}
    assert ah.saved_shortcuts(junk) == {}


def test_transform_and_command_count_only_when_their_feature_is_on():
    assert ah.feature_on({}, "transform") is False and ah.feature_on({}, "command") is False
    assert ah.feature_on({"transforms": {"enabled": True}}, "transform") is True
    assert ah.feature_on({"transforms": {"enabled": "yes"}}, "transform") is False
    assert ah.feature_on({"command_mode": {"enabled": True}}, "command") is True
    assert ah.feature_on({"command_mode": {"enabled": True}}, "transform") is False
    assert ah.feature_on({}, "paste_last") is True and ah.feature_on({}, "copy_last") is True
    assert ah.SPECS["transform"].feature == "Transforms" and ah.SPECS["command"].feature == "Command mode"
    assert ah.SPECS["paste_last"].feature == "" and ah.SPECS["copy_last"].feature == ""


# ── the rules: bare keys ─────────────────────────────────────────────────────

@pytest.mark.parametrize("platform", [MAC, WIN])
def test_a_function_key_alone_is_a_shortcut_for_a_press_f1_to_f20(platform):
    for n in range(1, 21):
        got = ah.check([f"f{n}"], platform)
        if platform == MAC and n in (13, 14, 15):
            continue                     # see the Fn test below: they are free unless the hotkey is Fn
        assert got["ok"] is True and got["keys"] == [f"f{n}"], (platform, n, got)
    assert ah.check(["f16"], platform)["display"] == "F16"


@pytest.mark.parametrize("platform", [MAC, WIN])
def test_other_bare_keys_are_refused(platform):
    for keys in (["t"], ["7"], ["f21"], ["f24"]):
        got = ah.check(keys, platform)
        assert got["ok"] is False and got["error"].endswith(".") and no_dashes(got["error"]), keys
    assert "Control" in ah.check(["t"], MAC)["error"] and "Ctrl" in ah.check(["t"], WIN)["error"]
    assert "F key from F1 to F20" in ah.check(["t"], MAC)["error"], "the refusal says what can stand alone"


def test_a_function_key_with_one_ordinary_modifier_is_fine_as_a_press_but_not_as_a_hold():
    assert ah.check(["shift", "f5"], MAC)["ok"] is True
    assert ah.check(["option", "f5"], MAC)["ok"] is True
    assert ah.check(["shift", "f5"], MAC, hold=True)["ok"] is False
    assert ah.check(["f5"], MAC, hold=True)["ok"] is False
    assert "F key from F1" not in ah.check(["f5"], MAC, hold=True)["error"], "a hold has no such exception"


def test_f13_to_f15_are_refused_on_a_mac_whose_dictation_hotkey_is_fn():
    for n in (13, 14, 15):
        got = ah.check([f"f{n}"], MAC, ["fn"])
        assert got["ok"] is False and "Fn" in got["error"] and "F16" in got["error"], n
        assert ah.check(["control", "option", f"f{n}"], MAC, ["fn"])["ok"] is False
        assert ah.check([f"f{n}"], MAC, ["cmd", "shift"])["ok"] is True, "free once the hotkey is not Fn"
        assert ah.check([f"f{n}"], WIN, ["win", "ctrl"])["ok"] is True
    assert ah.check(["f16"], MAC, ["fn"])["ok"] is True


# ── the rules: what the system and most apps use ─────────────────────────────

def test_every_reserved_combination_the_page_lists_is_refused_with_who_uses_it():
    for platform in (MAC, WIN):
        listed = ah.reserved_list(platform)
        assert len(listed) >= 14
        for item in listed:
            assert item["display"] and item["who"] and item["what"]
            got = ah.check(item["keys"], platform)
            assert got["ok"] is False, (platform, item["display"])
            assert "already use" in got["error"] and item["display"] in got["error"], got
            assert item["what"] in got["error"] and got["error"].endswith("Choose another."), got
            assert no_dashes(got["error"])


def test_the_refusal_names_the_combination_and_its_use():
    assert ah.check(["control", "cmd", "q"], MAC)["error"] == \
        "Your Mac already uses Control + Command + Q to lock the screen. Choose another."
    assert ah.check(["win", "shift", "s"], WIN)["error"] == \
        "Windows already uses Win + Shift + S to take a snip. Choose another."
    assert ah.check(["cmd", "shift", "z"], MAC)["error"] == \
        "Most apps already use Shift + Command + Z to redo. Choose another."


def test_keys_that_cannot_be_part_of_a_shortcut_say_why_when_the_system_uses_them():
    assert ah.check(["cmd", "space"], MAC)["error"] == \
        "Your Mac already uses Command + Space to open Spotlight. Choose another."
    assert "Control + Space" in ah.check(["control", "space"], MAC)["error"]
    assert "Command + Tab" in ah.check(["cmd", "tab"], MAC)["error"]
    assert "Win + Space" in ah.check(["win", "space"], WIN)["error"]
    assert "Alt + Tab" in ah.check(["alt", "tab"], WIN)["error"]
    # Anything else that is not a letter, a number or an F key is the plain refusal.
    assert ah.check(["control", "option", "space"], MAC)["error"].startswith("Space can't be used")
    assert "can't be used" in ah.check(["control", "option", "esc"], MAC)["error"]


# ── the rules: Windows and AltGr ─────────────────────────────────────────────

def test_on_windows_ctrl_and_alt_with_a_letter_or_a_number_is_refused_for_every_action():
    for keys in (["ctrl", "alt", "t"], ["alt", "ctrl", "7"], ["ctrl", "alt", "shift", "k"]):
        for hold in (False, True):
            got = ah.check(keys, WIN, hold=hold)
            assert got["ok"] is False and "AltGr" in got["error"] and no_dashes(got["error"]), (keys, hold)
    for action in ("transform", "command", "paste_last", "copy_last"):
        assert ah.validate(action, ["ctrl", "alt", "k"], WIN, {})["ok"] is False, action


def test_on_windows_what_is_not_altgr_still_passes():
    assert ah.check(["ctrl", "shift", "y"], WIN)["ok"]
    assert ah.check(["win", "alt", "y"], WIN)["ok"]
    assert ah.check(["win", "ctrl", "alt", "y"], WIN)["ok"], "the Windows key makes it no AltGr"
    assert ah.check(["ctrl", "alt", "f5"], WIN)["ok"], "AltGr and an F key type nothing"
    assert ah.check(["ctrl", "alt", "f5"], WIN, hold=True)["ok"] is False, "a hold is refused as before"


def test_a_mac_has_no_altgr():
    assert ah.check(["control", "option", "t"], MAC)["ok"]


# ── the rules: modifier keys alone (Mac holds) ───────────────────────────────

@pytest.mark.parametrize("keys", [["control", "option"], ["control", "cmd"], ["control", "shift"],
                                  ["cmd", "option"], ["cmd", "shift"], ["control", "option", "cmd"],
                                  ["ctrl", "alt"]])
def test_a_mac_hold_may_be_modifier_keys_alone(keys):
    got = ah.check(keys, MAC, hold=True)
    assert got["ok"] is True, got
    assert not any(k not in ah.MAC_MODIFIERS for k in got["keys"])
    assert got["display"].count("+") == len(keys) - 1


def test_modifier_keys_alone_are_only_for_a_hold_on_a_mac():
    assert ah.check(["control", "option"], MAC)["error"] == "Add a letter, a number or an F key to the shortcut."
    assert ah.check(["ctrl", "shift"], WIN, hold=True)["error"] == "Add a letter, a number or an F key to the shortcut."


@pytest.mark.parametrize("keys,fragment", [
    (["shift"], "One modifier key on its own"),
    (["control"], "One modifier key on its own"),
    (["option"], "One modifier key on its own"),
    (["cmd"], "One modifier key on its own"),
    (["option", "shift"], "Include Control or Command"),
    (["fn", "control"], "Fn key can't be part"),
])
def test_modifier_keys_alone_that_would_misfire_are_refused_in_a_sentence(keys, fragment):
    got = ah.check(keys, MAC, hold=True)
    assert got["ok"] is False and fragment in got["error"] and got["error"].endswith(".")
    assert no_dashes(got["error"])


def test_modifier_keys_alone_may_not_be_or_sit_inside_the_dictation_hotkey():
    assert "dictation hotkey" in ah.check(["control", "option"], MAC, ["control", "option"], hold=True)["error"]
    assert "dictation hotkey" in ah.check(["control", "option", "cmd"], MAC, ["control", "option"], hold=True)["error"]
    assert "dictation hotkey" in ah.check(["control", "option"], MAC, ["control", "option", "shift"], hold=True)["error"]
    assert ah.check(["control", "option"], MAC, ["fn"], hold=True)["ok"]
    assert ah.check(["control", "option"], MAC, ["cmd", "shift"], hold=True)["ok"]


def test_modifier_keys_alone_may_not_sit_inside_another_shortcut_or_contain_one():
    others = [("Transform", ["control", "option", "t"])]
    got = ah.check(["control", "option"], MAC, [], others, hold=True)
    assert got["ok"] is False and "overlaps another shortcut (Transform)" in got["error"]
    assert ah.check(["control", "cmd"], MAC, [], others, hold=True)["ok"]
    back = ah.check(["control", "option", "t"], MAC, [], [("Command mode", ["control", "option"])])
    assert back["ok"] is False and "overlaps another shortcut (Command mode)" in back["error"]


def test_a_registry_takes_a_hold_of_modifier_keys_alone_on_a_mac_only():
    r, _, _ = registry(MAC)
    assert r.register_hold("command", ["control", "option"], lambda: None, lambda: None)["ok"]
    assert r.registered() == {"command": ["control", "option"]}
    w, _, _ = registry(WIN)
    assert w.register_hold("command", ["ctrl", "shift"], lambda: None, lambda: None)["ok"] is False


# ── the rules: clashes ───────────────────────────────────────────────────────

def test_a_combination_that_is_the_dictation_hotkey_or_inside_it_is_refused():
    assert "dictation hotkey" in ah.check(["control", "option", "t"], MAC, ["control", "option"])["error"]
    assert "dictation hotkey" in ah.check(["control", "option", "t"], MAC, ["ctrl", "alt", "t"])["error"]
    assert ah.check(["control", "option", "t"], MAC, ["control", "shift"])["ok"]


def test_the_hands_free_chord_needs_no_rule_because_space_cannot_be_in_a_shortcut():
    """Hands-free is the dictation hotkey held with Space. No shortcut may
    contain Space, so none can equal that chord; one inside it would be inside
    the dictation hotkey, which is refused."""
    for platform, ptt in ((MAC, ["cmd", "shift"]), (WIN, ["win", "ctrl"])):
        mods = ptt + (["option"] if platform == MAC else ["alt"])
        got = ah.check(mods + ["space"], platform, ptt)
        assert got["ok"] is False and ("can't be used" in got["error"] or "already use" in got["error"]), got
    assert ah.check(["cmd", "shift", "space"], MAC, ["cmd", "shift"])["ok"] is False


def test_another_actions_combination_is_named_in_the_refusal():
    same = ah.check(["control", "option", "t"], MAC, others=[("Transform", ["option", "control", "t"])])
    assert same["error"] == "Another shortcut (Transform) already uses that combination. Choose another."
    inside = ah.check(["control", "option", "shift", "t"], MAC, others=[("Command mode", ["control", "option", "t"])])
    assert inside["error"].startswith("That overlaps another shortcut (Command mode)")
    plain = ah.check(["control", "option", "t"], MAC, others=[["control", "option", "t"]])
    assert plain["error"] == "Another shortcut already uses that combination. Choose another."


def test_the_same_key_with_more_or_fewer_modifiers_still_overlaps():
    others = [("Paste last", ["control", "option", "v"])]
    assert ah.check(["control", "option", "shift", "v"], MAC, others=others)["ok"] is False
    assert ah.check(["f16"], MAC, others=[("Copy last", ["control", "f16"])])["ok"] is False
    assert ah.check(["control", "f16"], MAC, others=[("Copy last", ["f16"])])["ok"] is False
    assert ah.check(["control", "option", "y"], MAC, others=others)["ok"]


def test_a_shortcut_cannot_use_the_key_that_cancels():
    got = ah.check(["control", "option", "f16"], MAC, cancel_key="f16")
    assert got["ok"] is False and "F16 is your cancel key" in got["error"]
    assert ah.check(["f16"], MAC, cancel_key="f16")["ok"] is False
    assert ah.check(["control", "option", "f17"], MAC, cancel_key="f16")["ok"]
    assert ah.check(["control", "option", "t"], MAC, cancel_key="esc")["ok"], "Esc can never be a shortcut's key anyway"
    assert ah.check(["control", "option", "t"], MAC, cancel_key="")["ok"]


def test_every_refusal_is_one_sentence_without_dashes():
    cases = [(["t"], MAC), (["fn", "t"], MAC), (["control"], MAC), (["control", "option"], MAC),
             (["control", "option", "t", "y"], MAC), (["cmd", "space"], MAC), (["ctrl", "alt", "k"], WIN),
             (["f21"], WIN), (["cmd", "ctrl", "t"], WIN), (["win", "ctrl", "t"], MAC), ([], MAC), ("x", MAC)]
    for keys, platform in cases:
        got = ah.check(keys, platform)
        assert got["ok"] is False and got["error"].endswith("."), (keys, got)
        assert no_dashes(got["error"]) and "\n" not in got["error"], (keys, got)
    assert no_dashes(ah.check(["control", "option"], MAC, ["control"], hold=True)["error"])


# ── validate(): the one call the settings checks and the dialog make ─────────

def test_validate_checks_against_what_is_saved():
    stored = {"hotkey_keys": ["cmd", "shift"],
              "transforms": {"enabled": True, "hotkey": ["control", "option", "t"]},
              "paste_last_hotkey": ["control", "option", "v"],
              "cancel_key": "f16"}
    ok = ah.validate("copy_last", ["control", "option", "c"], MAC, stored)
    assert ok == {"ok": True, "keys": ["control", "option", "c"], "display": "Control + Option + C"}
    assert "dictation hotkey" in ah.validate("copy_last", ["cmd", "shift", "c"], MAC, stored)["error"]
    assert "(Transform)" in ah.validate("copy_last", ["control", "option", "t"], MAC, stored)["error"]
    assert "(Paste last)" in ah.validate("copy_last", ["control", "option", "v"], MAC, stored)["error"]
    assert "F16 is your cancel key" in ah.validate("copy_last", ["f16"], MAC, stored)["error"]
    # An action may keep, and re-save, its own combination.
    assert ah.validate("paste_last", ["control", "option", "v"], MAC, stored)["ok"]


def test_validate_treats_command_as_a_hold_and_the_rest_as_presses():
    assert ah.validate("command", ["control", "option"], MAC, {})["ok"] is True
    for action in ("transform", "paste_last", "copy_last"):
        assert ah.validate(action, ["control", "option"], MAC, {})["ok"] is False, action
        assert ah.validate(action, ["f16"], MAC, {})["ok"] is True, action
    assert ah.validate("command", ["f16"], MAC, {})["ok"] is False, "a hold has no bare key"


def test_validate_can_be_asked_about_a_dictation_hotkey_not_yet_saved():
    stored = {"hotkey_keys": ["fn"]}
    assert ah.validate("copy_last", ["control", "option", "c"], MAC, stored, dictation_hotkey=["control", "option"])["ok"] is False
    assert ah.validate("copy_last", ["control", "option", "c"], MAC, stored)["ok"] is True


# ── the cancel key ───────────────────────────────────────────────────────────

def test_the_cancel_key_is_esc_unless_an_f_key_is_chosen():
    assert ah.cancel_key_of({}) == "esc" and ah.cancel_key_of(None) == "esc"
    assert ah.cancel_key_of({"cancel_key": "F16"}) == "f16"
    for junk in ("a", "f21", "", 5, None, ["f16"], "esc ", "space", "fn"):
        expected = "esc"
        assert ah.cancel_key_of({"cancel_key": junk}) == expected, junk
    assert ah.cancel_key_of({"cancel_key": " f9 "}) == "f9"


@pytest.mark.parametrize("value,platform,stored,fragment", [
    ("a", MAC, {}, "Esc or an F key"),
    ("f21", MAC, {}, "Esc or an F key"),
    ("", MAC, {}, "one key"),
    (5, MAC, {}, "one key"),
    ("f13", MAC, {"hotkey_keys": ["fn"]}, "Fn key as F13"),
    ("f5", MAC, {"hotkey_keys": ["shift", "f5"]}, "part of your dictation hotkey"),
    ("f9", WIN, {"hotkey_keys": ["win", "f9"]}, "part of your dictation hotkey"),
    ("f16", MAC, {"paste_last_hotkey": ["f16"]}, "Paste last uses F16"),
    ("f17", MAC, {"transforms": {"hotkey": ["control", "option", "f17"]}}, "Transform uses F17"),
])
def test_a_cancel_key_that_would_clash_is_refused_in_a_sentence(value, platform, stored, fragment):
    got = ah.cancel_problem(value, platform, stored)
    assert got and fragment in got and got.endswith(".") and no_dashes(got), got


def test_a_cancel_key_that_is_fine_is_none():
    assert ah.cancel_problem("esc", MAC, {}) is None
    assert ah.cancel_problem("Escape", MAC, {}) is None
    assert ah.cancel_problem("f16", MAC, {"hotkey_keys": ["fn"]}) is None
    assert ah.cancel_problem("f13", MAC, {"hotkey_keys": ["cmd", "shift"]}) is None, "free once the hotkey is not Fn"
    assert ah.cancel_problem("f13", WIN, {}) is None
    assert ah.cancel_problem("f9", WIN, {"hotkey_keys": ["win", "ctrl"]}) is None


def test_a_new_dictation_hotkey_may_not_include_the_chosen_cancel_key():
    msg = ah.dictation_clash_message(["shift", "f16"], [], MAC, cancel_key="f16")
    assert "F16" in msg and "cancels a recording" in msg and msg.endswith(".") and no_dashes(msg)
    assert ah.dictation_clash_message(["shift", "f16"], [], MAC, cancel_key="esc") == "", "Esc was never checked"
    assert ah.dictation_clash_message(["shift", "f16"], [], MAC) == ""
    assert ah.dictation_clash_message(["cmd", "shift"], [], MAC, cancel_key="f16") == ""


def test_the_registry_refuses_the_cancel_key_it_was_told():
    r, _, _ = registry()
    r.set_cancel_key("f16")
    assert r.register("copy_last", ["f16"], lambda: None)["ok"] is False
    assert r.conflict("copy_last", ["f16"]) is not None
    r.set_cancel_key("esc")
    assert r.register("copy_last", ["f16"], lambda: None)["ok"] is True
    r.set_cancel_key("nonsense")
    assert r._cancel_key == "esc"


# ── register_on_release (Paste last) ─────────────────────────────────────────

def test_paste_last_starts_when_the_keys_come_up_not_when_they_go_down():
    r, clock, _ = registry()
    seen = []
    assert r.register_on_release("paste_last", ["control", "option", "v"], lambda: seen.append("paste"))["ok"]
    assert r.key_down("v", ["control", "option"]) == ["paste_last"]
    assert seen == [], "the modifiers are still down: it waits"
    clock.advance(0.2)
    r.key_up("v")
    assert seen == ["paste"]
    r.key_up("v")
    assert seen == ["paste"], "once"


def test_paste_last_starts_once_when_a_modifier_comes_up_first():
    r, _, _ = registry()
    seen = []
    r.register_on_release("paste_last", ["control", "option", "v"], lambda: seen.append("paste"))
    r.key_down("v", ["control", "option"])
    r.modifiers_changed(["option"])
    assert seen == ["paste"]
    r.key_up("v")
    assert seen == ["paste"]


def test_paste_last_as_a_function_key_alone_starts_on_its_key_up():
    r, _, _ = registry()
    seen = []
    assert r.register_on_release("paste_last", ["f16"], lambda: seen.append(1))["ok"]
    assert r.key_down("f16", []) == ["paste_last"] and seen == []
    r.modifiers_changed(["shift"])                       # an unrelated modifier: nothing
    assert seen == []
    r.key_up("f16")
    assert seen == [1]


def test_register_on_release_is_checked_as_a_press():
    r, _, _ = registry()
    assert r.register_on_release("paste_last", ["t"], lambda: None)["ok"] is False
    assert r.register_on_release("paste_last", ["control", "option"], lambda: None)["ok"] is False
    assert r.registered() == {}


# ── the swallow table ────────────────────────────────────────────────────────

def armed(keys=("control", "option", "k"), platform=MAC, action="transform"):
    r, clock, timers = registry(platform)
    assert r.register(action, list(keys), lambda: None)["ok"]
    return r, clock


def test_the_key_down_of_a_registered_combination_is_swallowed_with_its_repeats_and_its_key_up():
    r, clock = armed()
    mods = ["control", "option"]
    assert r.swallow_down("k", mods) is True
    for _ in range(50):
        clock.advance(0.03)
        assert r.swallow_down("k", mods, repeat=True) is True
    assert r.swallow_up("k") is True
    assert r.swallow_up("k") is False, "the memory is spent"


def test_only_the_exact_modifiers_are_swallowed():
    r, _ = armed()
    for mods in ([], ["control"], ["option"], ["control", "option", "shift"], ["cmd"], ["control", "shift"]):
        assert r.swallow_down("k", mods) is False, mods
        assert r.swallow_up("k") is False, mods
    assert r.swallow_down("j", ["control", "option"]) is False, "another key with the right modifiers"
    assert r.swallow_up("j") is False


def test_a_key_pressed_without_the_modifiers_is_never_swallowed_so_typing_works():
    r, clock = armed()
    for _ in range(20):
        assert r.swallow_down("k", []) is False
        assert r.swallow_up("k") is False
        clock.advance(0.1)


def test_the_modifier_keys_themselves_are_never_swallowed():
    r, _ = armed()
    obs = ah.MacComboObserver(r, get_flags=lambda e: e[1], get_field=lambda e, f: e[0] if f == ah._FIELD_KEYCODE else 0)
    for flags in (0, 0x40000, 0x40000 | 0x80000, 0x40000 | 0x80000 | 0x20000, 0x100000):
        assert obs.handle(ah._FLAGS_CHANGED, (0, flags)) is False


def test_a_modifier_let_go_first_keeps_swallowing_the_key_so_no_letter_is_typed():
    r, clock = armed()
    assert r.swallow_down("k", ["control", "option"]) is True
    clock.advance(0.6)
    assert r.swallow_down("k", ["option"], repeat=True) is True, "Control came up; K is still held"
    assert r.swallow_down("k", [], repeat=True) is True, "all modifiers up; K is still held"
    assert r.swallow_up("k") is True


def test_a_modifier_let_go_before_the_key_up_still_swallows_the_key_up():
    r, _ = armed()
    r.swallow_down("k", ["control", "option"])
    r.modifiers_changed([])
    assert r.swallow_up("k") is True


def test_rapid_presses_are_each_swallowed_down_and_up():
    r, clock = armed()
    for _ in range(30):
        assert r.swallow_down("k", ["control", "option"]) is True
        clock.advance(0.02)
        assert r.swallow_up("k") is True
        clock.advance(0.02)


def test_two_shortcuts_may_share_a_main_key_each_swallows_only_its_own_modifiers():
    r, _, _ = registry()
    assert r.register("transform", ["control", "option", "k"], lambda: None)["ok"]
    assert r.register("copy_last", ["control", "shift", "k"], lambda: None)["ok"]
    assert r.swallow_down("k", ["control", "option"]) is True
    assert r.swallow_up("k") is True
    assert r.swallow_down("k", ["control", "shift"]) is True
    assert r.swallow_up("k") is True
    assert r.swallow_down("k", ["control"]) is False
    assert r.swallow_down("k", ["control", "option", "shift"]) is False


def test_a_key_up_that_never_comes_does_not_leave_the_key_swallowed():
    r, clock = armed()
    r.swallow_down("k", ["control", "option"])
    clock.advance(ah.SWALLOW_STALE_S + 0.1)
    assert r.swallow_up("k") is False, "forgotten: the late key-up goes through"
    r.swallow_down("k", ["control", "option"])
    clock.advance(ah.SWALLOW_STALE_S + 0.1)
    assert r.swallow_down("k", [], repeat=True) is False, "a repeat after the memory expired is an ordinary key"
    assert r.is_swallowing("k") is False


def test_a_fresh_press_after_a_lost_key_up_starts_clean():
    r, clock = armed()
    r.swallow_down("k", ["control", "option"])                  # ...and its key-up is lost
    clock.advance(1.0)
    assert r.swallow_down("k", []) is False, "typing a plain k: not a repeat, so not swallowed"
    assert r.swallow_up("k") is False
    # The same combination again is swallowed as the new press it is.
    assert r.swallow_down("k", ["control", "option"]) is True
    assert r.swallow_up("k") is True


def test_a_modifier_event_cleans_up_a_key_whose_key_up_was_lost():
    r, clock = armed()
    r.swallow_down("k", ["control", "option"])
    clock.advance(ah.SWALLOW_STALE_S + 1)
    r.modifiers_changed([])                                     # the person lets go of the modifiers
    assert "k" not in r._swallowed


def test_a_key_held_for_a_long_time_with_repeats_stays_swallowed():
    r, clock = armed()
    r.swallow_down("k", ["control", "option"])
    for _ in range(300):                                        # nine seconds of repeats
        clock.advance(0.03)
        assert r.swallow_down("k", ["control", "option"], repeat=True) is True
    assert r.swallow_up("k") is True


def test_a_disabled_action_swallows_nothing():
    r, _ = armed()
    r.unregister("transform")
    assert r.swallow_down("k", ["control", "option"]) is False
    assert r.swallow_up("k") is False
    empty, _, _ = registry()
    assert empty.swallow_down("k", ["control", "option"]) is False


def test_unregistering_while_a_key_is_held_still_lets_its_key_up_be_swallowed_once():
    r, _ = armed()
    r.swallow_down("k", ["control", "option"])
    r.unregister("transform")
    assert r.swallow_up("k") is True, "the app never saw the key go down: it must not see it come up"
    assert r.swallow_down("k", ["control", "option"]) is False


def test_nothing_is_swallowed_while_the_dictation_hotkey_is_held():
    r, _ = armed()
    held = {"now": True}
    r.set_ptt_probe(lambda: held["now"])
    assert r.swallow_down("k", ["control", "option"]) is False
    held["now"] = False
    assert r.swallow_down("k", ["control", "option"]) is True
    held["now"] = True
    assert r.swallow_down("k", ["control", "option"], repeat=True) is False


def test_a_probe_that_fails_or_is_missing_does_not_stop_swallowing():
    r, _ = armed()
    r.set_ptt_probe(lambda: (_ for _ in ()).throw(RuntimeError("gone")))
    assert r.swallow_down("k", ["control", "option"]) is True
    r.set_ptt_probe(None)
    r.swallow_up("k")
    assert r.swallow_down("k", ["control", "option"]) is True


def test_attaching_a_listener_teaches_the_registry_to_ask_it_whether_the_hotkey_is_held():
    r, _ = armed()

    class Listener:
        is_combo_active = True

        def attach_observer(self, handler):
            pass
    listener = Listener()
    assert r.attach_listener(listener) is True
    assert r.swallow_down("k", ["control", "option"]) is False
    listener.is_combo_active = False
    assert r.swallow_down("k", ["control", "option"]) is True
    # Windows has no tap to join, but learns the same.
    w, _ = armed(("ctrl", "shift", "k"), WIN)
    other = Listener()
    other.is_combo_active = True
    assert w.attach_listener(other) is False
    assert w.swallow_down("k", ["ctrl", "shift"]) is False


def test_nothing_is_swallowed_or_fired_while_the_key_recording_dialog_is_open():
    r, clock, _ = registry()
    fired = []
    r.register("transform", ["control", "option", "k"], lambda: fired.append(1))
    r.set_recording(True)
    assert r.swallow_down("k", ["control", "option"]) is False
    assert r.key_down("k", ["control", "option"]) == [] and fired == []
    r.key_up("k")
    r.set_recording(False)
    clock.advance(1)
    assert r.key_down("k", ["control", "option"]) == ["transform"] and fired == [1]
    assert r.swallow_down("k", ["control", "option"]) is True


def test_opening_the_dialog_forgets_a_key_that_was_being_swallowed():
    r, _ = armed()
    r.swallow_down("k", ["control", "option"])
    r.set_recording(True)
    assert r.swallow_down("k", ["control", "option"], repeat=True) is False
    assert r.swallow_up("k") is False


def test_a_dialog_that_never_closes_does_not_disable_shortcuts_for_ever():
    r, clock = armed()
    r.set_recording(True)
    assert r.swallow_down("k", ["control", "option"]) is False
    clock.advance(ah.RECORDING_MAX_S + 1)
    assert r.swallow_down("k", ["control", "option"]) is True


def test_a_new_tap_forgets_what_the_old_one_was_told():
    r, _ = armed()

    class Listener:
        is_combo_active = False
    r.swallow_down("k", ["control", "option"])
    r.attach_listener(Listener())
    assert r.is_swallowing("k") is False


# ── the Mac observer's answer ────────────────────────────────────────────────

class Ev:
    def __init__(self, keycode, flags=0, repeat=0, pid=0):
        self.fields = {ah._FIELD_KEYCODE: keycode, ah._FIELD_AUTOREPEAT: repeat, ah._FIELD_SOURCE_PID: pid}
        self.flags = flags


def observer(r, own_pid=4242):
    return ah.MacComboObserver(r, get_flags=lambda e: e.flags, get_field=lambda e, f: e.fields[f], own_pid=own_pid)


CTRL, OPT, SHIFT = 0x40000, 0x80000, 0x20000
K = ah.MAC_KEYCODES["k"]


def test_the_observer_swallows_the_whole_press_of_a_registered_combination():
    r, clock = armed()
    obs = observer(r)
    assert obs.handle(ah._KEY_DOWN, Ev(K, CTRL | OPT)) is True
    assert obs.handle(ah._KEY_DOWN, Ev(K, CTRL | OPT, repeat=1)) is True
    assert obs.handle(ah._FLAGS_CHANGED, Ev(0, OPT)) is False
    assert obs.handle(ah._KEY_DOWN, Ev(K, OPT, repeat=1)) is True
    assert obs.handle(ah._KEY_UP, Ev(K, OPT)) is True
    assert obs.handle(ah._KEY_UP, Ev(K, 0)) is False


def test_the_observer_lets_every_other_key_through():
    r, _ = armed()
    obs = observer(r)
    assert obs.handle(ah._KEY_DOWN, Ev(K, 0)) is False                 # a plain k
    assert obs.handle(ah._KEY_DOWN, Ev(K, CTRL)) is False
    assert obs.handle(ah._KEY_DOWN, Ev(ah.MAC_KEYCODES["j"], CTRL | OPT)) is False
    assert obs.handle(ah._KEY_DOWN, Ev(999, CTRL | OPT)) is False       # a key with no name here
    assert obs.handle(ah._KEY_UP, Ev(999, CTRL | OPT)) is False
    assert obs.handle(7, Ev(K, CTRL | OPT)) is False                     # not a key event


def test_the_observer_never_swallows_what_waffler_sent_itself():
    r, _ = armed()
    obs = observer(r, own_pid=4242)
    assert obs.handle(ah._KEY_DOWN, Ev(K, CTRL | OPT, pid=4242)) is False
    assert obs.handle(ah._KEY_UP, Ev(K, CTRL | OPT, pid=4242)) is False
    assert obs.handle(ah._KEY_DOWN, Ev(K, CTRL | OPT, pid=77)) is True, "another program's is the person's"


def test_the_observer_says_do_not_swallow_when_anything_goes_wrong():
    class Broken:
        def key_down(self, *a, **k):
            raise RuntimeError("x")
        swallow_down = swallow_up = key_up = modifiers_changed = other_key_down = key_down
    obs = ah.MacComboObserver(Broken(), get_flags=lambda e: 0, get_field=lambda e, f: ah.MAC_KEYCODES["k"])
    for kind in (ah._KEY_DOWN, ah._KEY_UP, ah._FLAGS_CHANGED):
        assert obs.handle(kind, object()) is False


def test_the_observer_does_not_swallow_a_function_key_pressed_alone_unless_it_is_registered():
    r, _, _ = registry()
    obs = observer(r)
    f16 = ah.MAC_KEYCODES["f16"]
    assert obs.handle(ah._KEY_DOWN, Ev(f16, 0)) is False
    r.register("copy_last", ["f16"], lambda: None)
    assert obs.handle(ah._KEY_DOWN, Ev(f16, 0x800000)) is True, "the Fn bit of a laptop's F keys is no modifier"
    assert obs.handle(ah._KEY_UP, Ev(f16, 0)) is True


# ── Windows ──────────────────────────────────────────────────────────────────

def test_the_windows_hook_swallows_the_main_key_of_a_registered_combination_only():
    r, clock, _ = registry(WIN)
    r.register("transform", ["ctrl", "shift", "y"], lambda: None)
    vk = ah.WINDOWS_VKS["y"]
    held = {0x11, 0x10}                                                       # Ctrl and Shift
    state = lambda v: v in held  # noqa: E731
    assert ah.windows_event(r, vk, ah._WM_KEYDOWN, 0, state) is True
    assert ah.windows_event(r, vk, ah._WM_KEYDOWN, 0, state) is True           # the repeat (no flag on Windows)
    held.discard(0x10)                                                         # Shift let go first
    assert ah.windows_event(r, vk, ah._WM_KEYDOWN, 0, state) is True, "still held: the letter is not typed"
    assert ah.windows_event(r, vk, ah._WM_KEYUP, 0, state) is True
    assert ah.windows_event(r, vk, ah._WM_KEYUP, 0, state) is False
    clock.advance(1)
    assert ah.windows_event(r, vk, ah._WM_KEYDOWN, 0, lambda v: v == 0x11) is False      # Ctrl alone: a plain Ctrl+Y
    assert ah.windows_event(r, vk, ah._WM_KEYUP, 0, state) is False


def test_the_windows_hook_never_swallows_modifiers_or_keys_a_program_sent():
    r, _, _ = registry(WIN)
    r.register("transform", ["ctrl", "shift", "y"], lambda: None)
    for vk in (0x10, 0x11, 0x12, 0x5B, 0xA0, 0xA3):
        for message in (ah._WM_KEYDOWN, ah._WM_KEYUP, ah._WM_SYSKEYDOWN):
            assert ah.windows_event(r, vk, message, 0, lambda v: True) is False, (vk, message)
    assert ah.windows_event(r, ah.WINDOWS_VKS["y"], ah._WM_KEYDOWN, ah._LLKHF_INJECTED,
                            lambda v: v in (0x11, 0x10)) is False
    assert ah.windows_event(r, 0x20, ah._WM_KEYDOWN, 0, lambda v: True) is False        # Space: no name here


# ── a hold of modifier keys alone ────────────────────────────────────────────

def modifier_hold():
    r, clock, timers = registry(MAC)
    events = []
    assert r.register_hold("command", ["control", "option"], lambda: events.append("down"),
                           lambda: events.append("up"))["ok"]
    return r, clock, timers, events


def test_modifier_keys_held_alone_start_the_hold_after_a_moment_and_end_it_when_one_comes_up():
    r, clock, timers, events = modifier_hold()
    r.modifiers_changed(["control"])
    r.modifiers_changed(["control", "option"])
    assert events == [] and [d for d, _ in timers.pending] == [ah.MODIFIER_HOLD_S]
    timers.fire()
    assert events == ["down"]
    clock.advance(4)
    r.modifiers_changed(["control", "option", "shift"])         # an extra key joins: still held
    assert events == ["down"]
    r.modifiers_changed(["option"])                             # Control let go
    assert events == ["down", "up"]
    r.modifiers_changed([])
    assert events == ["down", "up"]


def test_modifier_keys_on_the_way_to_another_shortcut_start_nothing():
    r, clock, timers, events = modifier_hold()
    r.modifiers_changed(["control", "option"])
    r.key_down("t", ["control", "option"])                      # a key before the moment is up
    timers.fire()
    assert events == []
    r.modifiers_changed([])
    # A key no shortcut can use (Space, an arrow) counts the same.
    r.modifiers_changed(["control", "option"])
    r.other_key_down()
    timers.fire()
    assert events == []


def test_a_different_set_of_modifier_keys_does_not_start_the_hold():
    r, _, timers, events = modifier_hold()
    r.modifiers_changed(["control", "option", "shift"])
    r.modifiers_changed(["control"])
    timers.fire()
    assert events == []
    # Held, then one more joins before the moment is up: not exactly it any more.
    r.modifiers_changed(["control", "option"])
    r.modifiers_changed(["control", "option", "shift"])
    timers.fire()
    assert events == []


def test_letting_go_before_the_moment_is_up_starts_nothing():
    r, _, timers, events = modifier_hold()
    r.modifiers_changed(["control", "option"])
    r.modifiers_changed([])
    timers.fire()
    assert events == []


def test_the_same_modifier_event_twice_is_one_start():
    r, _, timers, events = modifier_hold()
    r.modifiers_changed(["control", "option"])
    r.modifiers_changed(["control", "option"])
    assert len(timers.pending) == 1
    timers.fire()
    r.modifiers_changed(["control", "option"])
    timers.fire()
    assert events == ["down"], "and none while it is already held"


def test_the_hold_can_start_again_after_it_ended():
    r, _, timers, events = modifier_hold()
    for _ in range(2):
        r.modifiers_changed(["control", "option"])
        timers.fire()
        r.modifiers_changed([])
    assert events == ["down", "up", "down", "up"]


def test_a_hold_of_modifier_keys_alone_swallows_nothing_and_fires_on_no_key():
    r, _, timers, events = modifier_hold()
    assert r.key_down("t", ["control", "option"]) == []
    assert r.swallow_down("t", ["control", "option"]) is False
    assert events == []


def test_unregistering_or_opening_the_dialog_cancels_a_hold_waiting_to_start():
    r, _, timers, events = modifier_hold()
    r.modifiers_changed(["control", "option"])
    r.unregister("command")
    timers.fire()
    assert events == []
    r2, _, timers2, events2 = modifier_hold()
    r2.set_recording(True)
    r2.modifiers_changed(["control", "option"])
    timers2.fire()
    assert events2 == [], "choosing a shortcut does not run one"
    r3, _, timers3, events3 = modifier_hold()
    r3.modifiers_changed(["control", "option"])
    r3.set_recording(True)
    timers3.fire()
    assert events3 == []


def test_a_hold_the_system_never_ended_is_ended_by_the_next_modifier_event():
    r, clock, timers, events = modifier_hold()
    r.modifiers_changed(["control", "option"])
    timers.fire()
    clock.advance(ah.HOLD_STALE_S + 1)
    r.modifiers_changed(["control", "option"])
    assert events == ["down", "up"]


def test_a_hold_that_is_modifier_keys_alone_runs_down_and_up_in_order_on_the_actions_thread():
    """The same serial runner as a hold with a letter: an up never overtakes its down."""
    r, _, timers, events = modifier_hold()
    r.modifiers_changed(["control", "option"])
    timers.fire()
    r.modifiers_changed([])
    assert events == ["down", "up"]


# ── from the independent review ──────────────────────────────────────────────

def test_a_name_that_is_several_letters_or_digits_is_not_a_key():
    for platform in (MAC, WIN):
        for keys in (["control", "shift", "abc"], ["control", "shift", "12"], ["control", "shift", "tt"]):
            got = ah.check(keys if platform == MAC else [k.replace("control", "ctrl") for k in keys], platform)
            assert got["ok"] is False, (platform, keys)
    assert ah.check(["ctrl", "shift", "y"], WIN)["ok"]


def test_a_repeat_of_a_press_that_went_through_is_never_started_and_its_key_up_goes_through():
    """Shift + V held (the V went to the app), then Control is added: the repeats
    now match Control + Shift + V, but the app has seen this V go down, so it
    must see it come up."""
    r, clock, _ = registry()
    r.register("transform", ["control", "shift", "v"], lambda: None)
    assert r.swallow_down("v", ["shift"]) is False
    clock.advance(0.5)
    assert r.swallow_down("v", ["control", "shift"], repeat=True) is False
    assert r.swallow_up("v") is False
    # A fresh press of the exact combination afterwards is swallowed as ever.
    assert r.swallow_down("v", ["control", "shift"]) is True and r.swallow_up("v") is True


def test_windows_knows_a_key_is_a_repeat_by_a_key_down_it_has_seen():
    r, clock, _ = registry(WIN)
    r.register("transform", ["ctrl", "shift", "y"], lambda: None)
    vk = ah.WINDOWS_VKS["y"]
    shift_only = lambda v: v == 0x10  # noqa: E731
    both = lambda v: v in (0x10, 0x11)  # noqa: E731
    assert ah.windows_event(r, vk, ah._WM_KEYDOWN, 0, shift_only) is False
    clock.advance(0.5)
    assert ah.windows_event(r, vk, ah._WM_KEYDOWN, 0, both) is False, "a repeat of a key the app has seen go down"
    assert ah.windows_event(r, vk, ah._WM_KEYUP, 0, both) is False
    clock.advance(1.0)
    assert ah.windows_event(r, vk, ah._WM_KEYDOWN, 0, both) is True, "and a new press is swallowed"
    assert ah.windows_event(r, vk, ah._WM_KEYUP, 0, both) is True


def test_is_held_follows_the_key_down_and_the_key_up_and_forgets_a_lost_key_up():
    r, clock, _ = registry()
    r.register("transform", ["control", "option", "k"], lambda: None)
    assert r.is_held("k") is False
    r.key_down("k", ["control", "option"])
    assert r.is_held("k") is True
    r.key_up("k")
    assert r.is_held("k") is False
    r.key_down("k", ["control", "option"])
    clock.advance(ah.HELD_FOR_S + 0.1)
    assert r.is_held("k") is False


def test_the_windows_hook_taps_a_masking_key_when_it_swallows_a_key_with_the_windows_key_or_alt_held():
    r, clock, _ = registry(WIN)
    r.register("transform", ["win", "ctrl", "y"], lambda: None)
    r.register("copy_last", ["alt", "f5"], lambda: None)
    r.register("paste_last", ["ctrl", "shift", "u"], lambda: None)
    masks = []
    held = set()
    state = lambda v: v in held  # noqa: E731
    y, f5, u = ah.WINDOWS_VKS["y"], ah.WINDOWS_VKS["f5"], ah.WINDOWS_VKS["u"]
    held.update({0x5B, 0x11})
    assert ah.windows_event(r, y, ah._WM_KEYDOWN, 0, state, lambda: masks.append("win")) is True
    assert masks == ["win"]
    ah.windows_event(r, y, ah._WM_KEYDOWN, 0, state, lambda: masks.append("win"))           # the repeat
    ah.windows_event(r, y, ah._WM_KEYUP, 0, state, lambda: masks.append("win"))
    assert masks == ["win"], "once per press, not per repeat or key-up"
    clock.advance(1)
    held.clear()
    held.add(0x12)
    assert ah.windows_event(r, f5, ah._WM_SYSKEYDOWN, 0, state, lambda: masks.append("alt")) is True
    assert masks == ["win", "alt"]
    ah.windows_event(r, f5, ah._WM_SYSKEYUP, 0, state)
    clock.advance(1)
    held.clear()
    held.update({0x11, 0x10})
    assert ah.windows_event(r, u, ah._WM_KEYDOWN, 0, state, lambda: masks.append("never")) is True
    assert masks == ["win", "alt"], "Ctrl and Shift open nothing, so nothing is sent"
    # A key that is not swallowed sends nothing, and a mask that fails changes nothing.
    held.clear()
    held.add(0x5B)
    assert ah.windows_event(r, ah.WINDOWS_VKS["q"], ah._WM_KEYDOWN, 0, state, lambda: masks.append("never")) is False
    held.update({0x5B, 0x11})
    clock.advance(1)

    def broken():
        raise RuntimeError("no input")
    assert ah.windows_event(r, y, ah._WM_KEYDOWN, 0, state, broken) is True


def test_a_hold_that_starts_when_the_keys_come_up_is_dropped_not_run_if_it_goes_stale():
    r, clock, _ = registry()
    seen = []
    r.register_on_release("paste_last", ["control", "option", "v"], lambda: seen.append("paste"))
    r.key_down("v", ["control", "option"])
    clock.advance(ah.HOLD_STALE_S + 10)                         # the key-up never came
    r.key_down("x", ["control"])                                # any key at all, a long time later
    assert seen == [], "a paste must not happen two minutes late"
    r.key_up("v")
    assert seen == []
    # ...and the next press is a press.
    clock.advance(1)
    r.key_down("v", ["control", "option"])
    r.key_up("v")
    assert seen == ["paste"]


def test_a_stale_modifier_event_drops_it_too_and_a_real_hold_still_ends():
    r, clock, _ = registry()
    seen = []
    r.register_on_release("paste_last", ["control", "option", "v"], lambda: seen.append("paste"))
    r.register_hold("command", ["control", "shift", "c"], lambda: seen.append("down"), lambda: seen.append("up"))
    r.key_down("v", ["control", "option"])
    r.key_down("c", ["control", "shift"])
    clock.advance(ah.HOLD_STALE_S + 10)
    r.modifiers_changed(["control", "option", "shift"])
    assert seen == ["down", "up"], "Command mode's recording must still stop; Paste last does not run"


def test_removing_or_replacing_a_press_that_starts_on_release_while_it_is_held_runs_nothing():
    for how in ("unregister", "register"):
        r, _, _ = registry()
        seen = []
        r.register_on_release("paste_last", ["control", "option", "v"], lambda: seen.append("paste"))
        r.key_down("v", ["control", "option"])
        if how == "unregister":
            r.unregister("paste_last")
        else:
            r.register_on_release("paste_last", ["control", "option", "y"], lambda: seen.append("other"))
        r.key_up("v")
        assert seen == [], how
    # A hold of Command mode is still ended when it is removed.
    r, _, _ = registry()
    seen = []
    r.register_hold("command", ["control", "option", "c"], lambda: seen.append("down"), lambda: seen.append("up"))
    r.key_down("c", ["control", "option"])
    r.unregister("command")
    assert seen == ["down", "up"]


def test_a_dictation_hotkey_of_fn_refuses_f1_to_f15_for_a_shortcut_on_a_mac():
    for n in range(1, 16):
        for keys in ([f"f{n}"], ["control", "option", f"f{n}"]):
            got = ah.check(keys, MAC, ["fn"])
            assert got["ok"] is False and "Fn" in got["error"] and "F16 to F20" in got["error"], (n, keys)
    for n in range(16, 21):
        assert ah.check([f"f{n}"], MAC, ["fn"])["ok"] is True
    assert ah.check(["f5"], MAC, ["cmd", "shift"])["ok"] is True, "only while the hotkey is Fn"
    assert ah.check(["f5"], WIN, ["win", "ctrl"])["ok"] is True
    assert ah.check(["f5"], MAC)["ok"] is True, "no hotkey given: nothing to clash with"
    assert ah.validate("copy_last", ["f5"], MAC, {})["ok"] is False, "Fn is the default"
    assert ah.validate("copy_last", ["f5"], MAC, {"hotkey_keys": ["cmd", "shift"]})["ok"] is True


def test_changing_the_dictation_hotkey_to_fn_is_refused_while_a_shortcut_or_the_cancel_key_is_in_the_way():
    msg = ah.dictation_clash_message(["fn"], [["f5"]], MAC)
    assert "F5" in msg and msg.endswith(".") and no_dashes(msg)
    assert ah.dictation_clash_message(["fn"], [["control", "option", "f14"]], MAC)
    assert ah.dictation_clash_message(["fn"], [["control", "option", "t"], ["f16"]], MAC) == ""
    assert ah.dictation_clash_message(["fn"], [], MAC, cancel_key="f15").startswith("Your cancel key F15")
    assert ah.dictation_clash_message(["fn"], [], MAC, cancel_key="f5") == "", "Fn is held for F5 already"
    assert ah.dictation_clash_message(["cmd", "shift"], [["f5"]], MAC) == ""
    assert ah.dictation_clash_message(["win", "ctrl"], [["f5"]], WIN) == ""


def test_the_reserved_function_key_combinations_are_refused_with_who_uses_them():
    for keys, platform, fragment in ((["alt", "f4"], WIN, "close the window"), (["ctrl", "f4"], WIN, "close the tab"),
                                     (["shift", "f10"], WIN, "context menu"), (["control", "f2"], MAC, "menu bar"),
                                     (["cmd", "f3"], MAC, "show the desktop"), (["cmd", "f5"], MAC, "VoiceOver")):
        got = ah.check(keys, platform)
        assert got["ok"] is False and fragment in got["error"] and "already use" in got["error"], keys
    assert ah.check(["alt", "f5"], WIN)["ok"] and ah.check(["control", "f9"], MAC)["ok"]


def test_waiting_for_modifier_keys_to_come_up():
    state = {"down": 3, "slept": 0, "now": 0.0}

    def is_down():
        state["down"] -= 1
        return state["down"] > 0

    def sleep(s):
        state["slept"] += 1
        state["now"] += s
    assert ah.wait_for_modifiers_up(is_down, 1.0, sleep=sleep, clock=lambda: state["now"]) is True
    assert state["slept"] == 2
    assert ah.wait_for_modifiers_up(lambda: True, 0.05, sleep=sleep, clock=lambda: state["now"]) is False
    assert ah.wait_for_modifiers_up(lambda: (_ for _ in ()).throw(RuntimeError("x")), 1.0) is True
    if sys.platform != "win32":
        assert ah.windows_modifiers_down() is False
