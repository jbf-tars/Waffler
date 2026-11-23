"""Meetings through Waffler Plus: no key and no model on this computer.

With Plus the chosen way (settings.json "dictation_plus") and an account that
has it, a meeting's audio is written down by the account service (POST
/api/plus/transcribe), in parts when it is long, and its notes, title, Ask
and "Write notes" go to POST /api/plus/write with the app's own prompts
(src/meetings/llm.py). The branch sits where the engine already chooses
between a key and the model on this computer: transcribe() and
resolve_provider()/complete().

The real engine runs here against a stand-in for the account service
(tests/_account_fake.py): no network. Keys ARE set and every provider and
both local models raise if asked: Plus chosen means none of them is touched.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import local_llm  # noqa: E402
import local_stt  # noqa: E402
import meetings_bridge  # noqa: E402
import meetings_ctl  # noqa: E402
from meetings import llm, paths, recorder, transcribe as mt  # noqa: E402
from meetings.service import Service  # noqa: E402
from meetings.store import Store, wait_until  # noqa: E402
from _account_fake import FakeAccountServer  # noqa: E402
from src import account  # noqa: E402

RATE = 16000
SERVER = "https://notes.example.test"
EMAIL = "ada@example.com"
KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NATTER_CUSTOM_API_KEY")
NOTES = "TITLE: Kitchen plans\n\n### Decisions\n- The cabinets arrive on the 14th."


def _speech(seconds=6.0):
    """Bursts of tone with gaps: enough voice for the engine's voice check."""
    t = np.arange(int(seconds * RATE)) / RATE
    tone = 0.2 * np.sin(2 * np.pi * 220 * t)
    gate = (np.sin(2 * np.pi * 0.8 * t) > -0.3).astype(np.float32)
    return (tone * gate).astype(np.float32)


def _never(*a, **k):
    raise AssertionError("a key's provider or a model on this computer was asked while Plus is chosen")


@pytest.fixture
def plus(monkeypatch, _isolated_data_dir, tmp_path):
    """Signed in to an account that has Plus, with Plus the chosen way. Keys
    are set, and nothing that would use one may be called."""
    fake = FakeAccountServer(contract="new")
    monkeypatch.setenv(account.SERVER_ENV, SERVER)
    monkeypatch.setattr(account, "_transport", fake.transport())
    monkeypatch.setattr(account, "_notice", "")
    monkeypatch.setattr(account, "_plan", {})
    assert account.start(EMAIL)["ok"]
    assert account.verify(EMAIL, fake.mailbox[EMAIL], "Ada's Mac")["ok"]
    fake.give_plus(EMAIL)
    assert account.refresh_plan()["plan"] == "plus"
    fake.data = _isolated_data_dir
    (fake.data / "settings.json").write_text(json.dumps({account.PLUS_SETTING: True}), encoding="utf-8")
    monkeypatch.setenv("NATTER_NO_KEYRING", "1")
    monkeypatch.setenv("WAFFLER_MEETINGS_HOME", str(tmp_path / "meetings"))
    monkeypatch.setattr(mt.keys, "_memory", {})
    monkeypatch.setattr(meetings_ctl, "_share_waffler_keys", lambda: None)
    for name in KEYS:
        monkeypatch.setenv(name, "test-not-a-real-key")
    # The engine exists twice in a test run (as `meetings` and, for the
    # controller, as `src.meetings`): neither copy may ask anyone.
    for engine in (llm, meetings_ctl._engine("llm")):
        for name in ("_anthropic_complete", "_openai_complete", "ollama_complete", "list_models",
                     "_local_complete"):
            monkeypatch.setattr(engine, name, _never)
    for engine in (mt, meetings_ctl._engine("transcribe")):
        monkeypatch.setattr(engine, "_call_openai_compatible", _never)
        monkeypatch.setattr(engine.keys, "_memory", {})
    monkeypatch.setattr(local_stt, "transcribe_segments", _never)
    monkeypatch.setattr(local_llm, "chat", _never)
    fake.slept = []                                    # the long waits the service asked for, not taken
    nap = mt.time.sleep
    monkeypatch.setattr(mt.time, "sleep", lambda s: fake.slept.append(s) if s >= 1 else nap(s))
    fake.write_reply = lambda body: NOTES
    return fake


