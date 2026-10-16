"""The Shortcuts page's data, the settings it writes, and the bridge (S11a).

``src/shortcut_settings.py`` is pure and runs here directly. app.py's Api
methods are lifted out of its source and run against a temporary settings.json
(tests/_settings_fake.py): no window, no keys, no keyboard.
"""
import ast
import json
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import action_hotkeys as ah  # noqa: E402
import command_mode as cm  # noqa: E402
import shortcut_settings as ss  # noqa: E402
import transforms as tf  # noqa: E402
import user_messages as um  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

MAC, WIN = ah.MAC, ah.WINDOWS
_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


@pytest.fixture(autouse=True)
def _as_a_mac(monkeypatch):
    """The settings checks ask which computer this is; these tests use Mac key
    names, so they run as a Mac on every runner."""
    monkeypatch.setattr(ss, "_this_platform", lambda: MAC)
    monkeypatch.setattr(tf, "_this_platform", lambda: MAC)


def no_dashes(text):
    return "—" not in text and "–" not in text


# ── the settings ─────────────────────────────────────────────────────────────

def test_the_defaults_are_no_shortcut_and_esc():
    assert ss.settings({}) == {"paste_last_hotkey": [], "copy_last_hotkey": [], "cancel_key": "esc"}
    assert ss.settings(None) == ss.settings({})
    view = ss.window_view({}, MAC)
    assert view["paste_last_display"] == "" and view["copy_last_display"] == "" and view["cancel_display"] == "Esc"


@pytest.mark.parametrize("junk", [None, 5, "x", [], {"paste_last_hotkey": "cmd+v", "copy_last_hotkey": [1],
                                                    "cancel_key": ["f9"]}])
def test_a_hand_edited_file_reads_as_the_defaults(junk):
    assert ss.settings(junk if isinstance(junk, dict) else {}) == ss.settings({})
    assert ss.settings(junk) == ss.settings({})


def test_the_shortcuts_are_shown_as_the_platform_names_them():
    stored = {"paste_last_hotkey": ["control", "option", "v"], "copy_last_hotkey": ["f16"], "cancel_key": "f9"}
    v = ss.window_view(stored, MAC)
    assert (v["paste_last_display"], v["copy_last_display"], v["cancel_display"]) == (
        "Control + Option + V", "F16", "F9")
    assert ss.window_view({"paste_last_hotkey": ["ctrl", "shift", "y"]}, WIN)["paste_last_display"] == "Ctrl + Shift + Y"


def test_touches_names_only_the_three_settings():
    assert ss.touches({"cancel_key": "f9"}) and ss.touches({"paste_last_hotkey": []})
    assert not ss.touches({"transforms": {}}) and not ss.touches({}) and not ss.touches(None)


# ── checking a save ──────────────────────────────────────────────────────────

def test_a_good_payload_has_no_problem():
    assert ss.settings_problem({"paste_last_hotkey": ["control", "option", "v"]}, {}) is None
    assert ss.settings_problem({"copy_last_hotkey": ["f16"]}, {}) is None
    assert ss.settings_problem({"cancel_key": "f16"}, {}) is None
    assert ss.settings_problem({"paste_last_hotkey": [], "copy_last_hotkey": [], "cancel_key": "esc"}, {}) is None
    assert ss.settings_problem({"auto_paste": False}, {}) is None, "other settings are not this module's"


@pytest.mark.parametrize("payload", [
    {"paste_last_hotkey": "cmd+v"}, {"copy_last_hotkey": None}, {"paste_last_hotkey": {"a": 1}},
    {"cancel_key": ["f9"]}, {"cancel_key": 5}, {"cancel_key": None},
])
def test_a_payload_of_the_wrong_shape_is_one_plain_sentence(payload):
    assert ss.settings_problem(payload, {}) == um.SHORTCUT_BAD
    assert um.SHORTCUT_BAD.endswith(".") and no_dashes(um.SHORTCUT_BAD)


