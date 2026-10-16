"""Transforms through the bridge, and what they do to the Journal's numbers (S7).

app.py's Api methods are lifted out of its source and run against a temporary
settings.json (tests/_settings_fake.py): no window, no keys. The data parts
(stats, search, export, "Paste last", the logs bundle, usage) call the real
functions with Journal entries as plain dicts.
"""
import ast
import json
import os
import sys
import threading
import types
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import history_export  # noqa: E402
import history_meta  # noqa: E402
import journal_data  # noqa: E402
import pasteboard  # noqa: E402
import privacy_data  # noqa: E402
import transforms as tf  # noqa: E402
import user_messages as um  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _method(name):
    return next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)


@pytest.fixture(autouse=True)
def _as_a_mac(monkeypatch):
    """The bridge asks which computer this is (src/transforms.py): these tests
    use Mac key names, so they run as a Mac on every runner."""
    monkeypatch.setattr(tf, "_this_platform", lambda: "Darwin")


def _lift(name, **extra):
    ns = {"os": os, "json": json, "threading": threading, "time": __import__("time"),
          "_pipeline": None, "_log_to_file": lambda m: None,
          "_platform": types.SimpleNamespace(system=lambda: "Darwin"),
          "CLIPBOARD_CHOICE_UNKNOWN": um.CLIPBOARD_CHOICE_UNKNOWN, "SOUNDS_UNKNOWN": um.SOUNDS_UNKNOWN,
          "TIPS_UNKNOWN": um.TIPS_UNKNOWN, "_languages": __import__("languages"),
          "_transforms": tf}
    ns.update(extra)
    exec(compile(ast.Module([_method(name)], []), "<app.py Api>", "exec"), ns)
    return ns[name]


def methods(tmp_path, initial=None, **extra):
    api = settings_api(tmp_path, initial)
    save, get = _lift("save_settings", **extra), _lift("get_settings", **extra)
    api.save_settings = lambda payload: save(api, payload)
    return api, save, get


# ── reading ──────────────────────────────────────────────────────────────────

def test_the_defaults_are_off_with_no_shortcut_and_polish(tmp_path):
    api, _save, get = methods(tmp_path)
    t = get(api)["transforms"]
    assert t["enabled"] is False and t["hotkey"] == [] and t["hotkey_display"] == ""
    assert t["default"] == "polish" and t["custom"] == []
    assert t["skeleton_default"] == tf.DEFAULT_SKELETON
    assert t["polish"]["rules"] == {r.key: r.default for r in tf.POLISH_RULES}


@pytest.mark.parametrize("junk", [None, 5, "x", [], {"enabled": 1, "polish": 7, "custom": "no"}])
def test_a_hand_edited_file_reads_as_the_defaults(tmp_path, junk):
    api, _save, get = methods(tmp_path, {"transforms": junk})
    t = get(api)["transforms"]
    assert t["enabled"] is False and t["default"] == "polish" and t["custom"] == []


def test_the_shortcut_is_shown_as_the_platform_names_it(tmp_path):
    api, _save, get = methods(tmp_path, {"transforms": {"enabled": True, "hotkey": ["control", "option", "t"]}})
    t = get(api)["transforms"]
    assert t["hotkey"] == ["control", "option", "t"] and t["hotkey_display"]


# ── writing ──────────────────────────────────────────────────────────────────

def test_save_keeps_the_switch_and_what_was_there(tmp_path):
    api, save, get = methods(tmp_path, {"hotkey_keys": ["fn"], "language": "en"})
    assert save(api, {"transforms": {"enabled": True}})["ok"] is True
    assert api.data["hotkey_keys"] == ["fn"] and api.data["transforms"]["enabled"] is True
    assert save(api, {"transforms": {"polish": {"rules": {"concise": True}}}})["ok"] is True
    t = get(api)["transforms"]
    assert t["enabled"] is True and t["polish"]["rules"]["concise"] is True
    assert save(api, {"auto_paste": False})["ok"] is True, "other saves leave it alone"
    assert get(api)["transforms"] == t