def _meeting(service, audio=None, wait=True):
    note = service.store.create_note(template_id="general")
    service.ingest_audio(note["id"], _speech() if audio is None else audio)
    if wait:
        assert wait_until(lambda: service.store.get_note(note["id"])["status"] == "ready", timeout=30)
    return service.store.get_note(note["id"], with_segments=True)


def _waiting(note_id):
    return recorder.waiting_files(note_id)


# ── a short meeting ──────────────────────────────────────────────────────────

def test_a_short_meeting_is_written_down_and_its_notes_written_by_the_service(plus):
    plus.hear = lambda seconds, n: [{"start_s": 0.5, "end_s": 2.0, "text": "The cabinets arrive on the fourteenth."},
                                    {"start_s": 2.5, "end_s": 4.0, "text": "Tom books the electrician."}]
    service = Service(store=Store())
    note = _meeting(service)
    assert [s["text"] for s in note["segments"]] == ["The cabinets arrive on the fourteenth.",
                                                     "Tom books the electrician."]
    assert (note["segments"][0]["start_s"], note["segments"][1]["end_s"]) == (0.5, 4.0)
    assert len(plus.transcriptions) == 1, "one upload for a short meeting"
    sent = plus.transcriptions[0]
    assert sent["file"]["type"] == "audio/flac" and 5.5 < sent["seconds"] < 6.5 and sent["language"] == "en"
    # The notes and the title: the app's own prompt, the service's model.
    assert note["title"] == "Kitchen plans" and "cabinets arrive on the 14th" in note["summary"]
    assert note["error"] == "" and len(plus.writes) == 1
    asked = plus.writes[0]
    assert asked["system"] == llm.NOTES_SYSTEM, "the prompts live in the app"
    assert "Tom books the electrician." in asked["messages"][0]["content"]
    assert service.has_audio(note["id"]) and _waiting(note["id"]) == [], "the audio is kept, and none of it waits"


def test_nothing_is_sent_while_the_chunks_arrive_and_one_part_goes_at_the_end(plus):
    service = Service(store=Store())
    note = _meeting(service, _speech(70.0))            # three chunks or so as it is recorded
    assert len(list(paths.audio_dir(note["id"]).glob("*.flac"))) >= 3
    assert len(plus.transcriptions) == 1 and 69 < plus.transcriptions[0]["seconds"] < 71


def test_with_no_audio_kept_the_chunks_go_once_they_are_written_down(plus):
    service = Service(store=Store())
    service.store.set_settings({"keep_audio": False})
    note = _meeting(service)
    assert note["segments"] and not service.has_audio(note["id"])


def test_a_line_the_service_wrote_over_silence_never_reaches_the_transcript_or_the_notes(plus):
    """A Plus part has silence where chunks with no voice were never kept
    (recorder._joined), and the service's answer carries no confidence
    numbers. A line written in that silence, in any language, is dropped by
    what the audio shows (Nov 2026: a meeting's notes talked about "Norwegian
    phrases" nobody said; tests/test_meetings_invented_lines.py)."""
    plus.hear = lambda seconds, n: [
        {"start_s": 0.5, "end_s": 4.0, "text": "The cabinets arrive on the fourteenth."},
        {"start_s": 20.0, "end_s": 22.5, "text": "Teksting av Nicolai Winther"},
        {"start_s": seconds - 5.5, "end_s": seconds - 2.5, "text": "Tom books the electrician."}]
    audio = np.concatenate([_speech(6.0), np.zeros(34 * RATE, dtype=np.float32), _speech(6.0)])
    note = _meeting(Service(store=Store()), audio)
    assert len(plus.transcriptions) == 1, "one part"
    assert [s["text"] for s in note["segments"]] == ["The cabinets arrive on the fourteenth.",
                                                     "Tom books the electrician."]
    assert note["segments"][1]["start_s"] > 40.0, "put back where it was said on the meeting's clock"
    assert "Teksting" not in plus.writes[0]["messages"][0]["content"], "the notes never see it"


