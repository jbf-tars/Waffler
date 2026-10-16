"""Setting: "double_tap_handsfree", whether tapping the dictation hotkey twice
quickly locks a take on (Settings, Shortcuts; src/double_tap.py), S11b.

Off until it is exactly true. Read with get_settings and get_shortcuts, written
by save_settings, which refuses anything but a true or a false and saves
nothing from a payload it refuses, and applied to the running listener at once.
The real Api and WafflerPipeline methods from app.py run against temporary
folders and fakes.
"""
import ast
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import double_tap  # noqa: E402
import user_messages as um  # noqa: E402
from _pipeline_harness import make_pipeline  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

_SRC = (ROOT / "app.py").read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _lift(name, **ns):
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), ns)
    return ns[name]


class FakePipeline:
    def __init__(self):
        self.applied = 0

    def apply_double_tap(self):
        self.applied += 1


def methods(tmp_path, initial=None, pipeline=None):
    api = settings_api(tmp_path, initial)
    ns = {"os": __import__("os"), "_pipeline": pipeline, "_log_to_file": lambda m: None,
          "CLIPBOARD_CHOICE_UNKNOWN": um.CLIPBOARD_CHOICE_UNKNOWN,
          "SOUNDS_UNKNOWN": um.SOUNDS_UNKNOWN, "TIPS_UNKNOWN": um.TIPS_UNKNOWN,
          "_languages": __import__("languages")}
    return api, _lift("save_settings", **ns), _lift("get_settings", **ns), _lift("get_shortcuts", **ns)


# ── reading ──────────────────────────────────────────────────────────────────

def test_it_is_off_until_set(tmp_path):
    api, _save, get, _page = methods(tmp_path)
    assert get(api)["double_tap_handsfree"] is False


@pytest.mark.parametrize("stored, want", [
    ({"double_tap_handsfree": True}, True), ({"double_tap_handsfree": False}, False),
    ({"double_tap_handsfree": "true"}, False), ({"double_tap_handsfree": 1}, False),
    ({"double_tap_handsfree": None}, False), ({"double_tap_handsfree": [True]}, False),
])
def test_only_a_real_true_turns_it_on(tmp_path, stored, want):
    api, _save, get, page = methods(tmp_path, stored)
    assert get(api)["double_tap_handsfree"] is want is double_tap.enabled(stored)
    assert page(api)["double_tap_handsfree"] is want


def test_the_shortcuts_page_carries_it_and_its_rows_are_as_they_were(tmp_path):
    api, _save, _get, page = methods(tmp_path, {"double_tap_handsfree": True})
    answer = page(api)
    assert answer["ok"] is True and answer["double_tap_handsfree"] is True
    assert [r["id"] for r in answer["rows"]] == ["dictation", "hands_free", "cancel", "transform",
                                                 "command", "paste_last", "copy_last"]
    assert set(answer) == {"ok", "platform", "rows", "rules", "double_tap_handsfree"}


# ── writing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [True, False])
def test_save_stores_a_switch_and_keeps_everything_else(tmp_path, value):
    api, save, get, _page = methods(tmp_path, {"hotkey_keys": ["fn"], "cancel_key": "f9"})
    r = save(api, {"double_tap_handsfree": value})
    assert r["ok"] is True, r
    assert api.data == {"hotkey_keys": ["fn"], "cancel_key": "f9", "double_tap_handsfree": value}
    assert get(api)["double_tap_handsfree"] is value
    assert any("Double-tap" in n for n in r["notes"])


@pytest.mark.parametrize("bad", ["false", "true", "", 0, 1, None, [True], {"a": 1}])
def test_save_refuses_anything_else_and_saves_nothing(tmp_path, bad):
    pipeline = FakePipeline()
    api, save, _get, _page = methods(tmp_path, {"dialect": "en-GB", "double_tap_handsfree": True}, pipeline)
    r = save(api, {"double_tap_handsfree": bad, "dialect": "en-US"})
    assert r == {"ok": False, "error": um.DOUBLE_TAP_UNKNOWN}
    assert api.data == {"dialect": "en-GB", "double_tap_handsfree": True}
    assert api.saves == 0 and pipeline.applied == 0