def test_the_notes_never_carry_the_persons_wording(tmp_path):
    api, save, _get = methods(tmp_path)
    r = save(api, {"transforms": {"polish": {"custom_rules": ["Mention the Hendricks account"]},
                                  "custom": [{"name": "Hendricks reply", "prompt": "Reply to Hendricks firmly."}]}})
    assert r["ok"] is True and "Hendricks" not in json.dumps(r)
    assert r["notes"] == ["Transforms: off"]


@pytest.mark.parametrize("payload,message", [
    ({"surprise": 1}, um.TRANSFORMS_BAD),
    ({"enabled": "yes"}, um.TRANSFORMS_BAD),
    ({"polish": {"rules": {"made_up": True}}}, um.TRANSFORM_RULE_UNKNOWN),
    ({"polish": {"custom_rules": ["x" * 161]}}, um.TRANSFORM_RULE_LONG),
    ({"custom": [{"name": "A", "prompt": "p"}, {"name": "a", "prompt": "q"}]}, um.TRANSFORM_NAME_TAKEN),
    ({"custom": [{"name": f"T{i}", "prompt": "p"} for i in range(6)]}, um.TRANSFORMS_TOO_MANY),
    ({"default": "gone"}, um.TRANSFORM_UNKNOWN),
])
def test_a_refused_value_saves_nothing_from_the_payload(tmp_path, payload, message):
    start = {"dialect": "en-GB", "transforms": {"enabled": True}}
    api, save, _get = methods(tmp_path, start)
    r = save(api, {"transforms": payload, "dialect": "en-US", "auto_paste": False})
    assert r == {"ok": False, "error": message}
    assert api.data == start and api.saves == 0


def test_the_shortcut_is_refused_when_it_overlaps_the_dictation_hotkey(tmp_path):
    start = {"hotkey_keys": ["cmd", "shift"]}
    api, save, _get = methods(tmp_path, start)
    r = save(api, {"transforms": {"hotkey": ["cmd", "shift", "t"]}})
    assert r["ok"] is False and "overlaps your dictation hotkey" in r["error"]
    assert api.data == start
    assert save(api, {"transforms": {"hotkey": ["control", "option", "t"]}})["ok"] is True


