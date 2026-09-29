"""Housekeeping must never cost the user a dictation, or their clipboard.

Three real failures, all from one Windows log:

* ``os.replace`` onto history.json or usage.json raised "[WinError 5] Access
  is denied" while another handle had the file open. A usage write sits
  BEFORE the paste, so the dictation fell into the generic error handler: no
  paste, "Something went wrong". 6 dictations were lost this way.
* A history write fails AFTER the paste, and the same handler then copied the
  raw transcript over the styled text the user had just pasted.
* Cancelling a recording cleared the clipboard, although Waffler had not put
  anything there yet (23 times in the log).

app.py imports pywebview and audio hardware at module scope, so its functions
are lifted out of the source and run here with fakes, as in
tests/test_usage_pricing.py. No network, no keys, no real clipboard.
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

import atomic_json  # noqa: E402
import privacy_data  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


def _app_defs(*names, cls=None):
    body = _TREE.body
    if cls:
        body = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == cls).body
    nodes = [n for n in body
             if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in names)
             or (isinstance(n, ast.FunctionDef) and n.name in names)]
    ns: dict = {}
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    for n in names:
        assert n in ns, f"{n} not found in app.py"
    return ns


def _method(cls, name):
    klass = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _deny_replace(times):
    """An os.replace that raises WinError 5 the first ``times`` calls."""
    real = os.replace
    calls = []

    def fake(src, dst):
        calls.append(dst)
        if len(calls) <= times:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    return fake, calls


# ── the retried replace ──────────────────────────────────────────────────────

def test_a_briefly_locked_file_is_still_written(tmp_path, monkeypatch):
    fake, calls = _deny_replace(times=2)
    monkeypatch.setattr(atomic_json.os, "replace", fake)
    waits = []
    target = tmp_path / "history.json"
    atomic_json.write_json_atomic(target, [{"text": "café"}], sleep=waits.append)
    assert json.loads(target.read_text(encoding="utf-8")) == [{"text": "café"}]
    assert len(calls) == 3
    assert waits == [0.05, 0.1]
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_file_that_stays_locked_raises_and_leaves_no_temp_file(tmp_path, monkeypatch):
    fake, calls = _deny_replace(times=99)
    monkeypatch.setattr(atomic_json.os, "replace", fake)
    target = tmp_path / "usage.json"
    with pytest.raises(PermissionError):
        atomic_json.write_json_atomic(target, [], sleep=lambda s: None)
    assert len(calls) == atomic_json.REPLACE_ATTEMPTS
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_the_worst_case_wait_is_under_a_second():
    total = sum(atomic_json.REPLACE_BACKOFF_S * 2 ** i
                for i in range(atomic_json.REPLACE_ATTEMPTS - 1))
    assert total < 1.0


def test_other_os_errors_are_not_retried(tmp_path, monkeypatch):
    calls = []

    def fake(src, dst):
        calls.append(dst)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(atomic_json.os, "replace", fake)
    with pytest.raises(OSError):
        atomic_json.write_json_atomic(tmp_path / "x.json", [], sleep=lambda s: None)
    assert len(calls) == 1


# ── app.py's writers use it, and the dictation path cannot be failed ─────────

_BOOK = _app_defs("MODEL_RATES", "_rate_for", "_usage_cost", "ensure_data_dir",
                  "load_usage", "save_usage", "record_usage", "record_usage_safely",
                  "load_history", "save_history", "append_history",
                  "append_history_safely", "_history_retention_day",
                  "_history_keep_days", "_retain_history")


@pytest.fixture
def book(tmp_path, monkeypatch):
    from datetime import datetime
    logged = []
    _BOOK.update(
        json=json, os=os, datetime=datetime, threading=threading,
        write_json_atomic=lambda p, d: atomic_json.write_json_atomic(
            p, d, sleep=lambda s: None),
        DATA_DIR=tmp_path, HISTORY_FILE=tmp_path / "history.json",
        USAGE_FILE=tmp_path / "usage.json", _history_lock=threading.Lock(),
        _log_to_file=logged.append, _privacy=privacy_data,
    )
    _BOOK["logged"] = logged
    return _BOOK


def test_save_usage_survives_a_brief_lock(book, monkeypatch):
    fake, _ = _deny_replace(times=1)
    monkeypatch.setattr(atomic_json.os, "replace", fake)
    book["record_usage"]("whisper", duration_seconds=3.0, provider="groq")
    rows = json.loads(book["USAGE_FILE"].read_text(encoding="utf-8"))
    assert len(rows) == 1 and rows[0]["type"] == "whisper"


def test_a_usage_write_that_keeps_failing_does_not_raise(book, monkeypatch):
    """This call sits before the paste. It returning normally is what lets
    the paste happen."""
    fake, _ = _deny_replace(times=99)
    monkeypatch.setattr(atomic_json.os, "replace", fake)
    assert book["record_usage_safely"]("whisper", duration_seconds=3.0,
                                       provider="groq") is None
    assert any("[usage] not recorded" in m and "PermissionError" in m
               for m in book["logged"])


def test_a_history_write_that_keeps_failing_reports_false(book, monkeypatch):
    fake, _ = _deny_replace(times=99)
    monkeypatch.setattr(atomic_json.os, "replace", fake)
    assert book["append_history_safely"]({"text": "hi"}) is False
    assert any("[history] not saved" in m for m in book["logged"])


def test_a_history_write_that_works_reports_true(book):
    assert book["append_history_safely"]({"text": "hi"}) is True
    assert json.loads(book["HISTORY_FILE"].read_text(encoding="utf-8")) == [{"text": "hi"}]


def _calls_in(node):
    return {c.func.id for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}


def test_the_dictation_pipeline_only_uses_the_safe_bookkeeping():
    process = _method("WafflerPipeline", "_process")
    called = _calls_in(process)
    assert "record_usage" not in called and "append_history" not in called
    assert {"record_usage_safely", "append_history_safely"} <= called


def test_the_error_handler_never_salvages_over_the_styled_text():
    """Every clipboard.copy(transcript) in _process's error handler must be
    guarded by _clipboard_written, which is set once the styled text is on
    the clipboard."""
    process = _method("WafflerPipeline", "_process")
    parent = {}
    for node in ast.walk(process):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    salvages = [c for c in ast.walk(process)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == "copy"
                and any(isinstance(a, ast.Name) and a.id == "transcript" for a in c.args)]
    assert salvages, "no transcript salvage found; update this test"
    for call in salvages:
        node = parent[call]
        while not isinstance(node, ast.If):
            node = parent[node]
        assert "_clipboard_written" in ast.unparse(node.test), ast.unparse(node.test)


# ── cancel leaves the clipboard alone ────────────────────────────────────────

class _Rec:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*a, **k):
            self.calls.append(name)
        return record


def test_cancel_does_not_touch_the_clipboard(monkeypatch):
    ns = _app_defs("_on_overlay_cancel", cls="WafflerPipeline")
    ns.update(notify_js_status=lambda s: None, _log_to_file=lambda m: None)
    copies = []
    fake_pyperclip = types.SimpleNamespace(copy=copies.append, paste=lambda: "mine")
    monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)
    ns["pyperclip"] = fake_pyperclip

    pipeline = types.SimpleNamespace(
        _processing_lock=threading.Lock(), is_recording=True,
        _processing_cancelled=threading.Event(), audio=_Rec(), overlay=_Rec(),
        hotkey_listener=_Rec(), clipboard=_Rec(),
    )
    ns["_on_overlay_cancel"](pipeline)

    assert copies == []
    assert pipeline.clipboard.calls == []
    # It still does its real job.
    assert pipeline.is_recording is False
    assert pipeline._processing_cancelled.is_set()
    assert "stop" in pipeline.audio.calls and "hide" in pipeline.overlay.calls


def test_cancel_when_not_recording_does_not_touch_the_clipboard(monkeypatch):
    ns = _app_defs("_on_overlay_cancel", cls="WafflerPipeline")
    ns.update(notify_js_status=lambda s: None, _log_to_file=lambda m: None)
    copies = []
    monkeypatch.setitem(sys.modules, "pyperclip", types.SimpleNamespace(copy=copies.append))
    pipeline = types.SimpleNamespace(
        _processing_lock=threading.Lock(), is_recording=False,
        _processing_cancelled=threading.Event(), audio=_Rec(), overlay=_Rec(),
        hotkey_listener=_Rec(), clipboard=_Rec(),
    )
    ns["_on_overlay_cancel"](pipeline)
    assert copies == [] and pipeline.clipboard.calls == []
