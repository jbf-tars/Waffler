"""Crash recovery (S4): a take that was being turned into text when Waffler
stopped comes back as a "Not sent" card.

src/take_marker.py keeps a small file naming the takes in flight. The pipeline
writes it right after a recording is kept and clears it however the dictation
ends; a mark that survives to the next start is a take that never finished.
"""
import ast
import json
import shutil
import sys
import time
import types
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import atomic_json  # noqa: E402
import history_ids  # noqa: E402
import privacy_data  # noqa: E402
import recent_audio  # noqa: E402
import take_marker  # noqa: E402
import unsent  # noqa: E402
import user_messages  # noqa: E402
from _pipeline_harness import (  # noqa: E402
    FakeTranscriber, Hang, fast_limits, history, make_pipeline, run_process, silence_wav,
    speech_wav, wait_for,
)

NAME = "rec-20261016-143012.wav"
OTHER = "rec-20261016-143500.wav"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    fast_limits(monkeypatch)


def _ring(data_dir, name=NAME, wav=None):
    d = data_dir / "debug_audio"
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(wav if wav is not None else speech_wav(2.0))
    return d / name


def _marker(data_dir):
    f = data_dir / take_marker.FILE
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


# ── the file ─────────────────────────────────────────────────────────────────

def test_a_take_is_written_down_and_crossed_out(tmp_path):
    take_marker.note(tmp_path, NAME, datetime(2026, 10, 16, 14, 30, 12))
    assert _marker(tmp_path) == {"version": 1, "takes": [{"audio": NAME, "at": "2026-10-16T14:30:12"}]}
    take_marker.note(tmp_path, OTHER)
    assert [t["audio"] for t in _marker(tmp_path)["takes"]] == [NAME, OTHER]
    take_marker.clear(tmp_path, NAME)
    assert [t["audio"] for t in _marker(tmp_path)["takes"]] == [OTHER]
    take_marker.clear(tmp_path, OTHER)
    assert not (tmp_path / take_marker.FILE).exists(), "nothing in flight, no file"
    take_marker.clear(tmp_path, OTHER)                           # and again: nothing to do


def test_the_same_take_noted_twice_is_one_take(tmp_path):
    take_marker.note(tmp_path, NAME)
    take_marker.note(tmp_path, NAME)
    assert len(_marker(tmp_path)["takes"]) == 1


def test_the_file_holds_names_and_times_and_nothing_said(tmp_path):
    take_marker.note(tmp_path, NAME)
    raw = (tmp_path / take_marker.FILE).read_text(encoding="utf-8")
    assert set(json.loads(raw)) == {"version", "takes"}
    assert set(json.loads(raw)["takes"][0]) == {"audio", "at"}
    assert len(raw) < 200


@pytest.mark.parametrize("bad", ["../x.wav", "rec-1.wav", "recording-2026-10-16T10-00-00.wav",
                                 "rec-20261016-143012.wav/../..", "", None, 7, "REC-20261016-143012.wav",
                                 "rec-20261016-143012.WAV"])
def test_only_a_name_the_recorder_gives_is_ever_written_down(tmp_path, bad):
    take_marker.note(tmp_path, bad)
    assert not (tmp_path / take_marker.FILE).exists()


@pytest.mark.parametrize("content", [
    b"", b"{", b'{"version": 1, "takes": [{"audio": "rec-2026', b"not json at all", b"[]", b"null",
    b'"text"', b'{"takes": "no"}', b'{"takes": [1, "x", null, {"audio": 5}, {"audio": "../../etc/passwd"}]}',
    b"\xff\xfe\x00bad", b"\xef\xbb\xbf{",
])
def test_a_file_that_is_cut_short_or_wrong_is_never_trusted(tmp_path, content):
    (tmp_path / take_marker.FILE).write_bytes(content)
    assert take_marker.pending(tmp_path) == []
    _ring(tmp_path)
    assert take_marker.recover(tmp_path, []) == []
    take_marker.note(tmp_path, NAME)                      # and it can be written over
    assert _marker(tmp_path)["takes"][0]["audio"] == NAME