def test_a_save_tells_the_running_app_so_the_shortcut_changes_at_once(tmp_path):
    told = []
    pipeline = types.SimpleNamespace(apply_transform_settings=lambda: told.append(1))
    api, save, _get = methods(tmp_path, _pipeline=pipeline)
    save(api, {"transforms": {"enabled": True}})
    save(api, {"auto_paste": False})
    assert told == [1], "only a save that names Transforms"
    failing = types.SimpleNamespace(apply_transform_settings=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    api2, save2, _ = methods(tmp_path / "two", _pipeline=failing)
    assert save2(api2, {"transforms": {"enabled": True}})["ok"] is True, "a failure to apply is not a failed save"


def test_a_save_before_the_app_is_ready_is_fine(tmp_path):
    api, save, _get = methods(tmp_path)
    assert save(api, {"transforms": {"enabled": True}})["ok"] is True


def test_the_settings_are_written_through_the_editing_helper_only():
    src = ast.unparse(_method("save_settings"))
    assert "_editing_settings" in src and "_save_settings_file" not in src
    assert "'transforms'" in ast.unparse(_method("get_settings"))


# ── the shortcut's own call ──────────────────────────────────────────────────

def hotkey_methods(tmp_path, initial=None):
    api, save, _get = methods(tmp_path, initial)
    api.save_transform_hotkey = types.MethodType(_lift("save_transform_hotkey"), api)
    return api


def test_saving_the_shortcut_answers_like_the_dictation_hotkey_does(tmp_path):
    api = hotkey_methods(tmp_path, {"transforms": {"enabled": True}})
    r = api.save_transform_hotkey(["t", "option", "control"])
    assert r["ok"] is True and r["keys"] == ["control", "option", "t"]
    assert r["display"] == "Control + Option + T"
    assert api.data["transforms"]["hotkey"] == ["control", "option", "t"]
    r = api.save_transform_hotkey(json.dumps(["control", "shift", "y"]))
    assert r["ok"] is True and r["keys"] == ["control", "shift", "y"]


def test_clearing_the_shortcut_with_an_empty_list(tmp_path):
    api = hotkey_methods(tmp_path, {"transforms": {"hotkey": ["control", "option", "t"]}})
    assert api.save_transform_hotkey([]) == {"ok": True, "keys": [], "display": ""}
    assert api.data["transforms"]["hotkey"] == []
    assert api.save_transform_hotkey(None)["ok"] is True


@pytest.mark.parametrize("keys,fragment", [
    (["t"], "Control or Command"),
    (["control", "option"], "Add a letter"),
    (["fn", "control", "t"], "Fn"),
    (["control", "cmd", "q"], "already use"),
    ("not json", "Hold the keys"),
    (["control", "option", "t", "y"], "one letter"),
])
def test_a_shortcut_that_cannot_be_used_says_why_in_one_sentence(tmp_path, keys, fragment):
    api = hotkey_methods(tmp_path, {"hotkey_keys": ["fn"]})
    r = api.save_transform_hotkey(keys)
    assert r["ok"] is False and fragment in r["error"], r
    assert "transforms" not in api.data


def test_a_dictation_hotkey_that_overlaps_the_shortcut_is_refused(tmp_path, monkeypatch):
    fn = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "save_hotkey_config")
    ns = {"json": json, "threading": threading, "time": __import__("time"), "_pipeline": None,
          "_log_to_file": lambda m: None, "_platform": types.SimpleNamespace(system=lambda: "Darwin")}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    keys = {k: 0 for k in "abcdefghijklmnopqrstuvwxyz"} | {"space": 0}
    monkeypatch.setitem(sys.modules, "mac_hotkey_monitor", types.SimpleNamespace(
        KEY_TO_KEYCODE=keys, MODIFIER_FLAGS={k: 0 for k in ("cmd", "shift", "option", "control", "fn")}))
    api = settings_api(tmp_path, {"transforms": {"enabled": True, "hotkey": ["control", "option", "t"]}})
    r = ns["save_hotkey_config"](api, ["control", "option"])
    assert r["ok"] is False and "Control + Option + T" in r["error"]
    assert "hotkey_keys" not in api.data
    ok = ns["save_hotkey_config"](api, ["cmd", "shift"])
    assert ok["ok"] is True and api.data["hotkey_keys"] == ["cmd", "shift"]


# ── the logs bundle ──────────────────────────────────────────────────────────

def test_the_persons_wording_is_taken_out_of_the_logs_copy():
    raw = json.dumps({"transforms": {
        "enabled": True, "hotkey": ["control", "option", "t"], "default": "c1",
        "polish": {"rules": {"grammar": True, "concise": False}, "custom_rules": ["Mention the Hendricks deal"]},
        "prompt_engineer": {"skeleton": "# Ask about Hendricks\n(what)"},
        "custom": [{"id": "c1", "name": "Hendricks reply", "prompt": "Reply firmly to Hendricks."}]}})
    out = privacy_data.settings_for_logs(raw)
    assert "Hendricks" not in out
    t = json.loads(out)["transforms"]
    assert t["enabled"] is True and t["hotkey"] == ["control", "option", "t"] and t["default"] == "c1"
    assert t["polish_rules"] == {"grammar": True, "concise": False} and t["polish_own_rules"] == 1
    assert t["outline_chars"] == len("# Ask about Hendricks\n(what)")
    assert t["custom"] == [{"id": "c1", "prompt_chars": len("Reply firmly to Hendricks.")}]


@pytest.mark.parametrize("junk", [5, "x", [], {"polish": 4, "custom": "no", "prompt_engineer": []}])
def test_odd_transform_settings_do_not_break_the_logs_copy(junk):
    out = privacy_data.settings_for_logs(json.dumps({"transforms": junk}))
    assert json.loads(out) is not None