def test_silence_between_kept_chunks_is_not_sent_and_the_times_go_back_where_they_were_said(plus):
    """Chunks with no voice are never kept; the gap they leave is not sent
    (it costs the account's minutes and invites invented lines). Each line's
    times are put back on the meeting's own clock."""
    plus.hear = lambda seconds, n: [
        {"start_s": 0.5, "end_s": 4.0, "text": "The cabinets arrive on the fourteenth."},
        {"start_s": seconds - 5.5, "end_s": seconds - 2.5, "text": "Tom books the electrician."}]
    audio = np.concatenate([_speech(6.0), np.zeros(240 * RATE, dtype=np.float32), _speech(6.0)])
    note = _meeting(Service(store=Store()), audio)
    assert len(plus.transcriptions) == 1
    assert plus.transcriptions[0]["seconds"] < 100, "four minutes of silence did not go"
    first, second = note["segments"]
    assert first["start_s"] == pytest.approx(0.5, abs=0.05)
    assert second["start_s"] > 240.5 and second["end_s"] > second["start_s"]
    assert second["end_s"] <= 252.1


def test_packing_leaves_only_a_small_margin_and_maps_times_back(tmp_path):
    import soundfile as sf
    a, b = tmp_path / "a.flac", tmp_path / "b.flac"
    sf.write(str(a), _speech(4.0), RATE)
    sf.write(str(b), _speech(4.0), RATE)
    audio, spans = recorder._packed([(10.0, a), (100.0, b), (104.0, tmp_path / "b.flac")], 10.0)
    assert len(audio) / RATE == pytest.approx(4.0 + recorder.PACK_GAP_S + 4.0 + 4.0, abs=0.01), "touching chunks get no gap"
    assert recorder.unpacked(1.0, spans) == pytest.approx(1.0)
    assert recorder.unpacked(4.5 + 1.0, spans) == pytest.approx(90.0 + 1.0)
    assert recorder.unpacked(4.5, spans) == pytest.approx(90.0)
    assert recorder.unpacked(4.2, spans) == pytest.approx(4.0), "in the margin: the edge of the chunk before"
    assert recorder.unpacked(4.0, spans, end=True) == pytest.approx(4.0)


# ── a long meeting, in parts ─────────────────────────────────────────────────

def test_a_part_is_ten_to_fifteen_minutes():
    assert 600 <= mt.PLUS_PART_S <= 900


def test_a_long_meeting_goes_in_parts_with_the_times_put_back_and_progress_said(plus, monkeypatch):
    monkeypatch.setattr(mt, "PLUS_PART_S", 60.0)       # a minute a part, so the test is short
    service = Service(store=Store())
    said = []

    def hear(seconds, n):
        said.append(service.state()["progress"])
        return [{"start_s": 1.0, "end_s": 2.0, "text": f"Part {n} speaking."}]
    plus.hear = hear
    note = _meeting(service, _speech(150.0))
    n = len(plus.transcriptions)
    assert n >= 3 and all(t["seconds"] <= 60.0 for t in plus.transcriptions), "no part is longer than a part"
    assert abs(sum(t["seconds"] for t in plus.transcriptions) - 150.0) < 1.0, "all of it went, once"
    # Where each part began on the meeting's own clock: the first chunk in it.
    starts = sorted(int(f.name.split("_")[1]) / 1000 for f in paths.audio_dir(note["id"]).glob("*.flac"))
    begins, at = [0.0], 0.0
    for t in plus.transcriptions[:-1]:
        at += t["seconds"]
        begins.append(min(starts, key=lambda s: abs(s - at)))
    segs = note["segments"]
    assert [s["text"] for s in segs] == [f"Part {i} speaking." for i in range(1, n + 1)]
    assert [s["start_s"] for s in segs] == pytest.approx([b + 1.0 for b in begins], abs=0.01)
    assert [s["end_s"] for s in segs] == pytest.approx([b + 2.0 for b in begins], abs=0.01)
    assert begins[1] > 30 and begins == sorted(begins)
    assert said == [{note["id"]: f"Part {i} of {n}"} for i in range(1, n + 1)]
    assert service.state()["progress"] == {}, "nothing is in progress once it is done"
    assert note["summary"] and note["error"] == ""


