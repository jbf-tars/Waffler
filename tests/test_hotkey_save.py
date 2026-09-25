"""Saving a hotkey returns ok with the saved keys, or one plain sentence.

Settings showed the Mac presets (Fn, Command + Shift, Option + Shift) on
Windows, and the wizard offered "Ctrl + Alt + Space", which Windows never
accepted. The backend refused them with "Unknown key: fn", which neither
screen displayed, so the UI flashed success and nothing changed.

Now src/hotkey_rules.py decides per platform, and every hotkey Windows offers
(Win + Ctrl, Ctrl + Shift, a custom combination) saves. The key tables below
mirror the listeners' own; on the matching OS the real table is checked too.
"""
import ast
import json
import string
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import hotkey_rules as hr  # noqa: E402

# windows_hotkey.KEY_TO_VK / MODIFIER_KEYS, rebuilt (that module needs Win32).
WIN_KEYS = {"win", "ctrl", "alt", "shift"} | {f"f{i}" for i in range(1, 25)} \
    | set(string.ascii_lowercase) | set(string.digits)
WIN_MODS = {"win", "ctrl", "alt", "shift"}
# mac_hotkey_monitor.KEY_TO_KEYCODE + MODIFIER_FLAGS (that module needs Quartz).
MAC_MODS = {"cmd", "command", "shift", "option", "alt", "control", "ctrl", "fn"}
MAC_KEYS = MAC_MODS | {"space", "return", "enter", "tab", "delete", "escape", "esc",
                       "f13", "f14", "f15", "f16", "f17", "f18", "f19"} \
    | set(string.ascii_lowercase)


def win(keys):
    return hr.check(keys, hr.WINDOWS, WIN_KEYS, WIN_MODS)


def mac(keys):
    return hr.check(keys, hr.MAC, MAC_KEYS, MAC_MODS)


@pytest.mark.skipif(sys.platform != "win32", reason="windows_hotkey needs Win32")
def test_the_windows_table_here_is_the_listeners_table():
    import windows_hotkey
    assert set(windows_hotkey.KEY_TO_VK) == WIN_KEYS
    assert set(windows_hotkey.MODIFIER_KEYS) == WIN_MODS


@pytest.mark.skipif(sys.platform != "darwin", reason="mac_hotkey_monitor needs Quartz")
def test_the_mac_table_here_is_the_listeners_table():
    import mac_hotkey_monitor as m
    assert set(m.KEY_TO_KEYCODE) | set(m.MODIFIER_FLAGS) == MAC_KEYS
    assert set(m.MODIFIER_FLAGS) == MAC_MODS


# ── what each platform offers saves ──────────────────────────────────────────

@pytest.mark.parametrize("keys, display", [
    (["win", "ctrl"], "Win + Ctrl"),
    (["ctrl", "shift"], "Ctrl + Shift"),
    (["ctrl", "alt", "k"], "Ctrl + Alt + K"),     # Custom
    (["shift", "f9"], "Shift + F9"),               # Custom
    (["win", "alt", "1"], "Win + Alt + 1"),        # Custom
])
def test_every_windows_choice_saves(keys, display):
    r = win(keys)
    assert r["ok"] is True, r
    assert r["display"] == display


@pytest.mark.parametrize("keys, display", [
    (["fn"], "Fn"),
    (["cmd", "shift"], "Command + Shift"),
    (["option", "shift"], "Option + Shift"),
])
def test_every_mac_choice_saves(keys, display):
    r = mac(keys)
    assert r == {"ok": True, "keys": keys, "display": display}


def test_one_name_everywhere_whichever_key_came_first():
    r = win(["ctrl", "win"])
    assert r["keys"] == ["win", "ctrl"]
    assert r["display"] == "Win + Ctrl"
    assert hr.display(["ctrl", "win"], hr.WINDOWS) == "Win + Ctrl"
    assert hr.display(["shift", "ctrl"], hr.WINDOWS) == "Ctrl + Shift"


# ── what they must refuse, in a sentence ─────────────────────────────────────