# ── the Journal's numbers ────────────────────────────────────────────────────

TODAY = date(2026, 10, 16)


def dictation(ts, words=10):
    return {"timestamp": ts, "text": " ".join(["w"] * words), "styled": " ".join(["W"] * words),
            "word_count": words, "id": 1}


def rewrite(ts, before="a b c", after="A b c d e"):
    return tf.history_entry(tf.spec_for({}), before, after, timestamp=ts, provider="groq", took_s=2.0)


def test_a_rewrite_is_in_the_journal_and_in_none_of_the_dictation_numbers():
    base = [dictation("2026-10-16T09:00:00", 10), dictation("2026-10-15T09:00:00", 20)]
    plain = journal_data.compute_stats(base, TODAY)
    with_rewrites = journal_data.compute_stats(
        base + [rewrite("2026-10-16T10:00:00"), rewrite("2026-10-14T10:00:00", after="x " * 500),
                rewrite("2026-10-13T10:00:00")], TODAY)
    for key in plain:
        if key in ("entries", "transform_count"):
            continue
        assert with_rewrites[key] == plain[key], key
    assert plain["entries"] == 2 and plain["transform_count"] == 0
    assert with_rewrites["entries"] == 5 and with_rewrites["transform_count"] == 3


def test_a_day_with_only_a_rewrite_does_not_extend_the_streak():
    yesterday_dictation = [dictation("2026-10-15T09:00:00")]
    stats = journal_data.compute_stats(yesterday_dictation + [rewrite("2026-10-16T10:00:00")], TODAY)
    assert stats["streak_days"] == 1 and stats["today_count"] == 0 and stats["today_words"] == 0
    only = journal_data.compute_stats([rewrite("2026-10-16T10:00:00")], TODAY)
    assert only["streak_days"] == 0 and only["total_words"] == 0 and only["entries"] == 1
    gap = [dictation("2026-10-13T09:00:00"), rewrite("2026-10-14T10:00:00"), dictation("2026-10-15T09:00:00")]
    assert journal_data.compute_stats(gap, TODAY)["longest_streak_days"] == 1


def test_the_cache_reports_the_same(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps([dictation("2026-10-16T09:00:00"), rewrite("2026-10-16T10:00:00")]), encoding="utf-8")
    cache = journal_data.HistoryCache(path, lambda: json.loads(path.read_text(encoding="utf-8")))
    s = cache.stats(TODAY)
    assert s["entries"] == 2 and s["transform_count"] == 1 and s["today_count"] == 1 and s["today_words"] == 10


def test_search_finds_a_rewrite_by_its_result_or_its_original():
    items = [dictation("2026-10-16T09:00:00"), rewrite("2026-10-16T10:00:00", before="milk and eggs", after="Milk, eggs")]
    assert len(journal_data.page(items, query="eggs")) == 1
    assert journal_data.page(items, query="MILK AND")[0]["kind"] == "transform"
    assert journal_data.page(items, query="cheese") == []
    assert [i.get("kind") for i in journal_data.page(items)] == ["transform", None]


def test_a_rewrite_is_never_the_last_transcript():
    last = pasteboard.LastResult(history_loader=lambda: [dictation("2026-10-16T09:00:00"), rewrite("2026-10-16T10:00:00")])
    assert last.get() == " ".join(["W"] * 10)
    only = pasteboard.LastResult(history_loader=lambda: [rewrite("2026-10-16T10:00:00")])
    assert only.get() == "" and only.word_count() == 0


def test_the_words_of_a_rewrite_are_its_result():
    assert history_meta.words_of(rewrite("2026-10-16T10:00:00", after="Done here")) == "Done here"
    assert history_meta.words_of(dictation("2026-10-16T09:00:00")) == " ".join(["W"] * 10)


