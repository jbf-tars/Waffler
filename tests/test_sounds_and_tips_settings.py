"""Settings: "sounds" (off until asked for) and "tips" (on until turned off).

Read with get_settings, written by save_settings, which refuses anything but
a true or a false and saves nothing from a payload it refuses. Both are read
again for each cue and each tip, so a change applies at once.
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import notifier  # noqa: E402
import sounds  # noqa: E402
import user_messages as um  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _method(name):
    return next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _lift(name, **ns):
    exec(compile(ast.Module([_method(name)], []), "<app.py Api>", "exec"), ns)
    return ns[name]


def methods(tmp_path, initial=None):
    api = settings_api(tmp_path, initial)
    ns = {"os": __import__("os"), "_pipeline": None, "_log_to_file": lambda m: None,
          "CLIPBOARD_CHOICE_UNKNOWN": um.CLIPBOARD_CHOICE_UNKNOWN,
          "SOUNDS_UNKNOWN": um.SOUNDS_UNKNOWN, "TIPS_UNKNOWN": um.TIPS_UNKNOWN,
          "_languages": __import__("languages")}
    return api, _lift("save_settings", **ns), _lift("get_settings", **ns)


# ── reading ──────────────────────────────────────────────────────────────

def test_sounds_start_off_and_tips_start_on(tmp_path):
    api, _save, get = methods(tmp_path)
    s = get(api)
    assert s["sounds"] is False and s["tips"] is True


@pytest.mark.parametrize("stored,want_sounds,want_tips", [
    ({"sounds": True, "tips": False}, True, False),
    ({"sounds": False, "tips": True}, False, True),
    ({"sounds": "true", "tips": "false"}, False, True),
    ({"sounds": 1, "tips": 0}, False, True),
    ({"sounds": None, "tips": None}, False, True),
])
def test_only_a_real_true_or_false_counts(tmp_path, stored, want_sounds, want_tips):
    api, _save, get = methods(tmp_path, stored)
    s = get(api)
    assert (s["sounds"], s["tips"]) == (want_sounds, want_tips)
    # And what the cue player and the notifier read is the same answer.
    assert want_sounds is sounds.sounds_enabled(stored)
    assert want_tips is notifier.tips_enabled(stored)


# ── writing ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["sounds", "tips"])
@pytest.mark.parametrize("value", [True, False])
def test_save_stores_a_true_or_a_false(tmp_path, key, value):
    api, save, get = methods(tmp_path, {"hotkey_keys": ["fn"]})
    r = save(api, {key: value})
    assert r["ok"] is True, r
    assert api.data == {"hotkey_keys": ["fn"], key: value}
    assert get(api)[key] is value


@pytest.mark.parametrize("key,message", [("sounds", um.SOUNDS_UNKNOWN), ("tips", um.TIPS_UNKNOWN)])
@pytest.mark.parametrize("bad", ["true", "false", "", "on", 1, 0, None, [True], {"a": 1}])
def test_save_refuses_anything_else_and_saves_nothing(tmp_path, key, message, bad):
    api, save, _get = methods(tmp_path, {"dialect": "en-GB", key: True})
    r = save(api, {key: bad, "dialect": "en-US"})
    assert r == {"ok": False, "error": message}
    assert api.data == {"dialect": "en-GB", key: True}
    assert api.saves == 0


def test_the_refusals_are_plain_sentences():
    for text in (um.SOUNDS_UNKNOWN, um.TIPS_UNKNOWN):
        assert text.endswith(".") and "—" not in text and "–" not in text
        assert "Exception" not in text and "bool" not in text.lower()


def test_one_switch_does_not_touch_the_other(tmp_path):
    api, save, _get = methods(tmp_path, {"sounds": True, "tips": False})
    assert save(api, {"auto_paste": False})["ok"]
    assert api.data == {"sounds": True, "tips": False, "auto_paste": False}


def test_both_methods_know_both_keys():
    """get_settings and save_settings list their keys explicitly (the roadmap's
    conventions): a key added to one and not the other is a bug."""
    for name in ("get_settings", "save_settings"):
        src = ast.unparse(_method(name))
        assert "'sounds'" in src and "'tips'" in src, name


def test_the_literals_in_get_settings_agree_with_the_modules_helpers():
    src = ast.unparse(_method("get_settings"))
    assert "stored.get('sounds') is True" in src
    assert "stored.get('tips') is not False" in src
    for stored in ({}, {"sounds": True}, {"tips": False}, {"sounds": "x", "tips": "y"}):
        assert (stored.get("sounds") is True) == sounds.sounds_enabled(stored)
        assert (stored.get("tips") is not False) == notifier.tips_enabled(stored)