def test_a_byte_order_mark_is_read(tmp_path):
    (tmp_path / take_marker.FILE).write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"version": 1, "takes": [{"audio": NAME, "at": ""}]}).encode())
    assert [t["audio"] for t in take_marker.pending(tmp_path)] == [NAME]


def test_a_flooded_file_is_cut_to_the_recordings_that_can_exist(tmp_path):
    takes = [{"audio": f"rec-20261016-1000{i:02d}.wav", "at": ""} for i in range(60)]
    (tmp_path / take_marker.FILE).write_text(json.dumps({"version": 1, "takes": takes}), encoding="utf-8")
    assert len(take_marker.pending(tmp_path)) == recent_audio.KEEP


def test_no_file_no_takes_and_the_safe_forms_never_raise(tmp_path, monkeypatch):
    assert take_marker.pending(tmp_path) == []
    logged = []

    def boom(*a, **k):
        raise PermissionError("[WinError 5] Access is denied")
    monkeypatch.setattr(take_marker, "write_json_atomic", boom)
    take_marker.note_safely(tmp_path, NAME, logged.append)
    (tmp_path / take_marker.FILE).write_text(
        json.dumps({"version": 1, "takes": [{"audio": NAME, "at": ""}, {"audio": OTHER, "at": ""}]}),
        encoding="utf-8")
    take_marker.clear_safely(tmp_path, NAME, logged.append)
    assert len(logged) == 2 and all("PermissionError" in m for m in logged)
    assert not any(NAME in m for m in logged), "names of recordings stay out of the log"


# ── recovering ───────────────────────────────────────────────────────────────

def test_a_take_that_never_finished_comes_back_as_a_not_sent_card_with_its_own_copy(tmp_path):
    wav = _ring(tmp_path)
    take_marker.note(tmp_path, NAME, datetime(2026, 10, 16, 14, 30, 12))
    [card] = take_marker.recover(tmp_path, [])
    assert card["failed"] is True and card["not_sent_reason"] == unsent.REASON_INTERRUPTED
    assert card["timestamp"] == "2026-10-16T14:30:12"
    assert card["audio"] == NAME and card["text"] == "" and card["word_count"] == 0
    assert card["styled"] == unsent.STYLED_NOTE and card["will_retry"] is False
    assert card["duration_s"] == pytest.approx(2.0, abs=0.1)
    copy = tmp_path / "unsent" / card["unsent_id"]
    assert unsent.is_valid_id(card["unsent_id"]) and copy.read_bytes() == wav.read_bytes()
    assert wav.exists(), "the kept recording is left where it is"
    assert unsent.entry_id(card) == card["unsent_id"]
    assert unsent.find_entry([card], card["unsent_id"])[0] == 0
    assert take_marker.pending(tmp_path), "the caller crosses it out once the card is saved"


def test_a_take_already_in_the_journal_is_not_made_again(tmp_path):
    _ring(tmp_path)
    take_marker.note(tmp_path, NAME)
    done = {"timestamp": "2026-10-16T14:30:12", "text": "x", "styled": "X.", "audio": NAME, "id": 4}
    assert take_marker.recover(tmp_path, [done]) == []
    assert not (tmp_path / "unsent").exists()


def test_a_take_whose_recording_has_rotated_out_cannot_come_back(tmp_path):
    take_marker.note(tmp_path, NAME)
    assert take_marker.recover(tmp_path, []) == []


def test_only_the_takes_that_can_come_back_do(tmp_path):
    _ring(tmp_path, NAME)
    _ring(tmp_path, OTHER)
    take_marker.note(tmp_path, NAME)
    take_marker.note(tmp_path, OTHER)
    take_marker.note(tmp_path, "rec-20261016-150000.wav")             # gone
    found = take_marker.recover(tmp_path, [{"audio": OTHER}])
    assert [c["audio"] for c in found] == [NAME]


def test_a_copy_that_fails_is_skipped_and_the_mark_is_left_for_next_time(tmp_path, monkeypatch):
    _ring(tmp_path)
    take_marker.note(tmp_path, NAME)
    logged = []

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(take_marker.shutil, "copyfile", full)
    assert take_marker.recover(tmp_path, [], logged.append) == []
    assert any("could not keep" in m for m in logged)
    assert take_marker.pending(tmp_path)


