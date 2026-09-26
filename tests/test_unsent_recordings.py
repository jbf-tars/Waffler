"""Never lose a dictation: Not sent recordings can be sent again.

A recording that could not be turned into text was saved to unsent/ and a
card said it was "saved so you can retry", but nothing in the app could reach
the file: one user's unsent/ held five recordings going back months. Now the
card has Try again, Show the file and Delete, and Waffler sends waiting
recordings by itself once the provider answers, within firm limits: only
recordings a Journal entry names, only with the user's own keys (the same
transcriber), from the last day, at most three automatic tries each.

Runs the real app.py methods with fakes (tests/_pipeline_harness.py) and the
page logic in Node. No network, no keys, no private data.
"""
import ast
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta

import pytest

from _pipeline_harness import (ROOT, FakeTranscriber, Hang, fast_limits, history,
                               make_pipeline, run_process, speech_wav, wait_for)
import journal_data
import types
import unsent

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")


def js(expr):
    code = (f"const L = require({json.dumps(str(ROOT / 'ui' / 'logic.js'))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60,
                         encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=5.0, TRANSCRIBE_DEADLINE_BASE_S=5.0,
                TRANSCRIBE_DEADLINE_MAX_S=5.0, STYLE_DEADLINE_S=5.0)


def _fail_one(p, error=ConnectionError("Connection error.")):
    p.transcriber.error = error
    worker = run_process(p)
    worker.join(5)
    assert not worker.is_alive()
    p.transcriber.error = None
    return [e for e in history(p) if e.get("failed")][-1]


# ── the end-to-end promise (the SR3 verify line) ─────────────────────────────

def test_provider_down_then_back_up_try_again_turns_the_card_into_a_normal_entry(tmp_path):
    p = make_pipeline(tmp_path)
    entry = _fail_one(p)

    # Provider down: the WAV is saved and the card is in the Journal.
    uid = entry["unsent_id"]
    wav = tmp_path / "unsent" / uid
    assert wav.is_file() and wav.read_bytes() == p.audio.wav
    assert entry["not_sent_reason"] == "offline" and entry["provider_name"] == "Groq"
    assert p.page.items[-1]["failed"] is True and p.page.items[-1]["unsent_id"] == uid
    assert p.clipboard.pastes == []

    # Provider back up: Try again.
    r = p.resend_unsent(uid)
    assert r["ok"] is True
    [item] = history(p)
    assert "failed" not in item and "unsent_id" not in item and "audio_path" not in item
    assert item["text"] == "ok so ship it on monday"
    assert item["styled"] == "Ok so ship it on monday."
    assert item["timestamp"] == entry["timestamp"], "it stays where it was dictated"
    assert item["sent_later_at"]
    assert not wav.exists(), "the recording is removed once its words are saved"
    assert p.page.updates[-1] == (uid, item)
    # Try again never pastes into whatever the user is doing now.
    assert p.clipboard.copies == [] and p.clipboard.pastes == []


def test_try_again_while_still_down_counts_the_try_and_keeps_the_recording(tmp_path):
    p = make_pipeline(tmp_path)
    entry = _fail_one(p)
    uid = entry["unsent_id"]
    p.transcriber.error = ConnectionError("Connection error.")
    r = p.resend_unsent(uid)
    assert r["ok"] is False and r["reason"] == "offline"
    [still] = history(p)
    assert still["failed"] and still["unsent_id"] == uid
    assert still["last_attempt_at"]
    assert "auto_attempts" not in still or still["auto_attempts"] == 0   # a manual try
    assert (tmp_path / "unsent" / uid).is_file()
    assert p.page.updates[-1][0] == uid


def test_a_recording_with_no_words_in_it_stops_being_retried(tmp_path):
    p = make_pipeline(tmp_path)
    uid = _fail_one(p)["unsent_id"]
    p.transcriber.text = ""
    r = p.resend_unsent(uid)
    assert r["ok"] is False and r["reason"] == "empty"
    [still] = history(p)
    assert still["will_retry"] is False
    assert unsent.auto_retry_due(still, ignore_backoff=True) is False


def test_two_failures_in_the_same_second_keep_both_recordings(tmp_path):
    p = make_pipeline(tmp_path)
    first = p._save_unsent_recording(b"RIFF-one")
    second = p._save_unsent_recording(b"RIFF-two")
    assert first != second
    assert first.read_bytes() == b"RIFF-one" and second.read_bytes() == b"RIFF-two"


