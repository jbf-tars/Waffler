"""History details (S4): ids, the facts kept with each entry, and paging.

New Journal entries get an integer id and optional facts about their take
(length, speech, language, who heard and tidied it, what became of the paste,
the app, the kept recording). Entries saved before are not changed and every
reader treats each new field as optional. The real pipeline methods from
app.py run against the fakes in tests/_pipeline_harness.py.
"""
import json
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import history_ids  # noqa: E402
import history_meta  # noqa: E402
import journal_data  # noqa: E402
import pasteboard  # noqa: E402
import recent_audio  # noqa: E402
from _pipeline_harness import (  # noqa: E402
    FakeClipboard, FakeStyler, FakeTranscriber, fast_limits, history, make_pipeline,
    run_process, speech_wav,
)


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    fast_limits(monkeypatch)


def _dictate(p):
    worker = run_process(p)
    worker.join(5)
    assert not worker.is_alive()
    return history(p)[-1]


# ── ids ──────────────────────────────────────────────────────────────────────

def test_each_new_entry_gets_the_next_whole_number(tmp_path):
    p = make_pipeline(tmp_path)
    first, second = _dictate(p), _dictate(p)
    assert (first["id"], second["id"]) == (1, 2)
    assert all(type(e["id"]) is int for e in history(p))


def test_the_window_is_told_about_the_entry_with_its_id(tmp_path):
    p = make_pipeline(tmp_path)
    _dictate(p)
    assert p.page.items[-1]["id"] == history(p)[-1]["id"]


def test_ids_carry_on_after_a_restart_from_the_highest_in_the_file(tmp_path):
    p = make_pipeline(tmp_path)
    (tmp_path / "history.json").write_text(json.dumps([
        {"timestamp": "2026-09-01T10:00:00", "text": "old"},          # before ids
        {"timestamp": "2026-10-01T10:00:00", "text": "a", "id": 40},
        {"timestamp": "2026-10-02T10:00:00", "text": "b", "id": 41},
    ]), encoding="utf-8")
    history_ids._floor = 0                                              # a new run
    assert _dictate(p)["id"] == 42


def test_an_id_is_not_given_out_again_after_its_entry_was_deleted(tmp_path):
    p = make_pipeline(tmp_path)
    _dictate(p)
    newest = _dictate(p)
    f = tmp_path / "history.json"
    f.write_text(json.dumps([e for e in history(p) if e["id"] != newest["id"]]), encoding="utf-8")
    assert _dictate(p)["id"] == newest["id"] + 1


def test_ids_stay_unique_with_writers_on_several_threads(tmp_path):
    p = make_pipeline(tmp_path)
    ns = p.ns

    def work():
        for i in range(10):
            ns["append_history"]({"timestamp": "2026-10-16T10:00:00", "text": f"t{i}"})

    threads = [threading.Thread(target=work) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [e["id"] for e in history(p)]
    assert len(ids) == 50 and len(set(ids)) == 50 and ids == sorted(ids)


def test_a_not_sent_card_and_the_entry_it_becomes_keep_one_id(tmp_path):
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(error=ConnectionError("Connection error.")))
    card = _dictate(p)
    assert card["failed"] and card["id"] == 1
    p.transcriber.error = None
    result = p.resend_unsent(card["unsent_id"])
    assert result["ok"] and result["item"]["id"] == 1 and "failed" not in result["item"]
    assert [e["id"] for e in history(p)] == [1]


def test_the_entry_saved_after_an_error_has_an_id_and_its_recording(tmp_path):
    """Something fails after the words exist: the error handler puts them in
    the Journal, and that entry is as findable as any other."""
    p = make_pipeline(tmp_path)
    p._press_app = (1, "Notes")
    p.ns["_refresh_tray_menu"] = lambda: (_ for _ in ()).throw(RuntimeError("icon gone"))
    entry = _dictate(p)
    assert entry["id"] == 1 and entry["app"] == "Notes" and entry["audio"].startswith("rec-")
    assert entry["duration_s"] == pytest.approx(3.0, abs=0.1)
    assert "paste" not in entry          # the paste step never ran