def test_a_refused_shortcut_says_why_in_the_validators_words():
    assert "Control or Command" in ss.settings_problem({"paste_last_hotkey": ["t"]}, {})
    assert "Add a letter" in ss.settings_problem({"copy_last_hotkey": ["control", "option"]}, {})
    assert "already uses" in ss.settings_problem({"copy_last_hotkey": ["control", "cmd", "q"]}, {})
    assert "dictation hotkey" in ss.settings_problem(
        {"copy_last_hotkey": ["control", "option", "c"]}, {"hotkey_keys": ["control", "option"]})
    assert "(Transform)" in ss.settings_problem(
        {"copy_last_hotkey": ["control", "option", "t"]}, {"transforms": {"hotkey": ["control", "option", "t"]}})
    assert "(Command mode)" in ss.settings_problem(
        {"copy_last_hotkey": ["control", "option", "t"]}, {"command_mode": {"hotkey": ["control", "option", "t"]}})
    assert "(Paste last)" in ss.settings_problem(
        {"copy_last_hotkey": ["control", "option", "v"]}, {"paste_last_hotkey": ["control", "option", "v"]})


def test_two_shortcuts_saved_together_cannot_share_a_combination():
    both = {"paste_last_hotkey": ["control", "option", "v"], "copy_last_hotkey": ["option", "control", "v"]}
    assert "Another shortcut" in ss.settings_problem(both, {})
    inside = {"paste_last_hotkey": ["control", "option", "v"], "copy_last_hotkey": ["control", "option", "shift", "v"]}
    assert "overlaps another shortcut" in ss.settings_problem(inside, {})


def test_two_shortcuts_may_swap_combinations_in_one_save():
    stored = {"paste_last_hotkey": ["control", "option", "v"], "copy_last_hotkey": ["control", "option", "c"]}
    swap = {"paste_last_hotkey": ["control", "option", "c"], "copy_last_hotkey": ["control", "option", "v"]}
    assert ss.settings_problem(swap, stored) is None
    assert ss.merged(stored, swap) == {"paste_last_hotkey": ["control", "option", "c"],
                                       "copy_last_hotkey": ["control", "option", "v"]}


def test_a_paste_last_saved_with_a_transform_shortcut_is_checked_against_it():
    payload = {"paste_last_hotkey": ["control", "option", "t"],
               "transforms": {"hotkey": ["control", "option", "t"]}}
    assert "(Transform)" in ss.settings_problem(payload, {})


def test_a_paste_last_may_keep_and_resave_its_own_combination():
    stored = {"paste_last_hotkey": ["control", "option", "v"]}
    assert ss.settings_problem({"paste_last_hotkey": ["option", "control", "v"]}, stored) is None


def test_the_cancel_key_is_checked_against_the_hotkey_and_the_shortcuts():
    assert "dictation hotkey" in ss.settings_problem({"cancel_key": "f5"}, {"hotkey_keys": ["shift", "f5"]})
    assert "Paste last uses F16" in ss.settings_problem({"cancel_key": "f16"}, {"paste_last_hotkey": ["f16"]})
    assert "Esc or an F key" in ss.settings_problem({"cancel_key": "a"}, {})
    # A payload that moves the shortcut off that key frees it in the same save.
    assert ss.settings_problem({"cancel_key": "f16", "paste_last_hotkey": []}, {"paste_last_hotkey": ["f16"]}) is None


def test_a_new_shortcut_is_checked_against_the_cancel_key_in_the_same_payload_or_the_file():
    assert "F16 is your cancel key" in ss.settings_problem({"copy_last_hotkey": ["f16"]}, {"cancel_key": "f16"})


def test_merged_gives_the_stored_form_and_none_for_the_default():
    got = ss.merged({}, {"paste_last_hotkey": ["v", "option", "control"], "copy_last_hotkey": [], "cancel_key": "F16"})
    assert got == {"paste_last_hotkey": ["control", "option", "v"], "copy_last_hotkey": None, "cancel_key": "f16"}
    assert ss.merged({}, {"cancel_key": "esc"}) == {"cancel_key": None}
    assert ss.merged({}, {"cancel_key": "Escape"}) == {"cancel_key": None}
    assert ss.merged({}, {"auto_paste": True}) == {}


def test_reset_clears_the_shortcuts_and_keeps_the_switches():
    stored = {"hotkey_keys": ["cmd", "shift"], "cancel_key": "f9", "paste_last_hotkey": ["f16"],
              "copy_last_hotkey": ["f17"], "language": "en",
              "transforms": {"enabled": True, "hotkey": ["control", "option", "t"], "default": "polish"},
              "command_mode": {"enabled": True, "hotkey": ["control", "option"], "web_engine": "google"}}
    ss.reset(stored)
    assert stored == {"language": "en",
                      "transforms": {"enabled": True, "hotkey": [], "default": "polish"},
                      "command_mode": {"enabled": True, "hotkey": [], "web_engine": "google"}}
    ss.reset({})                          # nothing to reset
    ss.reset(None)
    odd = {"transforms": 5}
    ss.reset(odd)
    assert odd == {"transforms": 5}