def test_delete_removes_the_card_and_the_file(tmp_path):
    p = make_pipeline(tmp_path)
    uid = _fail_one(p)["unsent_id"]
    assert p.delete_unsent(uid) == {"ok": True}
    assert history(p) == []
    assert not (tmp_path / "unsent" / uid).exists()


def test_names_that_point_anywhere_else_are_refused(tmp_path):
    p = make_pipeline(tmp_path)
    _fail_one(p)
    (tmp_path / "history.json").write_text(json.dumps(history(p)), encoding="utf-8")
    before = (tmp_path / "history.json").read_bytes()
    for bad in ("../history.json", "..\\history.json", "recording-x.wav",
                str(tmp_path / "history.json"), "recording-2026-09-26T10-00-00.wav/../../x"):
        assert p.delete_unsent(bad)["ok"] is False
        assert p.resend_unsent(bad)["ok"] is False
    assert (tmp_path / "history.json").read_bytes() == before


# ── sending by itself ────────────────────────────────────────────────────────

def test_waiting_recordings_are_sent_once_the_provider_answers(tmp_path):
    p = make_pipeline(tmp_path)
    a = _fail_one(p)["unsent_id"]
    b = _fail_one(p)["unsent_id"]
    assert a != b
    p._drain_unsent("test", ignore_backoff=True)
    assert all("failed" not in e for e in history(p))
    assert list((tmp_path / "unsent").glob("*.wav")) == []


def test_a_dictation_that_works_sends_the_waiting_ones(tmp_path):
    p = make_pipeline(tmp_path)
    _fail_one(p)
    p._unsent_waiting = 1
    p.clipboard.copies.clear()
    worker = run_process(p)
    worker.join(5)
    assert wait_for(lambda: all("failed" not in e for e in history(p)), timeout=5)
    assert len(history(p)) == 2
    # Only the new dictation was pasted.
    assert p.clipboard.pastes == ["Ok so ship it on monday."]


def test_the_drain_stops_at_the_first_refusal_so_a_down_service_is_not_hammered(tmp_path):
    p = make_pipeline(tmp_path)
    _fail_one(p)
    _fail_one(p)
    p.transcriber.error = ConnectionError("Connection error.")
    calls = p.transcriber.calls
    p._drain_unsent("test", ignore_backoff=True)
    assert p.transcriber.calls == calls + 1


def test_automatic_tries_are_bounded(tmp_path):
    p = make_pipeline(tmp_path)
    uid = _fail_one(p)["unsent_id"]
    p.transcriber.error = ConnectionError("Connection error.")
    for _ in range(unsent.AUTO_MAX_ATTEMPTS + 2):
        p._drain_unsent("test", ignore_backoff=True)
    calls = p.transcriber.calls
    [entry] = history(p)
    assert entry["auto_attempts"] == unsent.AUTO_MAX_ATTEMPTS
    assert entry["will_retry"] is False
    p._drain_unsent("test", ignore_backoff=True)
    assert p.transcriber.calls == calls, "tried again past the limit"
    # Try again on the card still works.
    p.transcriber.error = None
    assert p.resend_unsent(uid)["ok"] is True


def test_old_recordings_wait_for_try_again(tmp_path):
    p = make_pipeline(tmp_path)
    uid = _fail_one(p)["unsent_id"]
    h = history(p)
    h[0]["timestamp"] = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    (tmp_path / "history.json").write_text(json.dumps(h), encoding="utf-8")
    calls = p.transcriber.calls
    p._drain_unsent("test", ignore_backoff=True)
    assert p.transcriber.calls == calls
    assert p.resend_unsent(uid)["ok"] is True


def test_nothing_it_was_not_given_is_ever_uploaded(tmp_path):
    """A WAV in unsent/ that no Journal entry names is never sent."""
    p = make_pipeline(tmp_path)
    (tmp_path / "unsent").mkdir()
    stray = tmp_path / "unsent" / "recording-2026-09-26T09-00-00.wav"
    stray.write_bytes(speech_wav(1.0))
    (tmp_path / "unsent" / "notes.wav").write_bytes(b"RIFF")
    p._drain_unsent("test", ignore_backoff=True)
    assert p.transcriber.calls == 0
    assert stray.exists()


def test_the_drain_never_competes_with_a_dictation(tmp_path):
    p = make_pipeline(tmp_path)
    _fail_one(p)
    calls = p.transcriber.calls
    p.is_recording = True
    p._drain_unsent("test", ignore_backoff=True)
    assert p.transcriber.calls == calls
    p.is_recording = False
    run = p._watchdog.begin(99)          # a dictation being processed
    p._drain_unsent("test", ignore_backoff=True)
    assert p.transcriber.calls == calls
    p._watchdog.end(run, "done")


