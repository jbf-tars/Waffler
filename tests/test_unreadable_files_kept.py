"""A file that cannot be read is never replaced by one new entry.

load_history returned [] for a history.json it could not read or parse, and
append_history then saved [the new dictation], replacing the whole Journal.
usage.json (record_usage) and settings.json (any setter, and set_theme ran
at every launch) had the same flaw: one failed read cost the hotkey, the
provider order, private mode and the spelling. A byte order mark was enough
for history.json and usage.json, which were read as plain utf-8.

Now: a file that cannot be parsed is kept aside as
<name>.unreadable-<time>.json before anything new is written, and one that
cannot be opened (locked by antivirus, backup or sync software) stops the
write. Offline, in a temporary folder; app.py's functions are lifted from its
source as in tests/test_bookkeeping_never_fails_dictation.py.
"""
import ast
import json
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import atomic_json  # noqa: E402
import privacy_data  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
OLD = [{"timestamp": "2026-09-01T10:00:00", "text": "one"},
       {"timestamp": "2026-09-02T10:00:00", "text": "two"}]


def _defs(*names):
    nodes = [n for n in _TREE.body
             if (isinstance(n, ast.FunctionDef) and n.name in names)
             or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in names)]
    ns = {}
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    return ns


@pytest.fixture
def app(tmp_path):
    from datetime import datetime
    ns = _defs("MODEL_RATES", "_rate_for", "_usage_cost", "ensure_data_dir",
               "load_history", "_load_history_for_update", "save_history",
               "append_history", "append_history_safely", "load_usage", "save_usage",
               "record_usage", "_history_retention_day", "_history_keep_days",
               "_retain_history")
    logged = []
    ns.update(json=json, os=os, datetime=datetime, threading=threading,
              DATA_DIR=tmp_path, HISTORY_FILE=tmp_path / "history.json",
              USAGE_FILE=tmp_path / "usage.json",
              _history_lock=threading.Lock(), _usage_lock=threading.Lock(),
              write_json_atomic=lambda p, d: atomic_json.write_json_atomic(p, d, sleep=lambda s: None),
              read_json_for_update=atomic_json.read_json_for_update,
              _log_to_file=logged.append, _privacy=privacy_data)
    ns["logged"] = logged
    return ns


def _unreadable(folder, stem):
    return sorted(folder.glob(f"{stem}.unreadable-*.json"))


# ── the helper ───────────────────────────────────────────────────────────────

def test_a_missing_file_is_empty(tmp_path):
    assert atomic_json.read_json_for_update(tmp_path / "x.json", list) == ([], None)
    assert atomic_json.read_json_for_update(tmp_path / "x.json", dict) == ({}, None)


def test_a_byte_order_mark_is_read(tmp_path):
    p = tmp_path / "history.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(OLD).encode("utf-8"))
    assert atomic_json.read_json_for_update(p, list) == (OLD, None)


@pytest.mark.parametrize("raw", [b'[{"text": "cut sh', b"\xff\xfe\x00not utf-8", b'{"a": 1}', b""])
def test_unparseable_or_the_wrong_shape_is_kept_aside(tmp_path, raw):
    p = tmp_path / "history.json"
    p.write_bytes(raw)
    value, kept = atomic_json.read_json_for_update(p, list, stamp="20261005-101500")
    assert value == []
    assert kept == tmp_path / "history.unreadable-20261005-101500.json"
    assert kept.read_bytes() == raw and not p.exists()


def test_a_second_unreadable_file_in_the_same_second_does_not_overwrite_the_first(tmp_path):
    p = tmp_path / "usage.json"
    p.write_bytes(b"first")
    atomic_json.read_json_for_update(p, list, stamp="S")
    p.write_bytes(b"second")
    _v, kept = atomic_json.read_json_for_update(p, list, stamp="S")
    assert kept.name == "usage.unreadable-S-2.json"
    assert (tmp_path / "usage.unreadable-S.json").read_bytes() == b"first"


def test_a_locked_file_raises_and_is_left_alone(tmp_path, monkeypatch):
    p = tmp_path / "history.json"
    p.write_text(json.dumps(OLD), encoding="utf-8")

    def locked(self):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(Path, "read_bytes", locked)
    with pytest.raises(PermissionError):
        atomic_json.read_json_for_update(p, list)
    monkeypatch.undo()
    assert json.loads(p.read_text(encoding="utf-8")) == OLD
    assert not _unreadable(tmp_path, "history")


# ── history.json ─────────────────────────────────────────────────────────────

def test_an_unreadable_journal_is_kept_not_replaced_by_the_next_dictation(app, tmp_path):
    raw = json.dumps(OLD)[:-5].encode("utf-8")           # cut short
    app["HISTORY_FILE"].write_bytes(raw)
    assert app["append_history_safely"]({"timestamp": "2026-10-05T09:00:00", "text": "new"})
    assert json.loads(app["HISTORY_FILE"].read_text(encoding="utf-8")) == [
        {"timestamp": "2026-10-05T09:00:00", "text": "new"}]
    kept = _unreadable(tmp_path, "history")
    assert len(kept) == 1 and kept[0].read_bytes() == raw
    assert any("history.json could not be read" in m for m in app["logged"])


def test_a_journal_with_a_byte_order_mark_keeps_every_entry(app):
    app["HISTORY_FILE"].write_bytes(b"\xef\xbb\xbf" + json.dumps(OLD).encode("utf-8"))
    assert app["load_history"]() == OLD
    assert app["append_history_safely"]({"timestamp": "2026-10-05T09:00:00", "text": "three"})
    saved = json.loads(app["HISTORY_FILE"].read_text(encoding="utf-8"))
    assert [e["text"] for e in saved] == ["one", "two", "three"]