# ── the rows ─────────────────────────────────────────────────────────────────

def by_id(rows_):
    return {r["id"]: r for r in rows_}


def test_the_page_lists_every_action_in_one_order():
    rows_ = ss.rows({}, MAC)
    assert [r["id"] for r in rows_] == ["dictation", "hands_free", "cancel", "transform", "command",
                                        "paste_last", "copy_last"]
    assert [r["label"] for r in rows_] == ["Hold to dictate", "Hands-free", "Cancel", "Transform",
                                           "Command mode", "Paste last", "Copy last"]
    for r in rows_:
        assert r["description"].endswith(".") and no_dashes(r["description"]) and no_dashes(r["label"]), r["id"]
        assert r["label"][0].isupper() and r["label"] != r["label"].upper()


def test_the_default_rows_show_fn_and_esc_and_nothing_for_the_rest():
    rows_ = by_id(ss.rows({}, MAC))
    assert rows_["dictation"]["display"] == "Fn" and rows_["dictation"]["keys"] == ["fn"]
    assert rows_["hands_free"]["keys"] == ["fn", "space"] and rows_["hands_free"]["display"] == "Fn + Space"
    assert rows_["cancel"]["keys"] == ["esc"] and rows_["cancel"]["display"] == "Esc"
    for rid in ("transform", "command", "paste_last", "copy_last"):
        assert rows_[rid]["keys"] == [] and rows_[rid]["display"] == "" and rows_[rid]["clearable"] is False, rid
    assert ss.rows({}, WIN)[0]["display"] == "Win + Ctrl"
    assert by_id(ss.rows({}, WIN))["hands_free"]["display"] == "Win + Ctrl + Space"


def test_which_rows_can_be_changed_and_cleared():
    rows_ = by_id(ss.rows({"paste_last_hotkey": ["f16"], "cancel_key": "f9"}, MAC))
    assert rows_["hands_free"]["editable"] is False and rows_["hands_free"]["kind"] == "fixed"
    for rid in ("dictation", "cancel", "transform", "command", "paste_last", "copy_last"):
        assert rows_[rid]["editable"] is True, rid
    assert rows_["dictation"]["clearable"] is False, "there is always a dictation hotkey"
    assert rows_["cancel"]["clearable"] is True and rows_["cancel"]["clear_text"] == "Use Esc"
    assert by_id(ss.rows({}, MAC))["cancel"]["clearable"] is False, "Esc is already the default"
    assert rows_["paste_last"]["clearable"] is True and rows_["paste_last"]["clear_text"] == "Clear"
    assert rows_["copy_last"]["clearable"] is False


def test_the_kinds_are_hold_press_and_fixed():
    kinds = {r["id"]: r["kind"] for r in ss.rows({}, MAC)}
    assert kinds == {"dictation": "hold", "hands_free": "fixed", "cancel": "press", "transform": "press",
                     "command": "hold", "paste_last": "press", "copy_last": "press"}


def test_transform_and_command_rows_say_where_to_turn_them_on_while_they_are_off():
    off = by_id(ss.rows({}, MAC))
    assert off["transform"]["enabled"] is False and off["transform"]["needs"] == "Turn on in Transforms"
    assert off["command"]["enabled"] is False and off["command"]["needs"] == "Turn on in Command mode"
    assert off["transform"]["needs_section"] == "Transforms" and off["command"]["needs_section"] == "Command mode"
    for rid in ("dictation", "hands_free", "cancel", "paste_last", "copy_last"):
        assert off[rid]["enabled"] is True and off[rid]["needs"] == "", rid
    on = by_id(ss.rows({"transforms": {"enabled": True}, "command_mode": {"enabled": True}}, MAC))
    assert on["transform"]["enabled"] and on["command"]["enabled"] and on["transform"]["needs"] == ""


def test_a_shortcut_set_while_its_feature_is_off_is_still_shown():
    stored = {"transforms": {"enabled": False, "hotkey": ["control", "option", "t"]}}
    row = by_id(ss.rows(stored, MAC))["transform"]
    assert row["keys"] == ["control", "option", "t"] and row["display"] == "Control + Option + T"
    assert row["enabled"] is False and row["needs"]