def test_a_save_of_something_else_leaves_it_alone_and_does_not_touch_the_listener(tmp_path):
    pipeline = FakePipeline()
    api, save, get, _page = methods(tmp_path, {"double_tap_handsfree": True}, pipeline)
    assert save(api, {"auto_paste": False})["ok"] is True
    assert api.data == {"double_tap_handsfree": True, "auto_paste": False}
    assert pipeline.applied == 0


def test_a_save_is_applied_to_the_running_listener_at_once(tmp_path):
    pipeline = FakePipeline()
    api, save, _get, _page = methods(tmp_path, None, pipeline)
    save(api, {"double_tap_handsfree": True})
    save(api, {"double_tap_handsfree": False})
    assert pipeline.applied == 2


def test_a_failure_to_apply_never_fails_the_save(tmp_path):
    class Broken:
        def apply_double_tap(self):
            raise RuntimeError("no listener")

    api, save, _get, _page = methods(tmp_path, None, Broken())
    assert save(api, {"double_tap_handsfree": True})["ok"] is True
    assert api.data == {"double_tap_handsfree": True}


def test_no_pipeline_yet_is_fine(tmp_path):
    api, save, _get, _page = methods(tmp_path, None, None)
    assert save(api, {"double_tap_handsfree": True})["ok"] is True


# ── applying it: the pipeline hands the setting to the listener ──────────────

class FakeListener:
    def __init__(self):
        self.calls = []

    def set_double_tap(self, enabled):
        self.calls.append(enabled)


def _pipeline(tmp_path, stored):
    import json
    p = make_pipeline(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps(stored), encoding="utf-8")
    p.hotkey_listener = FakeListener()
    return p


def test_the_pipeline_gives_the_listener_what_settings_json_says(tmp_path):
    p = _pipeline(tmp_path, {"double_tap_handsfree": True})
    p.apply_double_tap()
    assert p.hotkey_listener.calls == [True]
    (tmp_path / "settings.json").write_text('{"double_tap_handsfree": false}', encoding="utf-8")
    p.apply_double_tap()
    assert p.hotkey_listener.calls == [True, False]


@pytest.mark.parametrize("stored", ['{}', '{"double_tap_handsfree": "true"}', "not json", "[]"])
def test_an_unreadable_or_missing_setting_is_off(tmp_path, stored):
    p = _pipeline(tmp_path, {})
    (tmp_path / "settings.json").write_text(stored, encoding="utf-8")
    p.apply_double_tap()
    assert p.hotkey_listener.calls == [False]


def test_a_listener_without_the_method_or_no_listener_is_not_an_error(tmp_path):
    p = _pipeline(tmp_path, {"double_tap_handsfree": True})
    p.hotkey_listener = types.SimpleNamespace()
    p.apply_double_tap()
    p.hotkey_listener = None
    p.apply_double_tap()


# ── every listener the app makes is given the setting ────────────────────────

def _calls_to(names):
    found = []
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name in names:
                found.append(node)
    return found


def test_the_dictation_listener_is_made_with_the_setting_where_the_app_starts_and_restarts_it():
    """The pipeline's start and Api._restart_hotkey_listener (a change of the
    hotkey or of the cancel key) each make both listeners; the setup wizard's
    test listeners are not the dictation hotkey and are left as they are."""
    calls = _calls_to({"WindowsHotkeyListener", "SmartHotkeyListener"})
    given = [c for c in calls if any(k.arg == "double_tap" for k in c.keywords)]
    assert len(given) == 4, [ast.get_source_segment(_SRC, c)[:60] for c in given]
    for call in given:
        names = {k.arg for k in call.keywords}
        assert {"cancel_key", "on_lock", "double_tap"} <= names
    others = [c for c in calls if c not in given]
    assert len(others) == 4 and all("on_cancel" not in {k.arg for k in c.keywords} for c in others)


def test_both_listeners_can_be_switched_while_they_run():
    for module in ("smart_hotkey.py", "windows_hotkey.py"):
        text = (ROOT / "src" / module).read_text(encoding="utf-8")
        assert "def set_double_tap(self, enabled: bool):" in text, module
        assert "def double_tap_enabled(self) -> bool:" in text, module
        assert "double_tap=False, clock=None" in text, module


def test_the_settings_key_is_the_same_everywhere():
    assert double_tap.SETTING == "double_tap_handsfree"
    assert '"double_tap_handsfree"' in _SRC