def test_retries_use_the_same_transcriber_the_dictation_used():
    """No other client is built: the user's keys, and only theirs."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")

    def method(name):
        return ast.unparse(next(n for n in klass.body
                                if isinstance(n, ast.FunctionDef) and n.name == name))

    resend, fill = method("resend_unsent"), method("_fill_unsent_card")
    assert "self._transcribe_with_provenance" in resend and "self._fill_unsent_card" in resend
    assert "self.styler.style" in fill
    # Every path that sends a recording or fills its card: the resend, the
    # card, and a late answer to a request Waffler stopped waiting for.
    for src in (resend, fill, method("_late_words_arrived"), method("_collect_late_words")):
        for forbidden in ("OpenAI(", "Groq(", "WhisperTranscriber(", "requests.", "auto_paste",
                          "clipboard"):
            assert forbidden not in src, forbidden


# ── the rules (src/unsent.py) ────────────────────────────────────────────────

def test_failure_reasons_are_sorted_for_plain_messages():
    c = unsent.classify_reason
    assert c("Error code: 403 - Access denied") == "blocked"
    assert c("Error code: 429 - rate limit") == "rate_limited"
    assert c("Request timed out.") == "timeout"
    assert c("deadline: no answer in 45s") == "timeout"
    assert c("Connection error.") == "offline"
    assert c("no transcription backend available") == "offline"
    assert c("later") == "later"
    assert c("stuck") == "stuck"
    assert c("KeyError: 'text'") == "error"


def test_ids_have_one_shape():
    ok = unsent.is_valid_id
    assert ok("recording-2026-09-26T10-04-31.wav")
    assert ok("recording-2026-09-26T10-04-31-2.wav")
    for bad in ("", "../x.wav", "recording-2026-09-26T10-04-31.wav.exe", "x/recording-2026-09-26T10-04-31.wav",
                "recording-2026-09-26T10-04-31.WAV ", None, 5):
        assert not ok(bad), bad


def test_an_older_entry_is_found_by_its_file_name():
    old = {"failed": True, "audio_path": "C:\\Users\\x\\.waffler-hosted\\unsent\\recording-2026-06-05T09-12-44.wav"}
    assert unsent.entry_id(old) == "recording-2026-06-05T09-12-44.wav"
    mac = {"failed": True, "audio_path": "/Users/x/.waffler-hosted/unsent/recording-2026-06-05T09-12-44.wav"}
    assert unsent.entry_id(mac) == "recording-2026-06-05T09-12-44.wav"
    assert unsent.entry_id({"failed": True, "audio_path": ""}) == ""
    assert unsent.entry_id({"text": "fine", "unsent_id": "recording-2026-06-05T09-12-44.wav"}) == ""


def test_the_automatic_schedule():
    now = datetime(2026, 9, 26, 12, 0, 0)
    fresh = {"failed": True, "timestamp": (now - timedelta(seconds=30)).isoformat()}
    assert unsent.auto_retry_due(fresh, now) is False               # first wait: 60 s
    assert unsent.auto_retry_due(fresh, now, ignore_backoff=True) is True
    fresh["timestamp"] = (now - timedelta(seconds=61)).isoformat()
    assert unsent.auto_retry_due(fresh, now) is True
    once = unsent.with_attempt(fresh, auto=True, reason="offline", now=now)
    assert once["auto_attempts"] == 1 and once["will_retry"] is True
    assert unsent.auto_retry_due(once, now + timedelta(seconds=200)) is False   # then 5 min
    assert unsent.auto_retry_due(once, now + timedelta(seconds=301)) is True
    stale = {"failed": True, "timestamp": (now - timedelta(hours=25)).isoformat()}
    assert unsent.auto_retry_due(stale, now, ignore_backoff=True) is False
    assert unsent.will_auto_retry(stale, now) is False


def test_the_messages_are_plain_and_name_the_provider():
    heading, body = unsent.toast_text("offline", "Groq")
    assert heading == "Not sent"
    assert body == ("Waffler couldn't reach Groq. Your recording is saved in the Journal, "
                    "and Waffler will send it when you're back online.")
    assert unsent.toast_text("blocked", "")[1].startswith("Your speech service refused")
    for reason in ("offline", "blocked", "timeout", "rate_limited", "later", "stuck", "error"):
        for text in unsent.toast_text(reason, "Groq"):
            assert "\u2014" not in text


# ── the window ───────────────────────────────────────────────────────────────

def _lift_api(name):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    return next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_journal_gets_each_cards_live_state(tmp_path):
    fn = _lift_api("get_history")
    unsent_dir = tmp_path / "unsent"
    unsent_dir.mkdir()
    (unsent_dir / "recording-2026-09-26T10-00-00.wav").write_bytes(b"RIFF")
    now = datetime.now().isoformat(timespec="seconds")
    items = [
        {"timestamp": "2026-06-05T09:12:44", "failed": True, "text": "", "styled": "x",
         "audio_path": str(unsent_dir / "recording-2026-06-05T09-12-44.wav")},   # file gone
        {"timestamp": now, "failed": True, "text": "", "styled": "x",
         "unsent_id": "recording-2026-09-26T10-00-00.wav"},
        {"timestamp": now, "text": "hello", "styled": "Hello."},
    ]
    # The window reads history through the cache (src/journal_data.py).
    cached = [dict(i) for i in items]
    ns = {"_history_cache": types.SimpleNamespace(items=lambda: cached),
          "_journal": journal_data, "DATA_DIR": tmp_path, "_unsent": unsent}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    out = ns["get_history"](None)
    assert [o["timestamp"] for o in out] == [now, now, "2026-06-05T09:12:44"]
    assert out[0] == items[2]
    assert out[1]["unsent_id"] == "recording-2026-09-26T10-00-00.wav" and out[1]["will_retry"] is True
    assert out[2]["unsent_id"] == "" and out[2]["will_retry"] is False
    # The live state goes on copies: the cached entries are left as they were.
    assert cached == items
    # A page carries the live state too.
    assert [o["timestamp"] for o in ns["get_history"](None, 1, 1)] == [now]
    assert ns["get_history"](None, 1, 1)[0]["will_retry"] is True


def test_a_not_sent_note_adds_no_words_to_the_counts(tmp_path):
    fn = _lift_api("get_stats")
    from datetime import date
    today = date.today().isoformat()
    entries = [
        {"timestamp": f"{today}T09:00:00", "styled": "one two three"},
        {"timestamp": f"{today}T09:01:00", "styled": unsent.STYLED_NOTE, "failed": True},
    ]
    ns = {"_history_cache": types.SimpleNamespace(stats=lambda d: journal_data.compute_stats(entries, d)),
          "date": date}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    stats = ns["get_stats"](None)
    assert stats["today_words"] == 3 and stats["total_words"] == 3


@needs_node
def test_the_card_says_what_happened_in_plain_words():
    v = js("L.notSentView({failed: true, unsent_id: 'recording-2026-09-26T10-04-31.wav',"
           " not_sent_reason: 'offline', provider_name: 'Groq', will_retry: true})")
    assert v == {
        "id": "recording-2026-09-26T10-04-31.wav", "badge": "Not sent",
        "text": "Waffler couldn't reach Groq, so this wasn't turned into text. "
                "The recording is saved on this computer.",
        "next": "Waffler will send it by itself when Groq answers.",
        "canRetry": True, "canReveal": True, "canDelete": True,
    }


@needs_node
def test_an_older_card_is_read_from_its_raw_error_and_offers_try_again():
    v = js("L.notSentView({failed: true, error: 'Error code: 403 - Access denied',"
           " audio_path: 'C:/Users/x/.waffler-hosted/unsent/recording-2026-06-05T09-12-44.wav'})")
    assert v["id"] == "recording-2026-06-05T09-12-44.wav"
    assert v["text"].startswith("Your speech service refused the connection")
    assert v["next"] == "Press Try again to send it."
    assert "403" not in v["text"]


@needs_node
def test_a_card_whose_recording_is_gone_can_only_be_deleted():
    v = js("L.notSentView({failed: true, unsent_id: '',"
           " audio_path: 'C:/x/unsent/recording-2026-06-05T09-12-44.wav'})")
    assert v["canRetry"] is False and v["canReveal"] is False and v["canDelete"] is True


@needs_node
def test_settings_shows_the_count():
    assert js("L.unsentSummary({count: 0})")["canSend"] is False
    two = js("L.unsentSummary({count: 2, provider: 'Groq'})")
    assert two == {"label": "2 recordings are waiting to be sent. Waffler sends them when Groq "
                            "answers, or you can send them now.", "canSend": True}
    assert js("L.unsentSummary({count: 1, provider: 'Groq'})")["label"].startswith("1 recording is")


@needs_node
def test_the_window_pill_counts_while_working_and_every_ending_has_a_label():
    assert js("L.workingLabel('Cleaning up', 0.6)") == "Cleaning up"
    assert js("L.workingLabel('Cleaning up', 4.2)") == "Cleaning up · 4 s"
    assert js("['cancelled','not_sent','error'].map((s) => L.statusView(s).label)") == [
        "Cancelled", "Not sent", "Something went wrong"]
    assert js("['done','cancelled','not-sent','error','processing'].map(L.statusResetMs)") == [
        3000, 3000, 6000, 6000, 0]


def test_the_journal_card_and_settings_are_wired_up():
    app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    # Not sent entries get their own card: no Copy button for text that
    # does not exist, and the three actions go to the backend.
    make_card = app[app.index("function makeCard(item, isNew)"):]
    assert make_card.index("if (item && item.failed) return makeNotSentCard(item, isNew);") < 200
    for call in ("retry_unsent(", "reveal_unsent(", "delete_unsent(", "get_unsent_summary(",
                 "retry_all_unsent("):
        assert call in app, call
    assert "window.waffler_item_updated" in app
    assert 'id="unsentRow"' in html and 'id="unsentSummary"' in html
    # A Not sent card arriving is not "Transcription complete".
    refresh = app[app.index("window.waffler_refresh"):]
    refresh = refresh[:refresh.index("\n};")]
    assert "newItem.failed" in refresh


# ── Answers that arrive after Waffler stopped waiting (review of Round A) ────
# The request given up on at the deadline, or after "Send later", runs on
# until its own timeout (60 s or more) and is billed if it succeeds. Its words
# were thrown away and the automatic resend then sent (and billed) the same
# audio again, up to three times plus Try again and Send now.

def _given_up(tmp_path, monkeypatch, text="ok so ship it on monday"):
    """A dictation whose speech request outlives the deadline."""
    fast_limits(monkeypatch)                     # 1.5 s deadline
    hang = Hang(30)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(text=text, hang=hang))
    worker = run_process(p)
    worker.join(5)
    assert not worker.is_alive()
    [entry] = history(p)
    assert entry["failed"] and entry["not_sent_reason"] == "timeout"
    assert p.transcriber.calls == 1
    return p, hang, entry["unsent_id"]


def test_a_request_given_up_on_fills_the_card_when_it_answers(tmp_path, monkeypatch):
    p, hang, uid = _given_up(tmp_path, monkeypatch)
    hang.release.set()
    assert wait_for(lambda: "failed" not in history(p)[0], timeout=5)
    [item] = history(p)
    assert item["text"] == "ok so ship it on monday"
    assert item["styled"] == "Ok so ship it on monday." and item["sent_later_at"]
    assert not (tmp_path / "unsent" / uid).exists()
    assert p.page.updates[-1] == (uid, item)
    assert p.transcriber.calls == 1, "the recording was sent twice"
    assert p.clipboard.pastes == []
    assert wait_for(lambda: p._in_flight(uid) is None and not p._unsent_in_flight, timeout=2)


def test_nothing_sends_it_again_while_that_request_runs(tmp_path, monkeypatch):
    p, hang, uid = _given_up(tmp_path, monkeypatch)
    for _ in range(3):
        p._drain_unsent("test", ignore_backoff=True)
    assert p.transcriber.calls == 1
    assert p.resend_unsent(uid, auto=True)["reason"] == "in_flight"
    assert p.transcriber.calls == 1
    hang.release.set()
    assert wait_for(lambda: "failed" not in history(p)[0], timeout=5)


def test_try_again_waits_for_that_request_instead_of_paying_twice(tmp_path, monkeypatch):
    p, hang, uid = _given_up(tmp_path, monkeypatch)
    result = {}
    t = threading.Thread(target=lambda: result.update(p.resend_unsent(uid)), daemon=True)
    t.start()
    time.sleep(0.3)
    assert t.is_alive(), "Try again should wait for the request already on its way"
    hang.release.set()
    t.join(5)
    assert result["ok"] is True
    assert result["item"]["text"] == "ok so ship it on monday"
    assert p.transcriber.calls == 1
    assert "failed" not in history(p)[0]


def test_send_later_keeps_the_request_and_its_answer_fills_the_card(tmp_path, monkeypatch):
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=5.0, TRANSCRIBE_DEADLINE_BASE_S=5.0,
                TRANSCRIBE_DEADLINE_MAX_S=5.0)
    hang = Hang(30)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    worker = run_process(p)
    assert wait_for(lambda: p.overlay.toasts(), timeout=3)
    p._on_toast_action("send_later")
    worker.join(3)
    assert history(p)[0]["not_sent_reason"] == "later"
    hang.release.set()
    assert wait_for(lambda: "failed" not in history(p)[0], timeout=5)
    assert p.transcriber.calls == 1 and p.clipboard.pastes == []


def test_a_late_request_that_fails_leaves_the_card_for_the_next_try(tmp_path, monkeypatch):
    p, hang, uid = _given_up(tmp_path, monkeypatch)
    p.transcriber.error = ConnectionError("Connection error.")
    hang.release.set()
    assert wait_for(lambda: not p._unsent_in_flight, timeout=5)
    assert history(p)[0]["failed"] and history(p)[0]["unsent_id"] == uid
    p.transcriber.error = None
    p._drain_unsent("test", ignore_backoff=True)
    assert p.transcriber.calls == 2
    assert "failed" not in history(p)[0]


def test_a_late_answer_with_no_words_stops_the_retries(tmp_path, monkeypatch):
    p, hang, uid = _given_up(tmp_path, monkeypatch, text="")
    hang.release.set()
    assert wait_for(lambda: history(p)[0].get("not_sent_reason") == "empty", timeout=5)
    assert history(p)[0]["will_retry"] is False
    p._drain_unsent("test", ignore_backoff=True)
    assert p.transcriber.calls == 1


def test_a_late_answer_during_a_dictation_goes_in_as_said(tmp_path, monkeypatch):
    """It never competes with a dictation for the clean-up."""
    p, hang, uid = _given_up(tmp_path, monkeypatch)
    styled_before = p.styler.calls
    run = p._watchdog.begin(99)                 # a dictation being processed
    hang.release.set()
    assert wait_for(lambda: "failed" not in history(p)[0], timeout=5)
    p._watchdog.end(run, "done")
    assert history(p)[0]["styled"] == "ok so ship it on monday"
    assert p.styler.calls == styled_before


def test_a_card_deleted_while_its_request_runs_stays_deleted(tmp_path, monkeypatch):
    p, hang, uid = _given_up(tmp_path, monkeypatch)
    assert p.delete_unsent(uid)["ok"] is True
    hang.release.set()
    assert wait_for(lambda: not p._unsent_in_flight, timeout=5)
    assert history(p) == []


def test_a_resend_given_up_on_is_kept_too(tmp_path, monkeypatch):
    """A resend that hits its own deadline repeated the pattern."""
    fast_limits(monkeypatch)
    p = make_pipeline(tmp_path)
    uid = _fail_one(p)["unsent_id"]
    hang = Hang(30)
    p.transcriber.hang = hang
    r = p.resend_unsent(uid, auto=True)
    assert r["ok"] is False and r["reason"] == "timeout"
    assert p._in_flight(uid) is not None
    p._drain_unsent("test", ignore_backoff=True)
    calls = p.transcriber.calls
    hang.release.set()
    assert wait_for(lambda: "failed" not in history(p)[0], timeout=5)
    assert p.transcriber.calls == calls


# ── cancelled with Esc ───────────────────────────────────────────────────────

def test_a_recording_cancelled_with_esc_waits_for_try_again():
    now = datetime.now()
    entry = {"failed": True, "timestamp": now.isoformat(timespec="seconds"),
             "not_sent_reason": "cancelled"}
    assert unsent.classify_reason("cancelled") == "cancelled"
    assert unsent.will_auto_retry(entry, now) is False
    assert unsent.auto_retry_due(entry, now, ignore_backoff=True) is False
    assert unsent.is_waiting(entry) is False
    heading, body = unsent.toast_text("cancelled", "Groq")
    assert heading == "Cancelled" and "Journal" in body and "pasted" in body
    # A Try again that fails makes it an ordinary Not sent recording.
    tried = unsent.with_attempt(entry, auto=False, reason="offline", now=now)
    assert unsent.is_waiting(tried) and unsent.will_auto_retry(tried, now)


@pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")
def test_the_cancelled_card_says_esc_and_offers_try_again():
    v = js("L.notSentView({failed: true, unsent_id: 'recording-2026-09-26T10-04-31.wav', "
           "not_sent_reason: 'cancelled', will_retry: false, provider_name: 'Groq'})")
    assert "Esc" in v["text"] and "nothing was pasted" in v["text"]
    assert v["canRetry"] is True and v["next"] == "Press Try again to send it."
