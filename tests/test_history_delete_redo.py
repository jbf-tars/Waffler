"""Delete one entry, undo it, and "Redo this take" (S4).

The real Api and WafflerPipeline methods from app.py run against the harness
in tests/_pipeline_harness.py: a temporary data folder, a fake transcriber and
styler, no network, no clipboard.
"""
import ast
import json
import os
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import history_ops  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import privacy_data  # noqa: E402
import recent_audio  # noqa: E402
import unsent  # noqa: E402
from _pipeline_harness import (  # noqa: E402
    FakeStyler, FakeTranscriber, Hang, fast_limits, history, make_pipeline, run_process,
    speech_wav,
)

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=3.0, TRANSCRIBE_DEADLINE_BASE_S=3.0,
                TRANSCRIBE_DEADLINE_MAX_S=3.0, STYLE_DEADLINE_S=3.0)


def api_of(p, *names):
    """The named Api methods of app.py, run in the pipeline's namespace."""
    api = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    nodes = [n for n in api.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    ns = p.ns
    ns["_pipeline"] = p
    ns.setdefault("_history_cache", types.SimpleNamespace(invalidate=lambda: None))
    exec(compile(ast.Module(nodes, []), "<app.py Api>", "exec"), ns)
    return types.SimpleNamespace(**{n: (lambda f: lambda *a, **k: f(None, *a, **k))(ns[n]) for n in names})


def _dictate(p, text=None):
    if text is not None:
        p.transcriber.text = text
    worker = run_process(p)
    worker.join(5)
    assert not worker.is_alive()
    return history(p)[-1]


def _shelf(monkeypatch, window_s=15.0):
    now = [100.0]
    shelf = history_ops.UndoShelf(window_s=window_s, clock=lambda: now[0], timer=False)
    monkeypatch.setattr(history_ops, "SHELF", shelf)
    return shelf, now


def _three(tmp_path, monkeypatch=None):
    """A pipeline with three dictations: ids 1, 2, 3."""
    p = make_pipeline(tmp_path)
    a = _dictate(p, "first one")
    # Distinct times and recordings, as three real takes would have.
    entries = history(p)
    entries[0]["timestamp"] = "2026-10-16T09:00:00"
    (tmp_path / "history.json").write_text(json.dumps(entries), encoding="utf-8")
    b = _dictate(p, "second one")
    c = _dictate(p, "third one")
    h = history(p)
    h[1]["timestamp"], h[2]["timestamp"] = "2026-10-16T10:00:00", "2026-10-16T11:00:00"
    (tmp_path / "history.json").write_text(json.dumps(h), encoding="utf-8")
    return p, history(p)


# ── delete ───────────────────────────────────────────────────────────────────

def test_delete_by_id_removes_that_entry_only_and_returns_an_undo(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    api = api_of(p, "delete_history_item")
    r = api.delete_history_item(before[1]["id"])
    assert r["ok"] is True and isinstance(r["undo"], str) and r["undo"]
    assert history(p) == [before[0], before[2]]


def test_delete_is_one_atomic_write_that_leaves_no_temp_file(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    api_of(p, "delete_history_item").delete_history_item(before[0]["id"])
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads((tmp_path / "history.json").read_text(encoding="utf-8")) == before[1:]


def test_delete_says_the_cache_must_read_the_file_again(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    calls = []
    p.ns["_history_cache"] = types.SimpleNamespace(invalidate=lambda: calls.append(1))
    api = api_of(p, "delete_history_item", "undo_history_change")
    token = api.delete_history_item(before[0]["id"])["undo"]
    api.undo_history_change(token)
    assert calls == [1, 1]


def test_an_entry_without_an_id_is_found_by_its_time_and_words(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p = make_pipeline(tmp_path)
    old = [{"timestamp": "2026-09-01T10:00:00", "text": "one", "styled": "One."},
           {"timestamp": "2026-09-01T10:00:05", "text": "two", "styled": "Two."},
           {"timestamp": "2026-09-01T10:00:09", "text": "three", "styled": "Three."}]
    (tmp_path / "history.json").write_text(json.dumps(old), encoding="utf-8")
    api = api_of(p, "delete_history_item")
    assert api.delete_history_item(None, "2026-09-01T10:00:05", "two")["ok"] is True
    assert history(p) == [old[0], old[2]]
    # A time with different words, or words at a different time, is not it.
    assert api.delete_history_item(None, "2026-09-01T10:00:00", "two") == {"ok": False, "reason": "not_found"}
    assert api.delete_history_item(None, "2026-09-01T10:00:01", "one") == {"ok": False, "reason": "not_found"}
    assert api.delete_history_item(None, "", "one") == {"ok": False, "reason": "not_found"}
    assert history(p) == [old[0], old[2]]


def test_two_old_entries_that_look_the_same_lose_one_not_both(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p = make_pipeline(tmp_path)
    twin = {"timestamp": "2026-09-01T10:00:00", "text": "same", "styled": "Same."}
    (tmp_path / "history.json").write_text(json.dumps([twin, dict(twin)]), encoding="utf-8")
    assert api_of(p, "delete_history_item").delete_history_item(None, twin["timestamp"], "same")["ok"]
    assert history(p) == [twin]


def test_an_id_that_is_not_there_is_not_found_even_when_the_words_match(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    api = api_of(p, "delete_history_item")
    r = api.delete_history_item(999, before[0]["timestamp"], before[0]["text"])
    assert r == {"ok": False, "reason": "not_found"} and history(p) == before
    for bad in ("abc", -1, 0, 1.5, [1], {"id": 1}):
        assert api.delete_history_item(bad)["ok"] is False
    assert history(p) == before


def test_a_not_sent_card_is_left_to_its_own_delete(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(error=ConnectionError("Connection error.")))
    card = _dictate(p)
    assert card["failed"]
    r = api_of(p, "delete_history_item").delete_history_item(card["id"])
    assert r == {"ok": False, "reason": "not_sent"} and history(p) == [card]
    assert (tmp_path / "unsent" / card["unsent_id"]).is_file()


def test_the_recording_kept_with_the_entry_goes_too(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    wavs = sorted(os.listdir(tmp_path / "debug_audio"))
    gone = before[1]["audio"]
    r = api_of(p, "delete_history_item").delete_history_item(before[1]["id"])
    assert r["ok"] and not (tmp_path / "debug_audio" / gone).exists()
    assert set(os.listdir(tmp_path / "debug_audio")) == set(wavs) - {gone}


def test_an_entry_whose_recording_has_rotated_out_deletes_fine(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    os.unlink(tmp_path / "debug_audio" / before[0]["audio"])
    assert api_of(p, "delete_history_item").delete_history_item(before[0]["id"])["ok"]
    assert [e["id"] for e in history(p)] == [before[1]["id"], before[2]["id"]]


def test_a_write_that_fails_leaves_the_entry_and_the_recording_alone(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)

    def locked(path, data):
        raise PermissionError("[WinError 5] Access is denied")
    p.ns["write_json_atomic"] = locked
    r = api_of(p, "delete_history_item").delete_history_item(before[1]["id"])
    assert r == {"ok": False, "reason": "error"}
    assert history(p) == before and (tmp_path / "debug_audio" / before[1]["audio"]).is_file()
    assert any("could not delete" in m for m in p.logged)


def test_a_journal_that_cannot_be_read_is_not_written_over(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p = make_pipeline(tmp_path)
    (tmp_path / "history.json").write_bytes(b'[{"timestamp": "2026-10-16T10:00:00", "te')
    r = api_of(p, "delete_history_item").delete_history_item(1)
    assert r["ok"] is False
    kept = list(tmp_path.glob("history.unreadable-*.json"))
    assert len(kept) == 1                                       # kept aside, as ever


def test_the_log_does_not_say_what_was_deleted(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    api_of(p, "delete_history_item").delete_history_item(before[1]["id"])
    assert not any("second one" in m for m in p.logged)
    assert any("entry deleted by the user" in m for m in p.logged)


def test_paste_last_does_not_bring_a_deleted_entry_back(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    assert p._last_result.get() == before[2]["styled"]
    api = api_of(p, "delete_history_item")
    api.delete_history_item(before[1]["id"])                      # another entry
    assert p._last_result.get() == before[2]["styled"]
    api.delete_history_item(before[2]["id"])                      # the one it holds
    assert p._last_result.get() != before[2]["styled"]


def test_deleting_while_a_dictation_is_saved_loses_neither(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    api = api_of(p, "delete_history_item")
    out = []

    def work():
        for i in range(10):
            p.ns["append_history"]({"timestamp": f"2026-10-17T10:00:{i:02d}", "text": f"w{i}"})
    t = threading.Thread(target=work)
    t.start()
    out.append(api.delete_history_item(before[0]["id"]))
    t.join()
    texts = [e["text"] for e in history(p)]
    assert out[0]["ok"] and "first one" not in texts
    assert all(f"w{i}" in texts for i in range(10)) and "second one" in texts


# ── undo ─────────────────────────────────────────────────────────────────────

def test_undo_puts_the_entry_back_where_its_time_says_with_its_recording(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    wav = tmp_path / "debug_audio" / before[1]["audio"]
    data, mtime = wav.read_bytes(), wav.stat().st_mtime
    api = api_of(p, "delete_history_item", "undo_history_change")
    token = api.delete_history_item(before[1]["id"])["undo"]
    p.ns["append_history"]({"timestamp": "2026-10-16T12:00:00", "text": "newer"})   # life goes on
    r = api.undo_history_change(token)
    assert r["ok"] and r["kind"] == "deleted" and r["item"]["id"] == before[1]["id"]
    assert [e["text"] for e in history(p)] == ["first one", "second one", "third one", "newer"]
    assert wav.read_bytes() == data and wav.stat().st_mtime == pytest.approx(mtime, abs=1)
    assert r["item"]["can_redo"] is True


def test_undo_works_once(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    api = api_of(p, "delete_history_item", "undo_history_change")
    token = api.delete_history_item(before[1]["id"])["undo"]
    assert api.undo_history_change(token)["ok"] is True
    assert api.undo_history_change(token) == {"ok": False, "reason": "expired"}
    assert len(history(p)) == 3


def test_undo_is_too_late_after_the_window(tmp_path, monkeypatch):
    shelf, now = _shelf(monkeypatch, window_s=15.0)
    p, before = _three(tmp_path)
    api = api_of(p, "delete_history_item", "undo_history_change")
    token = api.delete_history_item(before[1]["id"])["undo"]
    now[0] += 15.5
    assert api.undo_history_change(token) == {"ok": False, "reason": "expired"}
    assert len(history(p)) == 2 and len(shelf) == 0
    assert api.undo_history_change("nonsense") == {"ok": False, "reason": "expired"}
    assert api.undo_history_change(None) == {"ok": False, "reason": "expired"}


def test_undo_with_the_recordings_folder_full_again_brings_back_the_words_only(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, before = _three(tmp_path)
    api = api_of(p, "delete_history_item", "undo_history_change")
    token = api.delete_history_item(before[0]["id"])["undo"]
    d = tmp_path / "debug_audio"
    for i in range(recent_audio.KEEP):                       # ten newer recordings
        (d / f"rec-20261017-1000{i:02d}.wav").write_bytes(b"RIFF")
    r = api.undo_history_change(token)
    assert r["ok"] and r["item"]["id"] == before[0]["id"] and "can_redo" not in r["item"]
    assert not (d / before[0]["audio"]).exists()
    assert len(os.listdir(d)) == recent_audio.KEEP + 2        # 2 of the 3 dictations' recordings + 10


def test_an_old_entry_without_an_id_comes_back_too(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p = make_pipeline(tmp_path)
    old = [{"timestamp": "2026-09-01T10:00:00", "text": "one", "styled": "One."},
           {"timestamp": "2026-09-01T10:00:05", "text": "two", "styled": "Two."}]
    (tmp_path / "history.json").write_text(json.dumps(old), encoding="utf-8")
    api = api_of(p, "delete_history_item", "undo_history_change")
    token = api.delete_history_item(None, "2026-09-01T10:00:00", "one")["undo"]
    assert api.undo_history_change(token)["ok"]
    assert history(p) == old


def test_undo_of_an_entry_that_is_already_back_does_nothing_twice(tmp_path, monkeypatch):
    shelf, _ = _shelf(monkeypatch)
    p, before = _three(tmp_path)
    api = api_of(p, "undo_history_change")
    token = shelf.put(history_ops.DELETED, dict(before[1]))      # its twin is still in the file
    assert api.undo_history_change(token) == {"ok": False, "reason": "expired"}
    assert len(history(p)) == 3


def test_delete_all_my_data_empties_the_shelf(tmp_path, monkeypatch):
    shelf, _ = _shelf(monkeypatch)
    p, before = _three(tmp_path)
    api = api_of(p, "delete_history_item", "undo_history_change")
    token = api.delete_history_item(before[1]["id"])["undo"]
    assert len(shelf) == 1
    privacy_data.delete_my_data(tmp_path)
    assert len(shelf) == 0
    assert api.undo_history_change(token)["ok"] is False


# ── the shelf ────────────────────────────────────────────────────────────────

def test_the_shelf_keeps_a_few_and_forgets_the_oldest_first():
    shelf = history_ops.UndoShelf(limit=3, timer=False)
    tokens = [shelf.put(history_ops.DELETED, {"i": i}) for i in range(5)]
    assert len(shelf) == 3
    assert shelf.take(tokens[0]) is None and shelf.take(tokens[1]) is None
    assert shelf.take(tokens[4])["entry"] == {"i": 4}


def test_the_shelf_does_not_hold_more_recording_than_it_may():
    shelf = history_ops.UndoShelf(audio_limit=100, timer=False)
    a = shelf.put(history_ops.DELETED, {"i": 1}, audio=("rec-20261016-100000.wav", b"x" * 80, 1.0))
    b = shelf.put(history_ops.DELETED, {"i": 2}, audio=("rec-20261016-100001.wav", b"x" * 80, 1.0))
    assert shelf.take(a)["audio"] is not None
    assert shelf.take(b)["audio"] is None, "the entry is kept, the audio is not"


def test_the_shelf_lets_go_by_itself_after_the_window():
    shelf = history_ops.UndoShelf(window_s=0.05, timer=True)
    shelf.put(history_ops.DELETED, {"i": 1}, audio=("rec-20261016-100000.wav", b"x", 1.0))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and shelf._records:
        time.sleep(0.02)
    assert shelf._records == {}


def test_the_shelf_is_memory_only():
    src = (ROOT / "src" / "history_ops.py").read_text(encoding="utf-8")
    body = src[src.index("class UndoShelf"):]
    for forbidden in ("open(", "write_text", "write_bytes", "json.dump", "pickle", "tempfile"):
        assert forbidden not in body


# ── Redo this take ───────────────────────────────────────────────────────────

class Hears(FakeTranscriber):
    """Records the audio it was sent."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.heard = []

    def transcribe_sync(self, audio_bytes):
        self.heard.append(audio_bytes)
        return super().transcribe_sync(audio_bytes)


def _one(tmp_path, **kw):
    p = make_pipeline(tmp_path, transcriber=Hears(), **kw)
    p._press_app = (1, "Mail")
    entry = _dictate(p, "ok so ship it on monday")
    p.transcriber.text = "ok so ship it on tuesday"
    return p, entry


def test_redo_sends_the_kept_recording_and_replaces_the_words_in_place(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    other = p.ns["append_history"]({"timestamp": "2026-10-17T10:00:00", "text": "later"})
    pastes, copies = list(p.clipboard.pastes), list(p.clipboard.copies)
    wav = (tmp_path / "debug_audio" / entry["audio"]).read_bytes()
    r = api_of(p, "redo_history_item").redo_history_item(entry["id"])
    assert r["ok"] is True
    new = r["item"]
    assert p.transcriber.heard[-1] == wav, "the kept recording, nothing else"
    assert new["id"] == entry["id"] and new["timestamp"] == entry["timestamp"]
    assert new["audio"] == entry["audio"] and new["app"] == "Mail"
    assert new["duration_s"] == entry["duration_s"]
    assert new["text"] == "ok so ship it on tuesday" and new["styled"] == "Ok so ship it on tuesday."
    assert new["word_count"] == 6 and new["text_is"] == "asr_filtered" and new["redone_at"]
    assert new["provider"] == {"stt": "groq", "cleanup": "groq"} and r["item"]["can_redo"] is True
    h = history(p)
    assert len(h) == 2 and h[0]["text"] == "ok so ship it on tuesday" and h[1]["text"] == "later"
    assert "can_redo" not in h[0], "that is the window's, not the file's"
    assert p.clipboard.pastes == pastes and p.clipboard.copies == copies, "nothing pasted or copied"


def test_nothing_from_the_old_run_carries_over(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    h = history(p)
    h[0].update({"quality": {"level": "check", "flags": ["x"]}, "as_said": "limit", "paste": "failed",
                 "vocab_changes": [["a", "b"]], "asr_text": "raw", "sent_later_at": "x"})
    (tmp_path / "history.json").write_text(json.dumps(h), encoding="utf-8")
    new = api_of(p, "redo_history_item").redo_history_item(entry["id"])["item"]
    for stale in ("quality", "as_said", "paste", "vocab_changes", "asr_text", "sent_later_at"):
        assert stale not in new, stale


def test_redo_pays_for_the_speech_and_the_clean_up_like_any_run(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    usage = []
    p.ns["record_usage_safely"] = lambda *a, **k: usage.append((a, k))
    api_of(p, "redo_history_item").redo_history_item(entry["id"])
    assert [u[0][0] for u in usage] == ["whisper", "gpt"]


def test_redo_finds_an_old_style_entry_by_time_and_words_if_it_has_a_recording(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p = make_pipeline(tmp_path, transcriber=Hears(text="redone words"))
    d = tmp_path / "debug_audio"
    d.mkdir()
    (d / "rec-20260901-100000.wav").write_bytes(speech_wav(2.0))
    old = {"timestamp": "2026-09-01T10:00:00", "text": "old words", "styled": "Old words.",
           "audio": "rec-20260901-100000.wav"}
    (tmp_path / "history.json").write_text(json.dumps([old]), encoding="utf-8")
    r = api_of(p, "redo_history_item").redo_history_item(None, old["timestamp"], old["text"])
    assert r["ok"] and r["item"]["text"] == "redone words" and "id" not in r["item"]
    assert history(p)[0]["text"] == "redone words" and len(history(p)) == 1


def test_a_recording_that_has_rotated_out_cannot_be_redone(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    os.unlink(tmp_path / "debug_audio" / entry["audio"])
    before = history(p)
    assert api_of(p, "redo_history_item").redo_history_item(entry["id"]) == {"ok": False, "reason": "missing"}
    assert history(p) == before and p.transcriber.calls == 1


def test_an_entry_with_no_recording_link_cannot_be_redone(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p = make_pipeline(tmp_path)
    p.ns["append_history"]({"timestamp": "2026-09-01T10:00:00", "text": "x", "styled": "X."})
    assert api_of(p, "redo_history_item").redo_history_item(1)["reason"] == "missing"


@pytest.mark.parametrize("name", ["../secret.wav", "rec-20260101-000000.wav/../../x", "/etc/passwd",
                                  "recording-2026-10-16T10-00-00.wav", "rec-1.wav"])
def test_a_link_that_points_anywhere_else_is_never_opened(tmp_path, monkeypatch, name):
    _shelf(monkeypatch)
    p = make_pipeline(tmp_path, transcriber=Hears())
    (tmp_path / "secret.wav").write_bytes(speech_wav(1.0))
    (tmp_path / "unsent").mkdir()
    (tmp_path / "unsent" / "recording-2026-10-16T10-00-00.wav").write_bytes(speech_wav(1.0))
    p.ns["append_history"]({"timestamp": "2026-09-01T10:00:00", "text": "x", "styled": "X.", "audio": name})
    assert api_of(p, "redo_history_item").redo_history_item(1)["reason"] == "missing"
    assert p.transcriber.calls == 0


def test_unknown_entries_and_not_sent_cards_are_not_redone(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(error=ConnectionError("Connection error.")))
    card = _dictate(p)
    p.transcriber.error = None
    api = api_of(p, "redo_history_item")
    assert api.redo_history_item(card["id"]) == {"ok": False, "reason": "not_found"}
    assert api.redo_history_item(404) == {"ok": False, "reason": "not_found"}
    assert api.redo_history_item("junk")["ok"] is False
    assert history(p) == [card]


@pytest.mark.parametrize("error,reason", [
    (ConnectionError("Connection error."), unsent.REASON_OFFLINE),
    (RuntimeError("Error code: 403 Access denied"), unsent.REASON_BLOCKED),
    (RuntimeError("429 rate limit"), unsent.REASON_RATE_LIMITED),
    (RuntimeError("something odd"), unsent.REASON_ERROR),
])
def test_when_speech_to_text_fails_the_entry_is_left_as_it_was(tmp_path, monkeypatch, error, reason):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    before = history(p)
    usage = []
    p.ns["record_usage_safely"] = lambda *a, **k: usage.append(a)
    p.transcriber.error = error
    r = api_of(p, "redo_history_item").redo_history_item(entry["id"])
    assert r == {"ok": False, "reason": reason}
    assert history(p) == before and usage == [] and len(history_ops.SHELF) == 0


def test_when_nothing_can_be_heard_the_entry_is_left_as_it_was(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    before = history(p)
    p.transcriber.text = ""
    assert api_of(p, "redo_history_item").redo_history_item(entry["id"]) == {"ok": False, "reason": "empty"}
    assert history(p) == before


def test_a_provider_that_never_answers_ends_in_a_timeout_not_a_hang(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    fast_limits(monkeypatch)                             # a 1.5 s deadline
    p, entry = _one(tmp_path)
    p.transcriber.hang = Hang(30)
    t0 = time.monotonic()
    r = api_of(p, "redo_history_item").redo_history_item(entry["id"])
    assert r == {"ok": False, "reason": unsent.REASON_TIMEOUT}
    assert time.monotonic() - t0 < 5
    p.transcriber.hang.release.set()
    assert history(p)[0]["text"] == "ok so ship it on monday"


def test_a_clean_up_that_fails_still_gives_the_words_as_said(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    p.styler = FakeStyler(error=RuntimeError("boom"))
    new = api_of(p, "redo_history_item").redo_history_item(entry["id"])["item"]
    assert new["styled"] == "ok so ship it on tuesday"
    assert new["provider"] == {"stt": "groq", "cleanup": "basic_clean"}


def test_a_trigger_phrase_is_redone_as_its_snippet(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    (tmp_path / "snippets.json").write_text(
        json.dumps([{"trigger": "my address", "expansion": "12 Acacia Avenue"}]), encoding="utf-8")
    p, entry = _one(tmp_path)
    p.transcriber.text = "my address"
    p.styler = FakeStyler(error=RuntimeError("must not be asked"))
    new = api_of(p, "redo_history_item").redo_history_item(entry["id"])["item"]
    assert new["styled"] == "12 Acacia Avenue" and new["provider"]["cleanup"] == "snippet"


def test_paste_last_gives_the_redone_words(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    assert p._last_result.get() == "Ok so ship it on monday."
    api_of(p, "redo_history_item").redo_history_item(entry["id"])
    assert p._last_result.get() == "Ok so ship it on tuesday."


def test_undo_gives_back_the_old_words_and_the_old_paste_last(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    api = api_of(p, "redo_history_item", "undo_history_change")
    r = api.redo_history_item(entry["id"])
    u = api.undo_history_change(r["undo"])
    assert u["ok"] and u["kind"] == "replaced" and u["item"]["styled"] == "Ok so ship it on monday."
    assert history(p) == [entry] and p._last_result.get() == "Ok so ship it on monday."
    assert u["item"]["can_redo"] is True
    assert api.undo_history_change(r["undo"])["ok"] is False


def test_redo_can_be_redone_and_each_undo_goes_one_step_back(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    api = api_of(p, "redo_history_item", "undo_history_change")
    first = api.redo_history_item(entry["id"])
    p.transcriber.text = "third version"
    second = api.redo_history_item(entry["id"])
    assert history(p)[0]["text"] == "third version"
    assert api.undo_history_change(second["undo"])["item"]["text"] == "ok so ship it on tuesday"
    assert api.undo_history_change(first["undo"])["item"]["text"] == "ok so ship it on monday"
    assert history(p) == [entry]


def test_an_undo_after_the_entry_was_deleted_changes_nothing(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    api = api_of(p, "redo_history_item", "undo_history_change", "delete_history_item")
    r = api.redo_history_item(entry["id"])
    api.delete_history_item(entry["id"])
    assert api.undo_history_change(r["undo"]) == {"ok": False, "reason": "not_found"}
    assert history(p) == []


def test_redo_while_another_recording_is_being_sent_says_busy(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)

    class Taken:
        def acquire(self, timeout=None):
            return False

        def release(self):
            raise AssertionError("never taken, never released")
    p._unsent_lock = Taken()
    assert api_of(p, "redo_history_item").redo_history_item(entry["id"]) == {"ok": False, "reason": "busy"}


def test_redo_waits_for_a_dictation_to_finish_rather_than_share_the_clean_up(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    api = api_of(p, "redo_history_item")
    p.is_recording = True
    assert api.redo_history_item(entry["id"]) == {"ok": False, "reason": "busy"}
    p.is_recording = False
    hang = Hang(10)
    p.transcriber.hang = hang
    worker = run_process(p)
    assert hang.entered.wait(5)
    assert api.redo_history_item(entry["id"]) == {"ok": False, "reason": "busy"}
    assert p.transcriber.calls == 2, "only the dictation was sent"
    hang.release.set()
    worker.join(5)
    p.transcriber.hang = None
    assert api.redo_history_item(entry["id"])["ok"] is True


def test_redo_takes_the_same_lock_as_try_again_and_lets_go_of_it(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    api_of(p, "redo_history_item").redo_history_item(entry["id"])
    assert p._unsent_lock.acquire(timeout=0.1)
    p._unsent_lock.release()
    p.transcriber.error = ConnectionError("x")
    api_of(p, "redo_history_item").redo_history_item(entry["id"])
    assert p._unsent_lock.acquire(timeout=0.1)               # also after a failure
    p._unsent_lock.release()


def test_redo_without_a_pipeline_says_not_ready(tmp_path):
    api = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "redo_history_item")
    ns = {"_pipeline": None, "_log_to_file": lambda m: None}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    assert ns["redo_history_item"](None, 1) == {"ok": False, "reason": "not_ready"}


def test_the_log_for_a_redo_has_counts_not_words(tmp_path, monkeypatch):
    _shelf(monkeypatch)
    p, entry = _one(tmp_path)
    api_of(p, "redo_history_item").redo_history_item(entry["id"])
    log = "\n".join(p.logged)
    assert "tuesday" not in log and "monday" not in log and "[redo] done: 6 words" in log


def test_redo_pastes_nothing_by_construction():
    klass = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    fn = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "redo_take")
    fn.body = fn.body[1:]                                  # the docstring says what it does not do
    src = ast.unparse(fn)
    for forbidden in ("clipboard", "paste", "auto_paste", "overlay", "show_toast"):
        assert forbidden not in src, forbidden