def test_id_helpers():
    assert history_ids.is_id(5) and not history_ids.is_id(0) and not history_ids.is_id(True)
    assert not history_ids.is_id("5") and not history_ids.is_id(5.0) and not history_ids.is_id(None)
    assert history_ids.parse("12") == 12 and history_ids.parse(12) == 12
    assert history_ids.parse("1.5") is None and history_ids.parse("-3") is None
    assert history_ids.parse("") is None and history_ids.parse(None) is None
    assert history_ids.parse(True) is None and history_ids.parse("٣") is None
    assert history_ids.highest([{"id": 3}, {"id": "9"}, {"x": 1}, "junk", {"id": True}]) == 3
    item = {"id": 7}
    assert history_ids.stamp(item, [{"id": 99}])["id"] == 7        # kept
    assert history_ids.stamp({"id": "x"}, [{"id": 99}])["id"] > 99


# ── the facts kept with a take ───────────────────────────────────────────────

def test_a_dictation_keeps_its_length_speech_language_providers_paste_and_recording(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"language": "fr"}), encoding="utf-8")
    p = make_pipeline(tmp_path)
    p._press_app = (1, "Mail")
    entry = _dictate(p)
    assert entry["duration_s"] == pytest.approx(3.0, abs=0.1)
    assert entry["speech_s"] == 3.0
    assert entry["lang"] == "fr"
    assert entry["provider"] == {"stt": "groq", "cleanup": "groq"}
    assert entry["paste"] == "pasted"
    assert entry["app"] == "Mail"
    assert entry["audio"].startswith("rec-") and entry["audio"].endswith(".wav")
    assert (tmp_path / "debug_audio" / entry["audio"]).is_file()


def test_the_app_is_the_one_in_front_when_that_press_began(tmp_path):
    p = make_pipeline(tmp_path)
    p._press_app = (99, "Slack")            # a newer press than the one processed
    assert "app" not in _dictate(p)


def test_what_is_not_known_is_left_out(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"keep_recent_audio": False}), encoding="utf-8")
    p = make_pipeline(tmp_path)
    entry = _dictate(p)
    assert "audio" not in entry and "app" not in entry
    assert entry["lang"] == "en"            # the default is the setting at the time


@pytest.mark.parametrize("outcome,label", [
    (pasteboard.PasteOutcome(pasteboard.UNVERIFIED), "unverified"),
    (pasteboard.PasteOutcome(pasteboard.FAILED, "accessibility_off", on_clipboard=True), "failed"),
])
def test_the_paste_outcome_is_kept(tmp_path, outcome, label):
    p = make_pipeline(tmp_path, clipboard=FakeClipboard(outcome=outcome))
    assert _dictate(p)["paste"] == label


def test_with_auto_paste_off_the_words_were_copied(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"auto_paste": False}), encoding="utf-8")
    p = make_pipeline(tmp_path)
    assert _dictate(p)["paste"] == "copied"


def test_a_failed_clipboard_write_is_a_failed_paste(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"auto_paste": False}), encoding="utf-8")
    p = make_pipeline(tmp_path, clipboard=FakeClipboard(copy_ok=False))
    assert _dictate(p)["paste"] == "failed"


def test_the_clean_up_that_ran_is_the_one_named(tmp_path):
    p = make_pipeline(tmp_path, styler=FakeStyler(fallback_reason="CONNECTION|x"))
    assert _dictate(p)["provider"] == {"stt": "groq", "cleanup": "basic_clean"}


def test_try_again_adds_the_facts_it_knows(tmp_path):
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(error=ConnectionError("Connection error.")))
    card = _dictate(p)
    assert card["duration_s"] == pytest.approx(3.0, abs=0.1) and card["audio"].startswith("rec-")
    p.transcriber.error = None
    item = p.resend_unsent(card["unsent_id"])["item"]
    assert item["provider"] == {"stt": "groq", "cleanup": "groq"}
    assert item["speech_s"] == 3.0 and item["lang"] == "en"
    assert item["audio"] == card["audio"] and item["duration_s"] == card["duration_s"]