def test_the_time_comes_from_the_mark_then_the_name_then_the_file(tmp_path):
    wav = _ring(tmp_path)
    t = take_marker._take_time
    assert t({"at": "2026-10-16T14:30:12"}, wav) == "2026-10-16T14:30:12"
    assert t({"at": "garbage"}, wav) == "2026-10-16T14:30:12"        # from the name
    assert t({}, wav) == "2026-10-16T14:30:12"
    odd = _ring(tmp_path, "rec-99999999-999999.wav")
    assert t({"at": ""}, odd) == datetime.fromtimestamp(odd.stat().st_mtime).isoformat(timespec="seconds")


def test_forget_all_removes_the_file(tmp_path):
    take_marker.note(tmp_path, NAME)
    take_marker.forget_all(tmp_path)
    take_marker.forget_all(tmp_path)
    assert not (tmp_path / take_marker.FILE).exists()


# ── at start-up (app.py) ─────────────────────────────────────────────────────

def _startup(tmp_path, existing=None):
    """app.py's _recover_interrupted_takes with the history it would find."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_recover_interrupted_takes")
    store = {"history": list(existing or []), "logged": [], "loads": 0}

    def load_history():
        store["loads"] += 1
        return store["history"]

    def append_history_safely(item):
        history_ids.stamp(item, store["history"])
        store["history"].append(item)
        return True
    ns = {"DATA_DIR": tmp_path, "_take_marker": take_marker, "load_history": load_history,
          "append_history_safely": append_history_safely, "_log_to_file": store["logged"].append}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    return ns["_recover_interrupted_takes"], store


def test_with_no_mark_start_up_reads_nothing_and_takes_no_time(tmp_path):
    run, store = _startup(tmp_path)
    t0 = time.perf_counter()
    for _ in range(100):
        run()
    assert (time.perf_counter() - t0) / 100 < 0.002
    assert store["loads"] == 0 and store["history"] == [] and store["logged"] == []


def test_start_up_adds_the_card_and_crosses_the_mark_out(tmp_path):
    _ring(tmp_path)
    take_marker.note(tmp_path, NAME, datetime(2026, 10, 16, 14, 30, 12))
    run, store = _startup(tmp_path)
    run()
    [card] = store["history"]
    assert card["failed"] and card["not_sent_reason"] == "interrupted" and card["id"] == 1
    assert not (tmp_path / take_marker.FILE).exists()
    assert any("1 interrupted take(s)" in m for m in store["logged"])
    run()                                                   # a second start: nothing more
    assert len(store["history"]) == 1


def test_a_damaged_mark_at_start_up_is_cleared_without_a_card_or_an_error(tmp_path):
    (tmp_path / take_marker.FILE).write_bytes(b'{"version": 1, "takes": [{"audio": "rec-2026')
    run, store = _startup(tmp_path)
    run()
    assert store["history"] == [] and not (tmp_path / take_marker.FILE).exists()


def test_a_card_that_could_not_be_saved_leaves_the_mark_for_next_time(tmp_path):
    _ring(tmp_path)
    take_marker.note(tmp_path, NAME)
    run, store = _startup(tmp_path)
    ns = run.__globals__
    ns["append_history_safely"] = lambda item: False
    run()
    assert take_marker.pending(tmp_path)


def test_start_up_never_raises(tmp_path):
    take_marker.note(tmp_path, NAME)
    run, store = _startup(tmp_path)
    run.__globals__["load_history"] = lambda: (_ for _ in ()).throw(OSError("locked"))
    run()
    assert any("recovery skipped" in m for m in store["logged"])


def test_start_up_runs_the_recovery_before_the_window_asks_for_the_journal():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    main = src[src.index("\ndef main():"):]
    assert "_recover_interrupted_takes()" in main
    assert main.index("_recover_interrupted_takes()") < main.index("webview.create_window")


# ── the pipeline writes it and crosses it out ────────────────────────────────

def _names_in_flight(p):
    f = p.ns["DATA_DIR"] / take_marker.FILE
    return [t["audio"] for t in json.loads(f.read_text(encoding="utf-8"))["takes"]] if f.exists() else []


def test_a_take_is_marked_while_it_is_being_turned_into_text_and_not_after(tmp_path):
    hang = Hang(10)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    worker = run_process(p)
    assert hang.entered.wait(5)
    [name] = _names_in_flight(p)
    assert recent_audio.is_valid_name(name) and (tmp_path / "debug_audio" / name).is_file()
    hang.release.set()
    worker.join(5)
    assert _names_in_flight(p) == []
    assert history(p)[-1]["audio"] == name


def test_the_mark_is_gone_when_the_words_could_not_be_heard_either(tmp_path):
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(error=ConnectionError("Connection error.")))
    run_process(p).join(5)
    assert _names_in_flight(p) == []
    [card] = history(p)
    assert card["failed"] and recent_audio.is_valid_name(card["audio"])


def test_the_mark_is_gone_after_silence(tmp_path):
    p = make_pipeline(tmp_path, wav=silence_wav(2.0))
    run_process(p).join(5)
    assert _names_in_flight(p) == [] and history(p) == []


def test_the_mark_is_gone_after_a_cancel(tmp_path):
    hang = Hang(10)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    worker = run_process(p)
    assert hang.entered.wait(5)
    assert _names_in_flight(p)
    p._on_overlay_cancel()
    hang.release.set()
    worker.join(5)
    assert _names_in_flight(p) == []


def test_the_mark_is_gone_after_an_error(tmp_path):
    p = make_pipeline(tmp_path)
    p.ns["_refresh_tray_menu"] = lambda: (_ for _ in ()).throw(RuntimeError("icon gone"))
    run_process(p).join(5)
    assert _names_in_flight(p) == [] and history(p)[-1]["audio"]


def test_two_takes_in_flight_are_both_marked_and_each_crossed_out_alone(tmp_path):
    take_marker.note(tmp_path, OTHER)
    p = make_pipeline(tmp_path)
    run_process(p).join(5)
    assert _names_in_flight(p) == [OTHER], "another take's mark is left alone"


def test_with_recent_recordings_off_nothing_is_marked(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"keep_recent_audio": False}), encoding="utf-8")
    hang = Hang(10)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    worker = run_process(p)
    assert hang.entered.wait(5)
    assert _names_in_flight(p) == []
    hang.release.set()
    worker.join(5)


def test_a_mark_that_cannot_be_written_never_costs_the_dictation(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise PermissionError("[WinError 5] Access is denied")
    monkeypatch.setattr(take_marker, "write_json_atomic", boom)
    p = make_pipeline(tmp_path)
    run_process(p).join(5)
    entry = history(p)[-1]
    assert entry["styled"] and p.clipboard.pastes
    assert any("[marker] not written" in m for m in p.logged)


def test_the_stuck_handler_names_the_recording_so_it_is_not_made_twice(tmp_path):
    p = make_pipeline(tmp_path)
    run = types.SimpleNamespace(audio_bytes=speech_wav(1.0), transcript=None, saved_as_unsent=False,
                                audio_name=NAME)
    _ring(tmp_path)
    p._run_is_current = lambda r: False
    p._ui_stuck(run)
    [card] = history(p)
    assert card["audio"] == NAME and card["not_sent_reason"] == unsent.REASON_STUCK
    take_marker.note(tmp_path, NAME)
    assert take_marker.recover(tmp_path, history(p)) == []


# ── Try again on a recovered card ────────────────────────────────────────────

def test_try_again_turns_the_recovered_card_into_a_normal_entry_without_pasting(tmp_path):
    _ring(tmp_path)
    take_marker.note(tmp_path, NAME, datetime(2026, 10, 16, 14, 30, 12))
    run, store = _startup(tmp_path)
    run()
    (tmp_path / "history.json").write_text(json.dumps(store["history"]), encoding="utf-8")
    p = make_pipeline(tmp_path)
    [card] = history(p)
    result = p.resend_unsent(card["unsent_id"])
    assert result["ok"] is True
    item = result["item"]
    assert "failed" not in item and item["styled"] and item["timestamp"] == "2026-10-16T14:30:12"
    assert item["id"] == card["id"] and item["audio"] == NAME
    assert p.clipboard.pastes == [] and p.clipboard.copies == []
    assert not (tmp_path / "unsent" / card["unsent_id"]).exists()


def test_an_interrupted_card_is_not_sent_behind_the_users_back(tmp_path):
    _ring(tmp_path)
    take_marker.note(tmp_path, NAME, datetime.now())
    run, store = _startup(tmp_path)
    run()
    (tmp_path / "history.json").write_text(json.dumps(store["history"]), encoding="utf-8")
    p = make_pipeline(tmp_path)
    [card] = history(p)
    assert unsent.is_waiting(card) is False
    assert unsent.auto_retry_due(card, ignore_backoff=True) is False
    assert unsent.will_auto_retry(card) is False
    assert unsent.pending(history(p), tmp_path / "unsent", include_cancelled=False) == []
    assert p._count_unsent() == 0
    p._drain_unsent("test", ignore_backoff=True)
    assert p.transcriber.calls == 0, "nothing was sent"


def test_if_try_again_fails_again_the_card_carries_on_like_any_other(tmp_path):
    _ring(tmp_path)
    take_marker.note(tmp_path, NAME, datetime.now())
    run, store = _startup(tmp_path)
    run()
    (tmp_path / "history.json").write_text(json.dumps(store["history"]), encoding="utf-8")
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(error=ConnectionError("Connection error.")))
    [card] = history(p)
    result = p.resend_unsent(card["unsent_id"])
    assert result["ok"] is False and result["reason"] == unsent.REASON_OFFLINE
    assert history(p)[0]["not_sent_reason"] == unsent.REASON_OFFLINE


# ── the rules and the words ──────────────────────────────────────────────────

def test_the_new_reason_is_known_to_the_rules():
    assert unsent.classify_reason("interrupted") == unsent.REASON_INTERRUPTED
    assert unsent.classify_reason("Interrupted") == unsent.REASON_INTERRUPTED
    heading, body = unsent.toast_text(unsent.REASON_INTERRUPTED)
    assert heading == "Recovered" and user_messages.INTERRUPTED_MESSAGE in body
    # A card that was cancelled is still cancelled, and nothing else changed.
    assert unsent.is_waiting({"not_sent_reason": "cancelled"}) is False
    assert unsent.is_waiting({"not_sent_reason": "offline"}) is True


def test_the_sentence_is_plain_and_the_window_says_the_same():
    msg = user_messages.INTERRUPTED_MESSAGE
    assert "—" not in msg and "–" not in msg and " - " not in msg
    assert msg.endswith(".") and "Waffler" in msg
    logic = (ROOT / "ui" / "logic.js").read_text(encoding="utf-8")
    assert "interrupted: `" in logic
    card = logic[logic.index("interrupted: `"):]
    card = card[:card.index("\n")]
    assert msg.replace("The recording is saved on this computer.", "${kept}") in card


def test_the_marker_is_on_the_privacy_lists_and_is_not_zipped_into_the_logs(tmp_path):
    assert take_marker.FILE in privacy_data.DATA_FILES and take_marker.FILE not in privacy_data.KEPT_FILES
    take_marker.note(tmp_path, NAME)
    assert privacy_data.delete_my_data(tmp_path)["ok"] is True
    assert not (tmp_path / take_marker.FILE).exists()
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "download_logs")
    listed = [c.value for c in ast.walk(fn) if isinstance(c, ast.Constant)
              and isinstance(c.value, str) and c.value.endswith(".json")]
    assert take_marker.FILE not in listed and take_marker.FILE in ast.get_docstring(fn)


def test_the_marker_is_written_through_the_atomic_helper_with_an_encoding():
    src = (ROOT / "src" / "take_marker.py").read_text(encoding="utf-8")
    assert "write_json_atomic(" in src
    for line in src.splitlines():
        if ".read_text(" in line:
            assert "encoding=" in line
