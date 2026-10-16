"""Command mode through the bridge (S8): get_settings and save_settings with
the "command_mode" key, the shortcut's own call, and how it checks against the
dictation hotkey and the Transform shortcut.

app.py's Api methods are lifted out of its source and run against a temporary
settings.json (tests/_settings_fake.py): no window, no keys.
"""
import ast
import json
import os
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import command_mode as cm  # noqa: E402
import transforms as tf  # noqa: E402
import user_messages as um  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _method(name):
    return next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)


@pytest.fixture(autouse=True)
def _as_a_mac(monkeypatch):
    """The bridge asks which computer this is: these tests use Mac key names, so
    they run as a Mac on every runner."""
    monkeypatch.setattr(tf, "_this_platform", lambda: "Darwin")


def _lift(name, **extra):
    ns = {"os": os, "json": json, "threading": threading, "time": __import__("time"),
          "_pipeline": None, "_log_to_file": lambda m: None,
          "_platform": types.SimpleNamespace(system=lambda: "Darwin"),
          "CLIPBOARD_CHOICE_UNKNOWN": um.CLIPBOARD_CHOICE_UNKNOWN, "SOUNDS_UNKNOWN": um.SOUNDS_UNKNOWN,
          "TIPS_UNKNOWN": um.TIPS_UNKNOWN, "_languages": __import__("languages"),
          "_transforms": tf, "_command_mode": cm}
    ns.update(extra)
    exec(compile(ast.Module([_method(name)], []), "<app.py Api>", "exec"), ns)
    return ns[name]


def methods(tmp_path, initial=None, **extra):
    api = settings_api(tmp_path, initial)
    save, get = _lift("save_settings", **extra), _lift("get_settings", **extra)
    api.save_settings = lambda payload: save(api, payload)
    return api, save, get


GOOD = ["control", "option", "c"]


# ── reading ──────────────────────────────────────────────────────────────────

def test_the_defaults_are_off_with_no_shortcut_and_perplexity(tmp_path):
    api, _save, get = methods(tmp_path)
    c = get(api)["command_mode"]
    assert c == {"enabled": False, "hotkey": [], "web_engine": "perplexity", "hotkey_display": ""}


@pytest.mark.parametrize("junk", [None, 5, "x", [], {"enabled": 1, "hotkey": "cmd", "web_engine": 9}])
def test_a_hand_edited_file_reads_as_the_defaults(tmp_path, junk):
    api, _save, get = methods(tmp_path, {"command_mode": junk})
    c = get(api)["command_mode"]
    assert c["enabled"] is False and c["hotkey"] == [] and c["web_engine"] == "perplexity"


def test_the_shortcut_is_shown_as_the_platform_names_it(tmp_path):
    api, _save, get = methods(tmp_path, {"command_mode": {"enabled": True, "hotkey": GOOD, "web_engine": "claude"}})
    c = get(api)["command_mode"]
    assert c["hotkey"] == GOOD and c["hotkey_display"] == "Control + Option + C" and c["web_engine"] == "claude"


# ── writing ──────────────────────────────────────────────────────────────────

def test_save_keeps_the_switch_the_engine_and_what_was_there(tmp_path):
    api, save, get = methods(tmp_path, {"hotkey_keys": ["fn"], "language": "en", "transforms": {"enabled": True}})
    assert save(api, {"command_mode": {"enabled": True}})["ok"] is True
    assert api.data["hotkey_keys"] == ["fn"] and api.data["command_mode"]["enabled"] is True
    assert api.data["transforms"] == {"enabled": True}, "Transforms is not touched"
    assert save(api, {"command_mode": {"web_engine": "google"}})["ok"] is True
    c = get(api)["command_mode"]
    assert c["enabled"] is True and c["web_engine"] == "google"
    assert save(api, {"auto_paste": False})["ok"] is True, "other saves leave it alone"
    assert get(api)["command_mode"] == c


def test_the_notes_say_only_on_or_off(tmp_path):
    api, save, _get = methods(tmp_path)
    r = save(api, {"command_mode": {"enabled": True, "web_engine": "google"}})
    assert r["ok"] is True and r["notes"] == ["Command mode: on"]
    assert save(api, {"command_mode": {"enabled": False}})["notes"] == ["Command mode: off"]