def test_the_microphone_and_the_speakers_go_as_one_part_each_and_an_echo_is_dropped(plus):
    plus.hear = lambda seconds, n: [{"start_s": 1.0, "end_s": 3.0, "text": "We agreed the budget is nine thousand."}]
    service = Service(store=Store())
    note = service.store.create_note(template_id="general")
    session = service.store.start_session(note["id"])
    rec = service.recorder
    rec._finishing.add(note["id"])
    with rec._pending_lock:
        rec._pending += 1
    rec._jobs.put(recorder._Job(note["id"], session["n"], 0.0, {"mic": _speech(), "system": _speech()}))
    service.store.end_session(session["id"], note["id"], 6.0)
    service.store.update_note(note["id"], status="processing")
    rec._jobs.put(("done", note["id"]))
    assert wait_until(lambda: service.store.get_note(note["id"])["status"] == "ready", timeout=30)
    segs = service.store.segments(note["id"])
    assert len(plus.transcriptions) == 2
    assert [s["source"] for s in segs] == ["system"], "the microphone only heard the speakers"


# ── notes, the title, Ask and Write notes ────────────────────────────────────

def test_ask_and_write_notes_go_to_the_service_and_the_answer_arrives_whole(plus):
    service = Service(store=Store())
    note = _meeting(service)
    plus.write_reply = lambda body: "On the 14th."
    heard = []
    out = service.ask("When do the cabinets arrive?", note["id"], on_text=heard.append)
    assert out["answer"] == "On the 14th." and heard == [], "no streaming: the answer is the whole text"
    asked = plus.writes[-1]
    assert asked["system"] == llm.ASK_SYSTEM and asked["effort"] == "low"
    assert asked["messages"][-1] == {"role": "user", "content": "When do the cabinets arrive?"}
    assert asked["messages"][0]["role"] == "user" and "<material>" in asked["messages"][0]["content"]
    # "Write notes" again, with another template.
    plus.write_reply = lambda body: "TITLE: Second go\n\n- Rewritten."
    service.enhance(note["id"], wait=True)
    assert service.store.get_note(note["id"])["summary"] == "- Rewritten."
    assert plus.writes[-1]["system"] == llm.NOTES_SYSTEM and len(plus.transcriptions) == 1


def test_a_long_transcript_is_condensed_in_parts_through_the_service(plus, monkeypatch):
    monkeypatch.setitem(llm._PART_CHARS, "plus", 60)
    plus.hear = lambda seconds, n: [{"start_s": float(i), "end_s": i + 0.5, "text": f"Point number {i} was made."}
                                    for i in range(5)]
    service = Service(store=Store())
    note = _meeting(service)
    assert len(plus.writes) >= 3 and note["summary"]
    assert plus.writes[0]["system"].startswith("Condense this part") and plus.writes[-1]["system"] == llm.NOTES_SYSTEM


def test_the_page_is_told_plus_does_both_jobs(plus):
    ctl = meetings_ctl.MeetingsController()
    try:
        r = meetings_bridge.handle(ctl, "GET", "/app", None, waffler_theme="dark")
        assert r["status"] == 200
        assert r["body"]["local"] == {"speech": True, "writing": True, "plus": True}
        # Signed out: what this computer does by itself, exactly as before.
        plus.sessions.clear()
        account.forget()
        r = meetings_bridge.handle(ctl, "GET", "/app", None, waffler_theme="dark")
        assert "plus" not in r["body"]["local"]
    finally:
        ctl.shutdown()


def test_the_page_can_ask_for_notes_and_never_hears_notes_need_a_key(plus):
    ctl = meetings_ctl.MeetingsController()
    try:
        service = ctl._svc()
        note = _meeting(service)
        r = meetings_bridge.handle(ctl, "POST", f"/notes/{note['id']}/enhance", {}, waffler_theme="dark")
        assert r["status"] == 200
        assert wait_until(lambda: service.store.get_note(note["id"])["status"] == "ready", timeout=30)
        got = meetings_bridge.handle(ctl, "GET", f"/notes/{note['id']}", None, waffler_theme="dark")["body"]
        assert got["error"] == "" and got["summary"]
        assert service.store.get_note(note["id"])["error"] != llm.NOTES_NEED_KEY
    finally:
        ctl.shutdown()