def test_take_fields_leaves_out_anything_unknown_or_odd():
    f = history_meta.take_fields
    assert f() == {}
    assert f(audio_seconds=0, speech_seconds=-1, language="", stt="", cleanup=None,
             paste="nonsense", app="  ", audio="") == {}
    assert f(audio_seconds=float("nan"), speech_seconds=float("inf")) == {}
    assert f(audio_seconds="4.26") == {"duration_s": 4.3}
    assert f(stt="groq") == {"provider": {"stt": "groq"}}
    assert f(app="  Visual   Studio\nCode ") == {"app": "Visual Studio Code"}
    assert len(f(app="x" * 500)["app"]) == history_meta.APP_NAME_MAX
    assert f(audio=7) == {}


def test_old_entries_without_any_of_this_still_read_and_count(tmp_path):
    old = {"timestamp": "2026-09-01T10:00:00", "text": "one two", "styled": "One two.",
           "word_count": 2, "text_is": "asr_filtered"}
    assert journal_data.page([old]) == [old]
    assert journal_data.compute_stats([old], __import__("datetime").date(2026, 9, 1))["today_words"] == 2
    assert history_meta.words_of(old) == "One two."


# ── paging by id ─────────────────────────────────────────────────────────────

def _entries(n, start=1):
    return [{"timestamp": f"2026-10-{i:02d}T10:00:00", "text": f"entry {i}", "styled": f"Entry {i}.",
             "id": i} for i in range(start, start + n)]


def test_paging_before_an_id_gives_the_older_entries_newest_first():
    h = _entries(10)
    page = journal_data.page(h, 3, 0, "", before_id=8)
    assert [e["id"] for e in page] == [7, 6, 5]
    assert [e["id"] for e in journal_data.page(h, 3, 0, "", before_id=5)] == [4, 3, 2]
    assert [e["id"] for e in journal_data.page(h, 3, 0, "", before_id=2)] == [1]
    assert journal_data.page(h, 3, 0, "", before_id=1) == []


def test_before_an_id_that_was_deleted_still_works():
    h = [e for e in _entries(10) if e["id"] != 6]
    assert [e["id"] for e in journal_data.page(h, 2, 0, "", before_id=6)] == [5, 4]


def test_entries_without_an_id_are_older_than_every_id():
    old = [{"timestamp": f"2026-09-0{i}T10:00:00", "text": f"old {i}"} for i in range(1, 4)]
    h = old + _entries(2)
    assert [e.get("text") for e in journal_data.page(h, None, 0, "", before_id=1)] == ["old 3", "old 2", "old 1"]
    assert [e.get("id") for e in journal_data.page(h, None, 0, "", before_id=2)] == [1, None, None, None]


def test_before_id_with_a_search_and_the_old_offset_callers_are_unchanged():
    h = _entries(10)
    assert [e["id"] for e in journal_data.page(h, 5, 0, "entry", before_id=5)] == [4, 3, 2, 1]
    assert [e["id"] for e in journal_data.page(h, 2, 3)] == [7, 6]                 # offset still works
    assert [e["id"] for e in journal_data.page(h, 2, 0, "entry 9")] == [9]
    assert journal_data.page(h, 3, 0, "", before_id="x") == journal_data.page(h, 3, 0, "")
    assert journal_data.page(h, 3, 0, "", before_id=0) == journal_data.page(h, 3, 0, "")


def test_a_page_stays_right_when_entries_change_between_pages():
    h = _entries(20)
    first = journal_data.page(h, 5, 0, "")                      # 20..16
    del h[-1]                                                    # a dictation deleted elsewhere
    h.append({"timestamp": "2026-10-30T10:00:00", "text": "new", "id": 21})   # and one added
    after = journal_data.page(h, 5, 0, "", before_id=first[-1]["id"])
    assert [e["id"] for e in after] == [15, 14, 13, 12, 11]      # no skip, no repeat