def test_a_saved_shortcut_that_no_longer_passes_the_rules_carries_a_sentence():
    stored = {"hotkey_keys": ["control", "option"], "copy_last_hotkey": ["control", "option", "c"],
              "paste_last_hotkey": ["control", "option", "v"]}
    rows_ = by_id(ss.rows(stored, MAC))
    assert "dictation hotkey" in rows_["copy_last"]["conflict"] and rows_["copy_last"]["conflict"].endswith(".")
    assert "dictation hotkey" in rows_["paste_last"]["conflict"]
    assert "overlaps your shortcut" in rows_["dictation"]["conflict"], "and the hotkey row says so too"
    assert by_id(ss.rows({"paste_last_hotkey": ["control", "option", "v"]}, MAC))["paste_last"]["conflict"] == ""


def test_two_rows_on_one_combination_both_say_so():
    stored = {"paste_last_hotkey": ["control", "option", "v"], "copy_last_hotkey": ["option", "control", "v"]}
    rows_ = by_id(ss.rows(stored, MAC))
    assert "(Copy last)" in rows_["paste_last"]["conflict"] and "(Paste last)" in rows_["copy_last"]["conflict"]


def test_on_windows_a_saved_altgr_combination_is_flagged():
    row = by_id(ss.rows({"copy_last_hotkey": ["ctrl", "alt", "k"]}, WIN))["copy_last"]
    assert "AltGr" in row["conflict"]


def test_a_cancel_key_that_clashes_is_flagged_on_its_row():
    row = by_id(ss.rows({"cancel_key": "f16", "copy_last_hotkey": ["f16"]}, MAC))["cancel"]
    assert "Copy last uses F16" in row["conflict"]


def test_the_view_has_the_rows_the_rules_and_the_platform():
    v = ss.view({}, MAC)
    assert v["ok"] is True and v["platform"] == MAC and len(v["rows"]) == 7
    json.dumps(v)                                           # it crosses the bridge as JSON


def test_the_rules_are_plain_and_follow_the_platform():
    mac, win = ss.rules(MAC), ss.rules(WIN)
    for rules_ in (mac, win):
        assert len(rules_["lines"]) >= 5 and len(rules_["reserved"]) >= 14
        for line in rules_["lines"]:
            assert line.endswith(".") and no_dashes(line) and line[0].isupper(), line
        for item in rules_["reserved"]:
            assert set(item) == {"keys", "display", "who", "what", "line"}
            assert item["line"].endswith(".") and item["display"] in item["line"] and no_dashes(item["line"])
    mac_text, win_text = " ".join(mac["lines"]), " ".join(win["lines"])
    assert "Control or Command" in mac_text and "modifier keys held together" in mac_text and "Fn" in mac_text
    assert "AltGr" in win_text and "Windows key" in win_text and "modifier keys held together" not in win_text
    assert "AltGr" not in mac_text
    assert any(i["display"] == "Command + Space" for i in mac["reserved"])
    assert any(i["display"] == "Win + Shift + S" for i in win["reserved"])


def test_every_rule_the_page_states_is_true_of_the_validator():
    """The words are held to the behaviour: each sentence names something check() does."""
    assert ah.check(["control", "option", "t"], MAC)["ok"]
    assert ah.check(["f16"], MAC)["ok"] and ah.check(["f16"], WIN)["ok"]                  # F keys alone
    assert ah.check(["control", "option"], MAC, hold=True)["ok"]                          # modifier keys alone
    assert not ah.check(["ctrl", "alt", "k"], WIN)["ok"]                                  # AltGr
    assert not ah.check(["f13"], MAC, ["fn"])["ok"]                                       # F13 to F15 with Fn
    assert ah.cancel_problem("f20", MAC, {}) is None and ah.cancel_problem("f21", MAC, {})   # F1 to F20
    assert ah.BARE_KEYS[0] == "f1" and ah.BARE_KEYS[-1] == "f20"


# ── the bridge: Api methods lifted out of app.py ─────────────────────────────

LIFTED = ("get_settings", "save_settings", "get_shortcuts", "check_shortcut", "save_shortcut", "reset_shortcuts",
          "shortcut_recording", "save_transform_hotkey", "save_command_hotkey", "save_hotkey_config",
          "_keys_argument", "_restart_hotkey_listener")


