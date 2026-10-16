"""Setting: "learn_from_edits", whether the Journal's Edit text offers words for
the Vocabulary (Vocabulary page, "Suggest words from my edits").

On until it is exactly false; it only produces suggestions, and nothing is ever
added without a click. Read with get_settings, written by save_settings, which
refuses anything but a true or a false and saves nothing from a payload it
refuses.
"""
import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import learn_words  # noqa: E402
import privacy_data  # noqa: E402
import user_messages as um  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

_SRC = (ROOT / "app.py").read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)
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


def test_it_is_on_until_set(tmp_path):
    api, _save, get = methods(tmp_path)
    assert get(api)["learn_from_edits"] is True


@pytest.mark.parametrize("stored, want", [
    ({"learn_from_edits": True}, True), ({"learn_from_edits": False}, False),
    ({"learn_from_edits": "false"}, True), ({"learn_from_edits": 0}, True), ({"learn_from_edits": None}, True),
])
def test_only_a_real_false_turns_it_off(tmp_path, stored, want):
    api, _save, get = methods(tmp_path, stored)
    assert get(api)["learn_from_edits"] is want is learn_words.enabled(stored)


@pytest.mark.parametrize("value", [True, False])
def test_save_stores_a_switch(tmp_path, value):
    api, save, get = methods(tmp_path, {"hotkey_keys": ["fn"]})
    r = save(api, {"learn_from_edits": value})
    assert r["ok"] is True, r
    assert api.data == {"hotkey_keys": ["fn"], "learn_from_edits": value}
    assert get(api)["learn_from_edits"] is value
    assert any("Suggest words from edits" in n for n in r["notes"])


@pytest.mark.parametrize("bad", ["false", "true", "", 0, 1, None, [True], {"a": 1}])
def test_save_refuses_anything_else_and_saves_nothing(tmp_path, bad):
    api, save, _get = methods(tmp_path, {"dialect": "en-GB", "learn_from_edits": True})
    r = save(api, {"learn_from_edits": bad, "dialect": "en-US"})
    assert r == {"ok": False, "error": um.LEARN_FROM_EDITS_UNKNOWN}
    assert api.data == {"dialect": "en-GB", "learn_from_edits": True}
    assert api.saves == 0


def test_the_refusal_is_one_plain_sentence():
    text = um.LEARN_FROM_EDITS_UNKNOWN
    assert text.endswith(".") and "—" not in text and "–" not in text
    assert "Exception" not in text


def test_another_setting_does_not_touch_it(tmp_path):
    api, save, _get = methods(tmp_path, {"learn_from_edits": False})
    assert save(api, {"auto_paste": False})["ok"]
    assert api.data == {"learn_from_edits": False, "auto_paste": False}


def test_both_methods_know_the_key():
    """get_settings and save_settings list their keys explicitly (the
    roadmap's conventions): a key added to one and not the other is a bug."""
    for name in ("get_settings", "save_settings"):
        assert "learn_from_edits" in ast.unparse(_method(name)), name


def test_the_logs_bundle_keeps_it_as_it_is_because_it_is_not_secret():
    out = json.loads(privacy_data.settings_for_logs(json.dumps({"learn_from_edits": False, "typing_wpm": 50})))
    assert out == {"learn_from_edits": False, "typing_wpm": 50}