def test_get_history_takes_before_id_and_keeps_its_old_shape(tmp_path):
    import ast
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "get_history")
    h = _entries(6)
    ns = {"_history_cache": types.SimpleNamespace(items=lambda: h), "_journal": journal_data,
          "DATA_DIR": tmp_path, "_unsent": __import__("unsent"), "_history_ids": history_ids,
          "_recent_audio": recent_audio}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    get = ns["get_history"]
    assert [e["id"] for e in get(None, 2)] == [6, 5]                       # (limit)
    assert [e["id"] for e in get(None, 2, 2)] == [4, 3]                    # (limit, offset)
    assert [e["id"] for e in get(None, 2, 0, "entry")] == [6, 5]           # (limit, offset, query)
    assert [e["id"] for e in get(None, 2, 0, "", 4)] == [3, 2]             # (..., before_id)
    assert [e["id"] for e in get(None, 2, 0, "", "4")] == [3, 2]           # as text
    assert len(get(None)) == 6 and all("can_redo" not in e for e in get(None))


def test_get_history_marks_the_entries_whose_recording_is_still_kept(tmp_path):
    import ast
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "get_history")
    d = tmp_path / "debug_audio"
    d.mkdir()
    (d / "rec-20261016-100000.wav").write_bytes(b"RIFF")
    h = [{"timestamp": "2026-10-16T10:00:00", "text": "a", "audio": "rec-20261016-100000.wav", "id": 1},
         {"timestamp": "2026-10-16T09:00:00", "text": "b", "audio": "rec-20261016-090000.wav", "id": 2},
         {"timestamp": "2026-10-16T08:00:00", "text": "c", "id": 3}]
    cached = [dict(e) for e in h]
    ns = {"_history_cache": types.SimpleNamespace(items=lambda: cached), "_journal": journal_data,
          "DATA_DIR": tmp_path, "_unsent": __import__("unsent"), "_history_ids": history_ids,
          "_recent_audio": recent_audio}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    out = {e["id"]: e for e in ns["get_history"](None)}
    assert out[1].get("can_redo") is True
    assert "can_redo" not in out[2] and "can_redo" not in out[3]      # rotated out, never had one
    assert cached == h                                                 # the cache is untouched


# ── the app's name, cheap enough for the moment the key goes down ────────────

def test_frontmost_app_name_returns_text_and_never_raises():
    import app_detection
    name = app_detection.frontmost_app_name()
    assert isinstance(name, str)