def bridge(tmp_path, monkeypatch, initial=None, pipeline=None, system="Darwin"):
    """An object with the real settings and shortcut methods of Api, saving to
    a temporary settings.json, running as ``system``."""
    nodes = [n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name in LIFTED]
    assert {n.name for n in nodes} == set(LIFTED), "an Api shortcut method moved"
    logged = []
    ns = {"os": os, "json": json, "threading": threading, "time": time, "_pipeline": pipeline,
          "_log_to_file": logged.append,
          "_platform": types.SimpleNamespace(system=lambda: system),
          "CLIPBOARD_CHOICE_UNKNOWN": um.CLIPBOARD_CHOICE_UNKNOWN, "SOUNDS_UNKNOWN": um.SOUNDS_UNKNOWN,
          "TIPS_UNKNOWN": um.TIPS_UNKNOWN, "_languages": __import__("languages"), "_transforms": tf,
          "_command_mode": cm}
    exec(compile(ast.Module(nodes, []), "<app.py Api>", "exec"), ns)
    api = settings_api(tmp_path, initial)
    for name in LIFTED:
        setattr(type(api), name, ns[name])
    monkeypatch.setitem(sys.modules, "windows_hotkey", types.SimpleNamespace(
        KEY_TO_VK={k: [0] for k in ("win", "ctrl", "alt", "shift") + tuple("abcdefghijklmnopqrstuvwxyz0123456789")
                   + tuple(f"f{i}" for i in range(1, 25))},
        MODIFIER_KEYS={"win", "ctrl", "alt", "shift"}))
    monkeypatch.setitem(sys.modules, "mac_hotkey_monitor", types.SimpleNamespace(
        KEY_TO_KEYCODE={k: 0 for k in ("space", "return", "tab", "esc", "f13", "f14", "f15", "f16", "f17", "f18", "f19")
                        + tuple("abcdefghijklmnopqrstuvwxyz")},
        MODIFIER_FLAGS={k: 0 for k in ("cmd", "shift", "option", "control", "fn")}))
    api.logged = logged
    return api


def test_get_settings_carries_the_three_settings(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {"paste_last_hotkey": ["control", "option", "v"], "cancel_key": "f16"})
    got = api.get_settings()
    assert got["paste_last_hotkey"] == ["control", "option", "v"] and got["paste_last_display"] == "Control + Option + V"
    assert got["copy_last_hotkey"] == [] and got["copy_last_display"] == ""
    assert got["cancel_key"] == "f16" and got["cancel_display"] == "F16"
    plain = bridge(tmp_path / "plain", monkeypatch).get_settings()
    assert plain["cancel_key"] == "esc" and plain["paste_last_hotkey"] == [] and plain["copy_last_hotkey"] == []


def test_save_settings_stores_the_canonical_order_and_keeps_everything_else(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {"hotkey_keys": ["fn"], "language": "en"})
    r = api.save_settings({"paste_last_hotkey": ["v", "option", "control"]})
    assert r["ok"] is True and r["notes"] == ["Shortcuts updated"]
    assert api.data["paste_last_hotkey"] == ["control", "option", "v"]
    assert api.data["hotkey_keys"] == ["fn"] and api.data["language"] == "en"
    assert api.save_settings({"auto_paste": False})["ok"] is True
    assert api.data["paste_last_hotkey"] == ["control", "option", "v"], "other saves leave it alone"


def test_clearing_a_shortcut_or_going_back_to_esc_removes_the_key_from_the_file(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch)
    assert api.save_settings({"copy_last_hotkey": ["f16"], "cancel_key": "f9"})["ok"]
    assert api.data["copy_last_hotkey"] == ["f16"] and api.data["cancel_key"] == "f9"
    assert api.save_settings({"copy_last_hotkey": [], "cancel_key": "esc"})["ok"]
    assert "copy_last_hotkey" not in api.data and "cancel_key" not in api.data


def test_a_refused_shortcut_saves_none_of_the_payload(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {"language": "en"})
    r = api.save_settings({"auto_paste": False, "paste_last_hotkey": ["t"]})
    assert r["ok"] is False and "Control or Command" in r["error"]
    assert api.data == {"language": "en"}, "not even the auto_paste that came with it"
    r = api.save_settings({"copy_last_hotkey": ["control", "option", "c"], "cancel_key": "q"})
    assert r["ok"] is False and "Esc or an F key" in r["error"] and "copy_last_hotkey" not in api.data


def test_the_validator_is_the_same_for_every_way_a_shortcut_is_saved(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {"transforms": {"hotkey": ["control", "option", "t"]}})
    via_settings = api.save_settings({"copy_last_hotkey": ["control", "option", "t"]})["error"]
    via_shortcut = api.save_shortcut("copy_last", ["control", "option", "t"])["error"]
    via_check = api.check_shortcut("copy_last", ["control", "option", "t"])["error"]
    assert via_settings == via_shortcut == via_check and "(Transform)" in via_check