# ── refusals ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("refusal, sentence", [
    ((402, "The month's minutes are used up.", "minutes_cap"), account.PLUS_MINUTES_USED),
    ((402, "You have used this month's Waffler Plus.", "month_used"), account.PLUS_MONTH_USED),
    ((413, "That recording is too large.", "audio_too_large"), account.DICTATE_TOO_LARGE),
    ((400, "That file is not audio."), account.DICTATE_UNREADABLE),
    ((503, "The transcription failed.", "transcribe_failed", {"Retry-After": "5"}), account.TRANSCRIBE_FAILED),
    ((504, "Send it in shorter parts.", "transcribe_timeout"), account.TRANSCRIBE_TIMEOUT),
    ((507, "The server is full.", "server_full"), account.PLUS_FULL),
    ((503, "Busy.", "", {"Retry-After": "5"}), account.TRANSCRIBE_BUSY),
])
def test_a_recording_the_service_refuses_keeps_its_audio_and_says_why(plus, refusal, sentence):
    plus.transcribe_refusals = [refusal]
    service = Service(store=Store())
    note = _meeting(service)
    assert note["status"] == "ready" and note["segments"] == [] and note["error"] == sentence
    assert service.has_audio(note["id"]) and _waiting(note["id"]), "kept, to try again"
    assert plus.writes == [], "no notes from a meeting that was not written down"
    # "Try the transcription again": from the start, and then the notes.
    service.retranscribe(note["id"])
    assert wait_until(lambda: bool(service.store.get_note(note["id"])["summary"]), timeout=30)
    after = service.store.get_note(note["id"], with_segments=True)
    assert after["segments"] and after["error"] == "" and _waiting(note["id"]) == []


def test_no_internet_keeps_the_meeting_and_no_key_is_used_instead(plus):
    plus.offline = True
    service = Service(store=Store())
    note = _meeting(service)
    assert note["error"] == account.PLUS_OFFLINE and note["segments"] == [] and service.has_audio(note["id"])
    plus.offline = False
    service.retranscribe(note["id"])
    assert wait_until(lambda: bool(service.store.get_note(note["id"])["summary"]), timeout=30)


def test_too_many_in_a_minute_waits_as_long_as_the_service_says_and_goes_on(plus):
    plus.transcribe_refusals = [(429, "Too many.", "", {"Retry-After": "20"})]
    plus.write_refusals = [(429, "Too many.", "", {"Retry-After": "7"})]
    service = Service(store=Store())
    note = _meeting(service)
    assert note["segments"] and note["summary"] and note["error"] == ""
    assert plus.slept == [20.0, 7.0]


@pytest.mark.parametrize("refusal, sentence", [
    ((402, "Today's requests are used.", "model_calls_cap"), account.MODEL_CALLS_CAP),
    ((402, "Too many characters today.", "model_chars_cap"), account.WRITING_CAP),
    ((413, "Too many characters.", "too_long"), account.TOO_LARGE),
    ((422, "The model declined.", "write_refused"), account.WRITE_REFUSED),
    ((503, "The model could not be reached.", "write_failed"), account.WRITE_FAILED),
])
def test_notes_the_service_refuses_leave_the_transcript_and_one_sentence(plus, refusal, sentence):
    plus.write_refusals = [refusal]
    service = Service(store=Store())
    note = _meeting(service)
    assert note["status"] == "ready" and note["segments"] and note["summary"] == ""
    assert note["error"] == sentence
    with pytest.raises(llm.LLMError) as e:
        plus.write_refusals = [refusal]
        service.ask("Anything?", note["id"])
    assert str(e.value) == sentence