@pytest.mark.parametrize("keys, fragment", [
    (["fn"], "Fn is a Mac key"),
    (["cmd", "shift"], "Command is a Mac key"),
    (["option", "shift"], "Option is a Mac key"),
    (["ctrl", "alt", "space"], "Space can't be part of the hotkey"),
    (["k"], "needs at least one of Win, Ctrl, Alt or Shift"),
    (["win"], "Win on its own"),
    (["alt"], "Alt on its own"),
    (["ctrl", "alt"], "already uses that combination"),
    (["ctrl", "alt", "shift", "k"], "up to three keys"),
    (["ctrl", "tab"], "Tab can't be used"),
    ([], "Choose at least one key"),
    ("ctrl+win", "Choose at least one key"),
])
def test_windows_refusals_are_plain(keys, fragment):
    r = win(keys)
    assert r["ok"] is False
    assert fragment in r["error"], r["error"]
    assert r["error"].endswith(".")
    assert "Unknown key" not in r["error"] and chr(0x2014) not in r["error"]


def test_mac_refuses_windows_keys_plainly():
    r = mac(["win", "ctrl"])
    assert r["ok"] is False and "can't be used in the hotkey" in r["error"]


def test_mac_rules_are_unchanged():
    """Only the wording changed on a Mac: the same keys pass and fail."""
    assert mac(["alt"])["ok"] is False
    assert mac(["cmd", "space"])["ok"] is True
    assert mac(["shift", "a"])["ok"] is True
    assert mac(["a"])["ok"] is False


# ── app.py's bridge method ───────────────────────────────────────────────────

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


def _save_method(monkeypatch, system):
    klass = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in klass.body if isinstance(n, ast.FunctionDef)
              and n.name == "save_hotkey_config")
    ns = {"json": json, "threading": threading, "time": time, "_pipeline": None,
          "_log_to_file": lambda m: None,
          "_platform": types.SimpleNamespace(system=lambda: system)}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    monkeypatch.setitem(sys.modules, "windows_hotkey", types.SimpleNamespace(
        KEY_TO_VK={k: [0] for k in WIN_KEYS}, MODIFIER_KEYS=WIN_MODS))
    monkeypatch.setitem(sys.modules, "mac_hotkey_monitor", types.SimpleNamespace(
        KEY_TO_KEYCODE={k: 0 for k in MAC_KEYS - MAC_MODS},
        MODIFIER_FLAGS={k: 0 for k in MAC_MODS}))
    saved = {}
    api = types.SimpleNamespace(_load_settings_file=lambda: {"language": "en"},
                                _save_settings_file=saved.update)
    return ns["save_hotkey_config"], api, saved


@pytest.mark.parametrize("keys", [["win", "ctrl"], ["ctrl", "shift"], '["ctrl", "win"]'])
def test_save_on_windows_saves_and_returns_the_keys(monkeypatch, keys):
    save, api, saved = _save_method(monkeypatch, "Windows")
    r = save(api, keys)
    assert r["ok"] is True
    assert saved["hotkey_keys"] == r["keys"]
    assert r["display"] in ("Win + Ctrl", "Ctrl + Shift")


def test_save_on_windows_refuses_a_mac_preset_without_saving(monkeypatch):
    save, api, saved = _save_method(monkeypatch, "Windows")
    r = save(api, ["fn"])
    assert r["ok"] is False and "Mac key" in r["error"]
    assert saved == {}


def test_save_on_a_mac_uses_words_not_symbols(monkeypatch):
    save, api, saved = _save_method(monkeypatch, "Darwin")
    r = save(api, ["cmd", "shift"])
    assert r == {"ok": True, "keys": ["cmd", "shift"], "display": "Command + Shift"}
    assert saved["hotkey_keys"] == ["cmd", "shift"]


def test_a_failed_save_is_a_sentence(monkeypatch):
    save, api, _ = _save_method(monkeypatch, "Windows")
    api._save_settings_file = lambda d: (_ for _ in ()).throw(PermissionError("denied"))
    r = save(api, ["win", "ctrl"])
    assert r == {"ok": False, "error": "Couldn't save the hotkey. Please try again."}