def test_the_transform_and_command_saves_see_the_new_shortcuts(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {"paste_last_hotkey": ["control", "option", "v"],
                                         "cancel_key": "f16"})
    r = api.save_transform_hotkey(["control", "option", "v"])
    assert r["ok"] is False and "(Paste last)" in r["error"]
    r = api.save_command_hotkey(["control", "option", "shift", "v"])
    assert r["ok"] is False and "overlaps another shortcut (Paste last)" in r["error"]
    assert api.save_transform_hotkey(["f16"])["ok"] is False, "F16 is the cancel key"
    assert "cancel key" in api.save_transform_hotkey(["f16"])["error"]
    ok = api.save_transform_hotkey(["control", "option", "t"])
    assert ok == {"ok": True, "keys": ["control", "option", "t"], "display": "Control + Option + T"}


def test_command_mode_accepts_modifier_keys_alone_on_a_mac_through_the_bridge(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch)
    r = api.save_command_hotkey(["option", "control"])
    assert r == {"ok": True, "keys": ["control", "option"], "display": "Control + Option"}
    assert api.data["command_mode"]["hotkey"] == ["control", "option"]
    assert api.save_command_hotkey(["shift"])["ok"] is False
    assert api.save_command_hotkey(["option", "shift"])["ok"] is False
    assert api.save_transform_hotkey(["control", "option"])["ok"] is False, "a press needs a key"


def test_a_new_dictation_hotkey_may_not_overlap_paste_last_or_include_the_cancel_key(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {"paste_last_hotkey": ["control", "option", "v"], "cancel_key": "f16"})
    r = api.save_hotkey_config(["control", "option"])
    assert r["ok"] is False and "Control + Option + V" in r["error"]
    r = api.save_hotkey_config(["shift", "f16"])
    assert r["ok"] is False and "F16" in r["error"] and "cancels a recording" in r["error"]
    assert api.save_hotkey_config(["cmd", "shift"])["ok"] is True
    assert api.data["hotkey_keys"] == ["cmd", "shift"]


def test_a_new_dictation_hotkey_with_the_default_esc_is_checked_as_it_always_was(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch)
    assert api.save_hotkey_config(["cmd", "esc"])["ok"] is True


def test_get_shortcuts_is_the_page(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {"copy_last_hotkey": ["f16"], "transforms": {"enabled": True}})
    page = api.get_shortcuts()
    assert page["ok"] is True and [r["id"] for r in page["rows"]][:3] == ["dictation", "hands_free", "cancel"]
    rows_ = by_id(page["rows"])
    assert rows_["copy_last"]["display"] == "F16" and rows_["transform"]["enabled"] is True
    json.dumps(page)


def test_check_shortcut_says_what_a_save_would_say_and_saves_nothing(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {"hotkey_keys": ["cmd", "shift"], "cancel_key": "f16"})
    assert api.check_shortcut("copy_last", ["control", "option", "c"]) == {
        "ok": True, "keys": ["control", "option", "c"], "display": "Control + Option + C"}
    assert "dictation hotkey" in api.check_shortcut("copy_last", ["cmd", "shift", "c"])["error"]
    assert api.check_shortcut("command", ["control", "option"])["ok"] is True
    assert api.check_shortcut("transform", ["control", "option"])["ok"] is False
    assert api.check_shortcut("cancel", ["f17"]) == {"ok": True, "keys": ["f17"], "display": "F17"}
    assert api.check_shortcut("cancel", ["f1", "f2"])["error"] == "Press one key to cancel with."
    assert "Esc or an F key" in api.check_shortcut("cancel", ["a"])["error"]
    assert api.check_shortcut("cancel", ["Escape"]) == {"ok": True, "keys": ["esc"], "display": "Esc"}
    assert api.check_shortcut("dictation", ["control", "option"])["ok"] is True
    assert "F16" in api.check_shortcut("dictation", ["shift", "f16"])["error"]
    assert api.check_shortcut("nonsense", ["f16"])["ok"] is False
    assert api.check_shortcut("copy_last", [])["error"] == "Hold the keys you want, then save."
    assert api.check_shortcut("copy_last", '["f17"]')["ok"] is True, "the JSON of a list works too"
    assert api.check_shortcut("copy_last", "not json")["ok"] is False
    assert api.saves == 0, "nothing was saved"