def test_frontmost_app_name_on_windows_uses_no_subprocess(monkeypatch):
    import app_detection
    monkeypatch.setattr(app_detection.sys, "platform", "win32")
    monkeypatch.setattr(app_detection, "_get_active_app_windows",
                        lambda: {"name": "notepad", "bundleId": "", "suggested_style": "normal"})
    assert app_detection.frontmost_app_name() == "notepad"
    monkeypatch.setattr(app_detection, "_get_active_app_windows",
                        lambda: {"name": "Unknown", "bundleId": "", "suggested_style": "normal"})
    assert app_detection.frontmost_app_name() == ""
    import ast
    tree = ast.parse((ROOT / "src" / "app_detection.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "frontmost_app_name")
    used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | \
           {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "subprocess" not in used and "run" not in used and "Popen" not in used
    assert "osascript" not in " ".join(c.value for c in ast.walk(fn)
                                       if isinstance(c, ast.Constant) and isinstance(c.value, str)
                                       and c is not fn.body[0].value)


def test_frontmost_app_name_unknown_platform_and_errors_give_nothing(monkeypatch):
    import app_detection
    monkeypatch.setattr(app_detection.sys, "platform", "linux")
    assert app_detection.frontmost_app_name() == ""
    monkeypatch.setattr(app_detection.sys, "platform", "win32")

    def boom():
        raise OSError("no window")
    monkeypatch.setattr(app_detection, "_get_active_app_windows", boom)
    assert app_detection.frontmost_app_name() == ""


def test_the_name_is_captured_when_the_key_goes_down_and_the_menu_warms_it_up():
    import ast
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")

    def method(name):
        return ast.unparse(next(n for n in klass.body
                                if isinstance(n, ast.FunctionDef) and n.name == name))
    press = method("on_hotkey_press")
    assert "_text_context.frontmost_app()" in press and "self._press_context" in press
    assert press.index("_text_context.frontmost_app()") < press.index("self._show_recording_pill")
    assert "_text_context.warm()" in method("_warm_paste_deps")


# ── the recording an entry points at ─────────────────────────────────────────

def test_two_takes_that_end_in_the_same_second_keep_their_own_recordings(tmp_path):
    from datetime import datetime
    now = datetime(2026, 10, 16, 14, 30, 12)
    a = recent_audio.keep(tmp_path, b"first", {}, now=now)
    b = recent_audio.keep(tmp_path, b"second", {}, now=now)
    c = recent_audio.keep(tmp_path, b"third", {}, now=now)
    assert [p.name for p in (a, b, c)] == [
        "rec-20261016-143012.wav", "rec-20261016-143012-2.wav", "rec-20261016-143012-3.wav"]
    assert (a.read_bytes(), b.read_bytes(), c.read_bytes()) == (b"first", b"second", b"third")
    assert all(recent_audio.is_valid_name(p.name) for p in (a, b, c))


def test_the_pipeline_gives_each_entry_its_own_recording_even_in_one_second(tmp_path):
    p = make_pipeline(tmp_path)
    first, second = _dictate(p), _dictate(p)
    assert first["audio"] != second["audio"]
    assert len(list((tmp_path / "debug_audio").glob("rec-*.wav"))) == 2


def test_only_names_the_recorder_gives_are_names():
    ok = recent_audio.is_valid_name
    assert ok("rec-20261016-143012.wav") and ok("rec-20261016-143012-2.wav")
    for bad in ("", None, 5, "rec-20261016-143012.wav ", "rec-20261016-143012.wav\n", "../rec-20261016-143012.wav",
                "rec-20261016-143012-.wav", "rec-20261016-143012-1234.wav", "rec-2026101-143012.wav",
                "x/rec-20261016-143012.wav", "rec-20261016-143012.wav/x"):
        assert not ok(bad), bad


def test_a_recording_is_found_only_inside_the_folder(tmp_path):
    d = tmp_path / "debug_audio"
    d.mkdir()
    (d / "rec-20261016-143012.wav").write_bytes(b"RIFF")
    (tmp_path / "rec-20261016-143013.wav").write_bytes(b"RIFF")
    assert recent_audio.resolve(tmp_path, "rec-20261016-143012.wav") == (d / "rec-20261016-143012.wav").resolve()
    assert recent_audio.resolve(tmp_path, "rec-20261016-143013.wav") is None      # outside
    assert recent_audio.resolve(tmp_path, "rec-20261016-143014.wav") is None      # not there
    assert recent_audio.resolve(tmp_path / "nowhere", "rec-20261016-143012.wav") is None


def test_taking_a_recording_out_and_putting_it_back_keeps_its_place_in_line(tmp_path):
    import os
    d = tmp_path / "debug_audio"
    d.mkdir()
    for i, name in enumerate(["rec-20261016-100000.wav", "rec-20261016-100001.wav"]):
        (d / name).write_bytes(b"audio%d" % i)
        os.utime(d / name, (1000 + i, 1000 + i))
    taken = recent_audio.take_out(tmp_path, "rec-20261016-100000.wav")
    assert taken == ("rec-20261016-100000.wav", b"audio0", 1000.0)
    assert [p.name for p in recent_audio.files(tmp_path)] == ["rec-20261016-100001.wav"]
    assert recent_audio.take_out(tmp_path, "rec-20261016-100000.wav") is None
    assert recent_audio.put_back(tmp_path, taken) is True
    assert [p.name for p in recent_audio.files(tmp_path)] == ["rec-20261016-100000.wav", "rec-20261016-100001.wav"]
    assert recent_audio.put_back(tmp_path, None) is False
    assert recent_audio.names(tmp_path) == {"rec-20261016-100000.wav", "rec-20261016-100001.wav"}


def test_a_full_folder_does_not_take_a_recording_back(tmp_path):
    d = tmp_path / "debug_audio"
    d.mkdir()
    for i in range(recent_audio.KEEP):
        (d / f"rec-20261016-1000{i:02d}.wav").write_bytes(b"x")
    assert recent_audio.put_back(tmp_path, ("rec-20260101-000000.wav", b"old", 1.0)) is False