def test_a_locked_journal_is_not_written_and_the_dictation_path_carries_on(app, monkeypatch):
    app["HISTORY_FILE"].write_text(json.dumps(OLD), encoding="utf-8")
    real = Path.read_bytes

    def locked(self):
        if self.name == "history.json":
            raise PermissionError(13, "The process cannot access the file")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    assert app["append_history_safely"]({"text": "new"}) is False
    monkeypatch.undo()
    assert json.loads(app["HISTORY_FILE"].read_text(encoding="utf-8")) == OLD
    assert any("[history] not saved" in m for m in app["logged"])


def test_every_history_rewrite_in_app_py_reads_it_the_safe_way():
    """A save_history after a plain load_history is the old bug again."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        calls = {c.func.id for c in ast.walk(fn)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        if "save_history" in calls and fn.name != "clear_history":
            assert "load_history" not in calls, f"{fn.name} saves after a plain load_history"
            assert "_load_history_for_update" in calls, fn.name


# ── usage.json ───────────────────────────────────────────────────────────────

def test_unreadable_usage_is_kept_and_a_bom_is_read(app, tmp_path):
    app["USAGE_FILE"].write_bytes(b"not json")
    app["record_usage"]("whisper", duration_seconds=3.0, provider="groq")
    rows = json.loads(app["USAGE_FILE"].read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert _unreadable(tmp_path, "usage")[0].read_bytes() == b"not json"

    app["USAGE_FILE"].write_bytes(b"\xef\xbb\xbf" + json.dumps(rows).encode("utf-8"))
    assert app["load_usage"]() == rows
    app["record_usage"]("gpt", input_tokens=10, output_tokens=5, provider="groq")
    assert len(json.loads(app["USAGE_FILE"].read_text(encoding="utf-8"))) == 2


def test_usage_written_from_two_threads_keeps_every_row(app):
    def work():
        for _ in range(10):
            app["record_usage"]("whisper", duration_seconds=1.0, provider="groq")

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(json.loads(app["USAGE_FILE"].read_text(encoding="utf-8"))) == 40


def test_record_usage_holds_the_usage_lock():
    fn = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "record_usage")
    withs = [w for w in ast.walk(fn) if isinstance(w, ast.With)]
    assert any("_usage_lock" in ast.unparse(w.items[0].context_expr) for w in withs)


# ── settings.json ────────────────────────────────────────────────────────────

SETTINGS = {"hotkey_keys": ["ctrl", "win"], "provider_order": ["groq", "openai"],
            "private_mode": True, "dialect": "british", "theme": "dark"}


def test_a_change_keeps_every_other_setting(tmp_path):
    api = settings_api(tmp_path, SETTINGS)
    with api._editing_settings() as s:
        s["theme"] = "auto"
    assert api.data == {**SETTINGS, "theme": "auto"}


def test_unreadable_settings_are_kept_aside(tmp_path):
    api = settings_api(tmp_path)
    (tmp_path / "settings.json").write_bytes(b'{"hotkey_keys": ["ctrl",')
    with api._editing_settings() as s:
        s["theme"] = "dark"
    assert api.data == {"theme": "dark"}
    kept = _unreadable(tmp_path, "settings")
    assert len(kept) == 1 and kept[0].read_bytes() == b'{"hotkey_keys": ["ctrl",'
    assert any("settings.json could not be read" in m for m in api.logged)


def test_locked_settings_are_not_written_over(tmp_path, monkeypatch):
    api = settings_api(tmp_path, SETTINGS)
    real = Path.read_bytes

    def locked(self):
        if self.name == "settings.json":
            raise PermissionError(13, "The process cannot access the file")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    with pytest.raises(PermissionError):
        with api._editing_settings() as s:
            s["theme"] = "cream"
    monkeypatch.undo()
    assert api.data == SETTINGS and api.saves == 0


def test_a_block_that_raises_saves_nothing(tmp_path):
    api = settings_api(tmp_path, SETTINGS)
    with pytest.raises(RuntimeError):
        with api._editing_settings() as s:
            s["theme"] = "cream"
            raise RuntimeError("half way")
    assert api.data == SETTINGS and api.saves == 0


def test_settings_changed_from_many_threads_keep_every_change(tmp_path):
    api = settings_api(tmp_path, {})

    def set_one(i):
        with api._editing_settings() as s:
            s[f"k{i}"] = i

    threads = [threading.Thread(target=set_one, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert api.data == {f"k{i}": i for i in range(24)}


def test_settings_writes_retry_a_brief_lock(tmp_path, monkeypatch):
    api = settings_api(tmp_path, SETTINGS)
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 1:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(atomic_json.os, "replace", flaky)
    with api._editing_settings() as s:
        s["theme"] = "auto"
    assert len(calls) == 2 and api.data["theme"] == "auto"


def test_no_setter_in_app_py_saves_after_a_plain_read():
    api = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    for fn in api.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name in ("_editing_settings",):
            continue
        calls = {c.func.attr for c in ast.walk(fn)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
        assert "_save_settings_file" not in calls, f"{fn.name} saves outside _editing_settings"


# ── Delete all my data ───────────────────────────────────────────────────────

def test_delete_all_my_data_removes_the_kept_copies(tmp_path):
    for name in ("history.unreadable-20261005-101500.json", "usage.unreadable-X.json"):
        (tmp_path / name).write_text("[]", encoding="utf-8")
    (tmp_path / "settings.unreadable-X.json").write_text("{}", encoding="utf-8")
    assert privacy_data.delete_my_data(tmp_path)["ok"]
    assert not _unreadable(tmp_path, "history") and not _unreadable(tmp_path, "usage")
    # Settings stay with Delete all my data, as settings.json does.
    assert (tmp_path / "settings.unreadable-X.json").exists()