def test_save_shortcut_goes_through_the_call_that_always_saved_each(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch)
    assert api.save_shortcut("transform", ["control", "option", "t"])["keys"] == ["control", "option", "t"]
    assert api.data["transforms"]["hotkey"] == ["control", "option", "t"], "Transform's key is where it always was"
    assert api.save_shortcut("command", '["control", "cmd"]')["keys"] == ["control", "cmd"]
    assert api.data["command_mode"]["hotkey"] == ["control", "cmd"]
    assert api.save_shortcut("paste_last", ["control", "option", "v"]) == {
        "ok": True, "keys": ["control", "option", "v"], "display": "Control + Option + V"}
    assert api.save_shortcut("copy_last", ["f16"])["display"] == "F16"
    assert api.save_shortcut("cancel", ["f17"]) == {"ok": True, "keys": ["f17"], "display": "F17"}
    assert api.data["paste_last_hotkey"] == ["control", "option", "v"] and api.data["cancel_key"] == "f17"
    assert api.save_shortcut("dictation", ["cmd", "shift"])["ok"] is True and api.data["hotkey_keys"] == ["cmd", "shift"]


def test_save_shortcut_clears_with_an_empty_list(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {"paste_last_hotkey": ["f16"], "copy_last_hotkey": ["f17"],
                                         "cancel_key": "f9", "transforms": {"hotkey": ["control", "option", "t"]},
                                         "command_mode": {"hotkey": ["control", "option"]}})
    assert api.save_shortcut("paste_last", [])["keys"] == []
    assert api.save_shortcut("copy_last", None)["keys"] == []
    assert api.save_shortcut("cancel", [])["keys"] == ["esc"]
    assert api.save_shortcut("transform", "[]")["keys"] == []
    assert api.save_shortcut("command", [])["keys"] == []
    assert ss.settings(api.data) == {"paste_last_hotkey": [], "copy_last_hotkey": [], "cancel_key": "esc"}
    assert api.data["transforms"]["hotkey"] == [] and api.data["command_mode"]["hotkey"] == []


def test_save_shortcut_says_why_in_one_sentence(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch)
    for action, keys in (("paste_last", ["t"]), ("copy_last", ["control", "cmd", "q"]), ("cancel", ["a"]),
                         ("cancel", ["f1", "f2"]), ("transform", ["control"]), ("nonsense", ["f16"]),
                         ("paste_last", "{")):
        got = api.save_shortcut(action, keys)
        assert got["ok"] is False and got["error"].endswith(".") and no_dashes(got["error"]), (action, got)
    assert api.saves == 0


def test_reset_puts_every_shortcut_back_and_keeps_the_switches(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {
        "hotkey_keys": ["cmd", "shift"], "cancel_key": "f9", "paste_last_hotkey": ["f16"], "copy_last_hotkey": ["f17"],
        "language": "fr", "transforms": {"enabled": True, "hotkey": ["control", "option", "t"]},
        "command_mode": {"enabled": True, "hotkey": ["control", "option"]}})
    page = api.reset_shortcuts()
    assert page["ok"] is True
    rows_ = by_id(page["rows"])
    assert rows_["dictation"]["keys"] == ["fn"] and rows_["cancel"]["keys"] == ["esc"]
    for rid in ("transform", "command", "paste_last", "copy_last"):
        assert rows_[rid]["keys"] == [], rid
    assert api.data["language"] == "fr"
    assert api.data["transforms"]["enabled"] is True and api.data["command_mode"]["enabled"] is True
    assert "hotkey_keys" not in api.data


def test_resetting_an_untouched_file_changes_nothing(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch, {"language": "en"})
    assert api.reset_shortcuts()["ok"] is True
    assert api.data == {"language": "en"} and api.saves == 0


def test_shortcut_recording_tells_the_registry(tmp_path, monkeypatch):
    registry = ah.ActionHotkeys(MAC)
    pipeline = types.SimpleNamespace(_action_hotkeys=registry)
    api = bridge(tmp_path, monkeypatch, pipeline=pipeline)
    assert api.shortcut_recording(True) == {"ok": True}
    assert registry._recording_since is not None
    assert api.shortcut_recording(False) == {"ok": True}
    assert registry._recording_since is None
    assert bridge(tmp_path / "no-pipeline", monkeypatch).shortcut_recording(True) == {"ok": True}