@pytest.mark.parametrize("payload,message", [
    ({"surprise": 1}, um.COMMAND_BAD),
    ({"enabled": "yes"}, um.COMMAND_BAD),
    ({"hotkey": "cmd+c"}, um.COMMAND_BAD),
    ({"web_engine": 3}, um.COMMAND_BAD),
    ({"web_engine": "bing"}, um.COMMAND_ENGINE_UNKNOWN),
    ({"web_engine": "https://evil.example/?q="}, um.COMMAND_ENGINE_UNKNOWN),
])
def test_a_refused_value_saves_nothing_from_the_payload(tmp_path, payload, message):
    start = {"dialect": "en-GB", "command_mode": {"enabled": True}}
    api, save, _get = methods(tmp_path, start)
    r = save(api, {"command_mode": payload, "dialect": "en-US", "auto_paste": False})
    assert r == {"ok": False, "error": message}
    assert api.data == start and api.saves == 0


def test_a_payload_that_is_not_an_object_is_refused(tmp_path):
    api, save, _get = methods(tmp_path)
    assert save(api, {"command_mode": "on"}) == {"ok": False, "error": um.COMMAND_BAD}
    assert save(api, {"command_mode": None}) == {"ok": False, "error": um.COMMAND_BAD}


def test_the_shortcut_is_stored_in_the_one_order(tmp_path):
    api, save, _get = methods(tmp_path)
    assert save(api, {"command_mode": {"hotkey": ["C", "alt", "ctrl"]}})["ok"] is True
    assert api.data["command_mode"]["hotkey"] == GOOD


def test_the_shortcut_is_refused_when_it_overlaps_the_dictation_hotkey(tmp_path):
    start = {"hotkey_keys": ["control", "shift"]}
    api, save, _get = methods(tmp_path, start)
    r = save(api, {"command_mode": {"hotkey": ["control", "shift", "c"]}})
    assert r["ok"] is False and "dictation hotkey" in r["error"] and api.data == start


def test_the_shortcut_is_refused_when_it_is_or_sits_inside_the_transform_shortcut(tmp_path):
    start = {"transforms": {"enabled": True, "hotkey": ["control", "option", "t"]}}
    api, save, _get = methods(tmp_path, start)
    same = save(api, {"command_mode": {"hotkey": ["option", "control", "t"]}})
    assert same["ok"] is False and "Another shortcut" in same["error"]
    nested = save(api, {"command_mode": {"hotkey": ["control", "option", "shift", "t"]}})
    assert nested["ok"] is False and "overlaps" in nested["error"]
    assert api.data == start and api.saves == 0
    assert save(api, {"command_mode": {"hotkey": GOOD}})["ok"] is True


def test_the_transform_shortcut_may_not_take_the_command_shortcut_either(tmp_path):
    api, save, _get = methods(tmp_path, {"command_mode": {"enabled": True, "hotkey": GOOD}})
    r = save(api, {"transforms": {"hotkey": ["option", "control", "c"]}})
    assert r["ok"] is False and "Another shortcut" in r["error"]
    r = save(api, {"transforms": {"hotkey": ["control", "option", "shift", "c"]}})
    assert r["ok"] is False and "overlaps" in r["error"]
    assert "hotkey" not in api.data.get("transforms", {})
    assert save(api, {"transforms": {"hotkey": ["control", "option", "t"]}})["ok"] is True


def test_changes_to_other_settings_are_not_blocked_by_a_saved_command_shortcut(tmp_path):
    api, save, _get = methods(tmp_path, {"command_mode": {"enabled": True, "hotkey": GOOD}})
    assert save(api, {"command_mode": {"web_engine": "google"}})["ok"] is True, "its own shortcut is not its own clash"
    assert save(api, {"command_mode": {"enabled": False, "hotkey": GOOD}})["ok"] is True


# ── applied at once ──────────────────────────────────────────────────────────

def test_a_save_of_command_mode_is_applied_to_the_running_app_and_others_are_not(tmp_path):
    applied = []
    pipeline = types.SimpleNamespace(apply_command_settings=lambda: applied.append("command"),
                                     apply_transform_settings=lambda: applied.append("transform"))
    api, save, _get = methods(tmp_path, _pipeline=pipeline)
    assert save(api, {"command_mode": {"enabled": True}})["ok"] is True
    assert applied == ["command"]
    assert save(api, {"transforms": {"enabled": True}})["ok"] is True
    assert applied == ["command", "transform"]
    assert save(api, {"auto_paste": False})["ok"] is True
    assert applied == ["command", "transform"]