def test_the_allowance_ending_part_way_through_the_notes_keeps_the_transcript(plus, monkeypatch):
    """A long meeting's notes take several requests. The day's allowance ends
    after the first: the transcript stays, the meeting is finished, and where
    the notes would be is one calm sentence, the reason the service gave."""
    monkeypatch.setitem(meetings_ctl._engine("llm")._PART_CHARS, "plus", 60)
    plus.hear = lambda seconds, n: [{"start_s": float(i), "end_s": i + 0.5, "text": f"Point number {i} was made."}
                                    for i in range(5)]
    plus.write_refusals = [None, (402, "Today's requests to the AI are used.", "model_calls_cap")]
    ctl = meetings_ctl.MeetingsController()
    try:
        service = ctl._svc()
        note = _meeting(service)
        assert len(plus.writes) == 1 and len(note["segments"]) == 5 and note["summary"] == ""
        assert note["status"] == "ready" and note["error"] == account.MODEL_CALLS_CAP
        got = meetings_bridge.handle(ctl, "GET", f"/notes/{note['id']}", None, waffler_theme="dark")["body"]
        assert got["error"] == account.MODEL_CALLS_CAP and len(got["segments"]) == 5 and got["has_audio"]
        # Tomorrow: "Try again" writes them, and nothing is transcribed twice.
        service.enhance(note["id"], wait=True)
        assert service.store.get_note(note["id"])["summary"] and len(plus.transcriptions) == 1
    finally:
        ctl.shutdown()


# ── a failure part-way, and trying again ─────────────────────────────────────

def test_a_part_that_fails_keeps_what_went_before_and_only_the_rest_is_sent_again(plus, monkeypatch):
    monkeypatch.setattr(mt, "PLUS_PART_S", 60.0)
    plus.hear = lambda seconds, n: [{"start_s": 1.0, "end_s": 2.0, "text": f"Upload {n} speaking."}]
    plus.transcribe_refusals = [None, (503, "The transcription failed.", "transcribe_failed")]
    service = Service(store=Store())
    note = _meeting(service, _speech(150.0))
    assert note["status"] == "ready" and note["error"] == account.TRANSCRIBE_FAILED
    assert [s["text"] for s in note["segments"]] == ["Upload 1 speaking."], "the first part is not lost"
    assert plus.writes == [], "no notes from half a meeting"
    kept = len(list(paths.audio_dir(note["id"]).glob("*.flac")))
    assert 0 < len(_waiting(note["id"])) < kept, "the rest waits, with its audio"
    assert service.state()["progress"] == {}
    # "Try again": the parts that waited go, the first is not sent a second time, then the notes.
    first = plus.transcriptions[0]["seconds"]
    service.enhance(note["id"], wait=True)
    after = service.store.get_note(note["id"], with_segments=True)
    assert len(after["segments"]) == len(plus.transcriptions) >= 3 and after["error"] == "" and after["summary"]
    assert abs(sum(t["seconds"] for t in plus.transcriptions) - 150.0) < 1.0, "each part went once"
    assert plus.transcriptions[0]["seconds"] == first and _waiting(note["id"]) == []
    assert [s["start_s"] for s in after["segments"]] == sorted(s["start_s"] for s in after["segments"])
    assert len(list(paths.audio_dir(note["id"]).glob("*.flac"))) == kept, "and the audio is all still there"


def test_trying_again_while_it_still_fails_keeps_everything_and_writes_no_notes(plus, monkeypatch):
    monkeypatch.setattr(mt, "PLUS_PART_S", 60.0)
    refuse = (503, "The transcription failed.", "transcribe_failed")
    plus.transcribe_refusals = [None, refuse, refuse]
    service = Service(store=Store())
    note = _meeting(service, _speech(150.0))
    waiting = _waiting(note["id"])
    service.enhance(note["id"], wait=True)
    after = service.store.get_note(note["id"], with_segments=True)
    assert after["error"] == account.TRANSCRIBE_FAILED and after["status"] == "ready"
    assert len(after["segments"]) == 1 and after["summary"] == "" and _waiting(note["id"]) == waiting
    assert plus.writes == []


def test_a_meeting_left_waiting_when_waffler_closed_can_be_tried_again(plus):
    """Waffler quit before the service had written the meeting down: its
    chunks are still waiting on disk. The next start says so on the meeting,
    which is what puts "Try the transcription again" beside it."""
    store = Store()
    note = store.create_note(template_id="general")
    (paths.audio_dir(note["id"]) / "s01_000000000_mic.wait.flac").write_bytes(mt.to_flac(_speech()))
    store.update_note(note["id"], status="processing")
    service = Service(store=store)
    left = service.store.get_note(note["id"])
    assert left["status"] == "ready" and left["error"] == recorder.WAITING
    assert recorder.WAITING.endswith(".") and len(recorder.WAITING.split()) <= 16
    service.retranscribe(note["id"])
    assert wait_until(lambda: bool(service.store.get_note(note["id"])["summary"]), timeout=30)
    assert service.store.get_note(note["id"])["error"] == ""