def test_a_failing_save_is_a_sentence_not_an_exception(tmp_path, monkeypatch):
    api = bridge(tmp_path, monkeypatch)
    api._save_settings_file = lambda d: (_ for _ in ()).throw(PermissionError("denied"))
    got = api.save_shortcut("paste_last", ["control", "option", "v"])
    assert got["ok"] is False and got["error"] and "PermissionError" not in got["error"]
    assert not any("denied" in line for line in api.logged), "the log never carries the message"


def test_saving_the_cancel_key_remakes_the_hotkey_listener_and_other_shortcuts_do_not(tmp_path, monkeypatch):
    calls = []

    class Pipeline:
        hotkey_listener = object()

        def apply_last_result_shortcuts(self):
            calls.append("apply")

        def apply_transform_settings(self):
            calls.append("transform")

        def apply_command_settings(self):
            calls.append("command")
    api = bridge(tmp_path, monkeypatch, pipeline=Pipeline())
    api._restart_hotkey_listener = lambda: calls.append("restart")
    api.save_settings({"copy_last_hotkey": ["f16"]})
    assert calls == ["apply"]
    del calls[:]
    api.save_settings({"cancel_key": "f17"})
    assert calls == ["restart"], "the listener is what listens for the cancel key"


def test_the_restart_helper_builds_the_listener_with_the_chosen_cancel_key():
    """The restart is one helper; both the hotkey save and the cancel-key save use it."""
    helper = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "_restart_hotkey_listener")
    src = ast.unparse(helper)
    assert src.count("cancel_key=cancel_key") == 2, "Windows and Mac listeners both get it"
    for call in ("apply_transform_settings", "apply_command_settings", "apply_last_result_shortcuts"):
        assert call in src
    assert src.index("apply_last_result_shortcuts") < src.index("hotkey_listener.start()")
    start = ast.unparse(next(n for n in ast.walk(_TREE) if isinstance(n, ast.FunctionDef) and n.name == "start_hotkey"))
    assert start.count("cancel_key=cancel_key") == 2 and "cancel_key_of(stored)" in start
    assert start.index("apply_last_result_shortcuts") < start.index("hotkey_listener.start()")


def test_saving_the_cancel_key_it_already_has_does_not_remake_the_listener(tmp_path, monkeypatch):
    calls = []
    pipeline = types.SimpleNamespace(apply_last_result_shortcuts=lambda: calls.append("apply"),
                                     apply_transform_settings=lambda: None, apply_command_settings=lambda: None)
    api = bridge(tmp_path, monkeypatch, {"cancel_key": "f17"}, pipeline=pipeline)
    api._restart_hotkey_listener = lambda: calls.append("restart")
    api.save_settings({"cancel_key": "f17"})
    assert calls == ["apply"]
    del calls[:]
    api.save_settings({"cancel_key": "esc"})
    assert calls == ["restart"]


def test_resetting_remakes_the_listener_only_when_the_hotkey_or_cancel_key_changed(tmp_path, monkeypatch):
    calls = []
    pipeline = types.SimpleNamespace(apply_last_result_shortcuts=lambda: calls.append("last"),
                                     apply_transform_settings=lambda: calls.append("transform"),
                                     apply_command_settings=lambda: calls.append("command"))
    api = bridge(tmp_path, monkeypatch, {"paste_last_hotkey": ["f16"]}, pipeline=pipeline)
    api._restart_hotkey_listener = lambda: calls.append("restart")
    assert api.reset_shortcuts()["ok"] is True
    assert calls == ["transform", "command", "last"], "only the shortcuts are applied again"
    del calls[:]
    api.save_settings({"cancel_key": "f9"})
    del calls[:]
    assert api.reset_shortcuts()["ok"] is True
    assert calls == ["restart"], "the cancel key changed back: the listener is made again, and applies them"
    del calls[:]
    api.save_hotkey_config(["cmd", "shift"])
    del calls[:]
    assert api.reset_shortcuts()["ok"] is True and calls == ["restart"]


def test_a_save_that_names_no_shortcut_setting_never_touches_the_listener(tmp_path, monkeypatch):
    calls = []
    pipeline = types.SimpleNamespace(apply_last_result_shortcuts=lambda: calls.append("apply"),
                                     apply_transform_settings=lambda: None, apply_command_settings=lambda: None)
    api = bridge(tmp_path, monkeypatch, pipeline=pipeline)
    api._restart_hotkey_listener = lambda: calls.append("restart")
    api.save_settings({"auto_paste": False})
    assert calls == []