def test_a_failure_applying_it_does_not_fail_the_save(tmp_path):
    def boom():
        raise RuntimeError("tap gone")
    pipeline = types.SimpleNamespace(apply_command_settings=boom)
    logged = []
    api, save, _get = methods(tmp_path, _pipeline=pipeline, _log_to_file=logged.append)
    assert save(api, {"command_mode": {"enabled": True}})["ok"] is True
    assert any("not applied live" in m and "RuntimeError" in m for m in logged)


def test_the_settings_are_written_through_the_editing_helper_only():
    src = ast.unparse(_method("save_settings"))
    assert "_editing_settings" in src and "_save_settings_file" not in src
    assert "'command_mode'" in ast.unparse(_method("get_settings"))


# ── the shortcut's own call ──────────────────────────────────────────────────

def hotkey_methods(tmp_path, initial=None):
    api, save, _get = methods(tmp_path, initial)
    api.save_command_hotkey = types.MethodType(_lift("save_command_hotkey"), api)
    return api


def test_saving_the_shortcut_answers_like_the_transform_one_does(tmp_path):
    api = hotkey_methods(tmp_path, {"command_mode": {"enabled": True}})
    r = api.save_command_hotkey(["c", "option", "control"])
    assert r["ok"] is True and r["keys"] == GOOD and r["display"] == "Control + Option + C"
    assert api.data["command_mode"]["hotkey"] == GOOD and api.data["command_mode"]["enabled"] is True
    r = api.save_command_hotkey(json.dumps(["control", "shift", "y"]))
    assert r["ok"] is True and r["keys"] == ["control", "shift", "y"]


def test_clearing_the_shortcut_with_an_empty_list(tmp_path):
    api = hotkey_methods(tmp_path, {"command_mode": {"hotkey": GOOD}})
    assert api.save_command_hotkey([]) == {"ok": True, "keys": [], "display": ""}
    assert api.data["command_mode"]["hotkey"] == []
    assert api.save_command_hotkey(None)["ok"] is True
    assert api.save_command_hotkey("[]")["ok"] is True


@pytest.mark.parametrize("keys,fragment", [
    (["c"], "Control or Command"),
    (["control", "option"], "Add a letter"),
    (["fn", "control", "c"], "Fn"),
    (["control", "cmd", "q"], "already use"),
    ("not json", "Hold the keys"),
    (["control", "option", "c", "y"], "one letter"),
])
def test_a_shortcut_that_cannot_be_used_says_why_in_one_sentence(tmp_path, keys, fragment):
    api = hotkey_methods(tmp_path, {"hotkey_keys": ["fn"]})
    r = api.save_command_hotkey(keys)
    assert r["ok"] is False and fragment in r["error"], r
    assert "command_mode" not in api.data


def test_the_shortcut_that_is_the_transform_one_is_refused_in_one_sentence(tmp_path):
    api = hotkey_methods(tmp_path, {"transforms": {"hotkey": ["control", "option", "c"]}})
    r = api.save_command_hotkey(GOOD)
    assert r["ok"] is False and "Another shortcut" in r["error"] and r["error"].endswith(".")


def test_a_dictation_hotkey_that_overlaps_the_command_shortcut_is_refused(tmp_path, monkeypatch):
    fn = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "save_hotkey_config")
    ns = {"json": json, "threading": threading, "time": __import__("time"), "_pipeline": None,
          "_log_to_file": lambda m: None, "_platform": types.SimpleNamespace(system=lambda: "Darwin")}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    keys = {k: 0 for k in "abcdefghijklmnopqrstuvwxyz"} | {"space": 0}
    monkeypatch.setitem(sys.modules, "mac_hotkey_monitor", types.SimpleNamespace(
        KEY_TO_KEYCODE=keys, MODIFIER_FLAGS={k: 0 for k in ("cmd", "shift", "option", "control", "fn")}))
    api = settings_api(tmp_path, {"command_mode": {"enabled": True, "hotkey": GOOD}})
    r = ns["save_hotkey_config"](api, ["control", "option"])
    assert r["ok"] is False and "Control + Option + C" in r["error"]
    assert "hotkey_keys" not in api.data
    ok = ns["save_hotkey_config"](api, ["cmd", "shift"])
    assert ok["ok"] is True and api.data["hotkey_keys"] == ["cmd", "shift"]