def test_export_labels_a_rewrite_and_writes_its_result():
    items = [dictation("2026-10-16T09:00:00"), rewrite("2026-10-16T10:00:00", after="Done, here")]
    items[1]["app"] = "Notes"
    txt = history_export.render_txt(items, now=datetime(2026, 10, 16, 12, 0))
    assert "2026-10-16 10:00 (Notes), rewritten with Polish\nDone, here" in txt
    csv = history_export.render_csv(items).splitlines()
    assert len(csv) == 3 and "Done, here" in csv[2]
    data = json.loads(history_export.render_json(items))
    assert data[1]["kind"] == "transform" and data[1]["before"] == "a b c" and data[1]["after"] == "Done, here"


def test_retention_prunes_rewrites_like_any_entry_and_keeps_none_it_should_not():
    old = rewrite("2026-01-01T10:00:00")
    new = rewrite("2026-10-15T10:00:00")
    kept, removed = privacy_data.prune_history([old, new], 30, now=datetime(2026, 10, 16, 12, 0))
    assert kept == [new] and removed == 1


def test_delete_all_my_data_takes_the_rewrites_with_the_history(tmp_path):
    from atomic_json import write_json_atomic
    write_json_atomic(tmp_path / "history.json", [dictation("2026-10-16T09:00:00"), rewrite("2026-10-16T10:00:00")])
    assert privacy_data.delete_my_data(tmp_path)["ok"] is True
    assert json.loads((tmp_path / "history.json").read_text(encoding="utf-8")) == []


# ── the usage record ─────────────────────────────────────────────────────────

def _usage_ns():
    ns = {}
    nodes = [n for n in _TREE.body if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in
                                       ("MODEL_RATES", "_RATES_CHECKED"))
             or (isinstance(n, ast.FunctionDef) and n.name in ("_rate_for", "_usage_cost", "_entry_rate_is_estimate",
                                                               "record_usage"))]
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    saved = []
    ns.update(datetime=datetime, USAGE_FILE=None, USAGE_LOG_FILE=None, ensure_data_dir=lambda: None,
              _usage_lock=threading.Lock(), _log_to_file=lambda m: None,
              _usage_log=types.SimpleNamespace(try_import_legacy=lambda *a: None,
                                               append=lambda path, row: saved.append(row)))
    return ns, saved


def test_a_rewrites_usage_row_is_priced_like_any_call_and_tagged():
    ns, saved = _usage_ns()
    plain = ns["record_usage"]("gpt", input_tokens=1000, output_tokens=500, provider="groq")
    tagged = ns["record_usage"]("gpt", input_tokens=1000, output_tokens=500, provider="groq", purpose="transform")
    assert "purpose" not in plain and tagged["purpose"] == "transform"
    assert tagged["cost_usd"] == plain["cost_usd"] > 0
    assert tagged["type"] == "gpt" and tagged["model"] == plain["model"]


def test_the_cost_per_dictation_leaves_out_what_rewrites_cost_but_every_total_keeps_it():
    ns = {}
    method = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "get_usage_stats")
    exec(compile(ast.Module([method], []), "<app.py>", "exec"), ns)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    rows = [
        {"timestamp": now, "type": "whisper", "provider": "groq", "cost_usd": 0.01, "duration_seconds": 5},
        {"timestamp": now, "type": "gpt", "provider": "groq", "cost_usd": 0.01, "input_tokens": 10, "output_tokens": 5},
        {"timestamp": now, "type": "gpt", "provider": "groq", "cost_usd": 0.04, "purpose": "transform",
         "input_tokens": 90, "output_tokens": 50},
    ]
    ns.update(datetime=datetime, load_usage=lambda: rows, _entry_rate_is_estimate=lambda e: False)
    stats = ns["get_usage_stats"](None)
    assert stats["total_cost_usd"] == pytest.approx(0.06)
    assert stats["today_cost_usd"] == pytest.approx(0.06)
    assert stats["avg_cost_per_transcription"] == pytest.approx(0.02), "(0.06 - 0.04) / 1 dictation"
    assert stats["transform_count"] == 1 and stats["transform_cost_usd"] == pytest.approx(0.04)
    assert stats["transcription_count"] == 1
    assert stats["by_provider"]["groq"]["cost_usd"] == pytest.approx(0.06)