# ── Plus gone: what would have been used before ──────────────────────────────

def test_with_plus_ended_a_meeting_goes_the_way_it_would_have_before(plus, monkeypatch):
    plus.end_plus(EMAIL)
    account.refresh_plan()
    calls = []
    monkeypatch.setattr(mt, "_call_openai_compatible",
                        lambda audio, key, base, model, language: calls.append(model) or
                        [{"start": 0.0, "end": 1.0, "text": "From the key."}])
    monkeypatch.setattr(llm, "_anthropic_complete", lambda system, messages, model, max_tokens: NOTES)
    service = Service(store=Store())
    note = _meeting(service)
    assert [s["text"] for s in note["segments"]] == ["From the key."] and note["summary"]
    assert plus.transcriptions == [] and plus.writes == [] and calls
    assert json.loads((plus.data / "settings.json").read_text())[account.PLUS_SETTING] is True, \
        "Plus stays the chosen way"
    # It is given again: the next meeting goes through it, with nothing re-picked.
    plus.give_plus(EMAIL)
    account.refresh_plan()
    monkeypatch.setattr(mt, "_call_openai_compatible", _never)
    monkeypatch.setattr(llm, "_anthropic_complete", _never)
    again = _meeting(service)
    assert again["segments"] and len(plus.transcriptions) == 1 and len(plus.writes) == 1


def test_plus_ending_during_a_meeting_keeps_the_audio_for_the_key(plus, monkeypatch):
    service = Service(store=Store())
    plus.end_plus(EMAIL)                               # the service knows; this computer does not yet
    note = _meeting(service)
    assert note["error"] == account.PLUS_REQUIRED and note["segments"] == [] and service.has_audio(note["id"])
    monkeypatch.setattr(mt, "_call_openai_compatible",
                        lambda *a: [{"start": 0.0, "end": 1.0, "text": "From the key."}])
    monkeypatch.setattr(llm, "_anthropic_complete", lambda system, messages, model, max_tokens: NOTES)
    service.retranscribe(note["id"])
    assert wait_until(lambda: bool(service.store.get_note(note["id"])["summary"]), timeout=30)
    assert [s["text"] for s in service.store.segments(note["id"])] == ["From the key."]
    assert _waiting(note["id"]) == []


def test_home_is_told_which_part_while_a_long_meeting_is_written_down(plus, monkeypatch):
    monkeypatch.setattr(meetings_ctl._engine("transcribe"), "PLUS_PART_S", 60.0)
    monkeypatch.setattr(meetings_ctl, "WATCH_EVERY_S", 0.01)
    changes, listed = [], []
    ctl = meetings_ctl.MeetingsController(on_change=lambda: changes.append(1))
    try:
        service = ctl._svc()

        def hear(seconds, n):
            wait_until(lambda: len(changes) > len(listed), timeout=5)   # the page is told of each part
            listed.append([m.get("progress") for m in ctl.list()])
            return [{"start_s": 1.0, "end_s": 2.0, "text": f"Part {n} speaking."}]
        plus.hear = hear
        note = service.store.create_note(template_id="general")
        service.ingest_audio(note["id"], _speech(150.0))
        ctl._watch(note["id"])
        assert wait_until(lambda: service.store.get_note(note["id"])["status"] == "ready", timeout=30)
        n = len(plus.transcriptions)
        assert listed == [[f"Part {i} of {n}"] for i in range(1, n + 1)]
        assert "progress" not in ctl.list()[0]
    finally:
        ctl.shutdown()
    card = (ROOT / "ui" / "meetings.js").read_text(encoding="utf-8")
    assert "n.progress ? 'Transcribing. ' + n.progress + '.' : 'Writing the notes. This takes a moment.'" in card
