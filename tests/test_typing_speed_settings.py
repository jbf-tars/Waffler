"""Setting: "typing_wpm", how fast the person types (Settings, Usage).

It only feeds the minutes-saved estimate (src/stats_insights.py). Read with
get_settings (40 until set), written by save_settings, which refuses anything
but a whole number from 10 to 200 and saves nothing from a payload it
refuses. The stats read it again on every count, so a change shows at once.
"""
import ast
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import journal_data  # noqa: E402
import stats_insights as si  # noqa: E402
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


def test_the_typing_speed_starts_at_40(tmp_path):
    api, _save, get = methods(tmp_path)
    assert get(api)["typing_wpm"] == 40 == si.TYPING_WPM_DEFAULT


@pytest.mark.parametrize("stored,want", [
    ({"typing_wpm": 65}, 65), ({"typing_wpm": 10}, 10), ({"typing_wpm": 200}, 200),
    ({"typing_wpm": 65.0}, 65),
    ({"typing_wpm": "65"}, 40), ({"typing_wpm": 9}, 40), ({"typing_wpm": 201}, 40),
    ({"typing_wpm": None}, 40), ({"typing_wpm": True}, 40), ({"typing_wpm": 55.5}, 40),
])
def test_only_a_real_speed_in_range_is_read(tmp_path, stored, want):
    api, _save, get = methods(tmp_path, stored)
    assert get(api)["typing_wpm"] == want == si.typing_wpm_of(stored)


@pytest.mark.parametrize("value", [10, 40, 55, 200])
def test_save_stores_a_whole_number_in_range(tmp_path, value):
    api, save, get = methods(tmp_path, {"hotkey_keys": ["fn"]})
    r = save(api, {"typing_wpm": value})
    assert r["ok"] is True, r
    assert api.data == {"hotkey_keys": ["fn"], "typing_wpm": value}
    assert get(api)["typing_wpm"] == value
    assert any("Typing speed" in n for n in r["notes"])


def test_a_whole_number_sent_as_a_float_is_stored_as_an_int(tmp_path):
    api, save, _get = methods(tmp_path)
    assert save(api, {"typing_wpm": 65.0})["ok"] is True
    assert api.data == {"typing_wpm": 65} and isinstance(api.data["typing_wpm"], int)


@pytest.mark.parametrize("bad", [9, 0, -40, 201, 1000, 40.5, "40", "", None, True, False, [40],
                                 {"a": 1}, float("inf")])
def test_save_refuses_anything_else_and_saves_nothing(tmp_path, bad):
    api, save, _get = methods(tmp_path, {"dialect": "en-GB", "typing_wpm": 60})
    r = save(api, {"typing_wpm": bad, "dialect": "en-US"})
    assert r == {"ok": False, "error": um.TYPING_SPEED_UNKNOWN}
    assert api.data == {"dialect": "en-GB", "typing_wpm": 60}
    assert api.saves == 0


def test_the_refusal_is_one_plain_sentence():
    text = um.TYPING_SPEED_UNKNOWN
    assert text.endswith(".") and "—" not in text and "–" not in text
    assert "10" in text and "200" in text and "Exception" not in text


def test_another_setting_does_not_touch_it(tmp_path):
    api, save, _get = methods(tmp_path, {"typing_wpm": 70})
    assert save(api, {"auto_paste": False})["ok"]
    assert api.data == {"typing_wpm": 70, "auto_paste": False}


def test_both_methods_know_the_key():
    """get_settings and save_settings list their keys explicitly (the
    roadmap's conventions): a key added to one and not the other is a bug."""
    for name in ("get_settings", "save_settings"):
        assert "'typing_wpm'" in ast.unparse(_method(name)), name


def _lifted_reader(tmp_path):
    fn = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_typing_wpm_setting")
    ns = {"DATA_DIR": tmp_path, "json": json, "_stats_insights": si}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    return ns["_typing_wpm_setting"]


def test_the_stats_read_the_setting_from_settings_json_each_time(tmp_path):
    read = _lifted_reader(tmp_path)
    assert read() == 40                                     # no file yet
    (tmp_path / "settings.json").write_text(json.dumps({"typing_wpm": 75}), encoding="utf-8")
    assert read() == 75
    (tmp_path / "settings.json").write_bytes(b"\xef\xbb\xbf" + b'{"typing_wpm": 20}')
    assert read() == 20                                     # a hand-edited file with a BOM
    (tmp_path / "settings.json").write_text("{ not json", encoding="utf-8")
    assert read() == 40
    (tmp_path / "settings.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    assert read() == 40
    (tmp_path / "settings.json").write_text(json.dumps({"typing_wpm": 5000}), encoding="utf-8")
    assert read() == 40


def test_the_window_gets_the_estimate_for_the_typing_speed_it_was_asked_with(tmp_path):
    """The path from Settings to the count: save, then the next get_stats."""
    api, save, _get = methods(tmp_path)
    read = _lifted_reader(tmp_path)
    hist = tmp_path / "history.json"
    hist.write_text(json.dumps([{"timestamp": "2026-10-16T09:00:00", "text": "w " * 80}]), encoding="utf-8")
    cache = journal_data.HistoryCache(hist, lambda: json.loads(hist.read_text(encoding="utf-8")),
                                      typing_wpm=read)
    assert cache.stats(date(2026, 10, 16))["minutes_saved"] == 1.5           # at 40 a minute
    assert save(api, {"typing_wpm": 20})["ok"]
    after = cache.stats(date(2026, 10, 16))
    assert after["minutes_saved"] == 3.5 and after["minutes_saved_basis"]["typing_wpm"] == 20
    assert cache.loads == 1                                                  # history was not read again


def test_the_history_cache_in_app_py_is_given_the_quality_log_and_the_setting():
    call = next(n for n in ast.walk(_TREE) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_history_cache" for t in n.targets))
    kw = {k.arg: ast.unparse(k.value) for k in call.value.keywords}
    assert kw["typing_wpm"] == "_typing_wpm_setting"
    assert "quality.jsonl" in kw["quality_path"]
    # get_stats still asks for the day only: the cache adds the rest.
    assert "_history_cache.stats(date.today())" in ast.unparse(_method("get_stats"))
