"""Waffler Plus failing on its own side: a meeting goes on another way.

Before: when the account service failed on its own side (on 26 Nov its
provider key was refused) a meeting's recording waited with the service's
sentence ("couldn't take this recording just now"), its notes were not
written, and its lines were not tidied, even with a key or the model on
this computer there to use. A dictation already went on another way
(tests/test_plus_service_fault.py). Now a meeting does too:

- written down: the person's key, else the model on this computer; what is
  still waiting goes a chunk at a time, as it would without Plus;
- the notes, and the lines' capitals and punctuation: the writer this
  computer has without Plus;
- with nothing else to use, the meeting keeps its audio (or its
  transcript) and says so in one line, with "Try again" beside it.

Plus then rests a few minutes and is tried again by itself. A refusal about
the plan or the allowance, signing out, or no internet keeps today's
sentence and is never sent another way (tests/test_plus_meetings.py).

The real engine against the stand-in service (tests/_account_fake.py): no
network, and every provider is a stand-in.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import local_llm  # noqa: E402
import local_stt  # noqa: E402
import meetings_bridge  # noqa: E402
import meetings_ctl  # noqa: E402
from meetings import llm, tidy_lines, transcribe as mt  # noqa: E402
from meetings.recorder import CHUNK_MAX_S  # noqa: E402
from meetings.service import Service  # noqa: E402
from meetings.store import Store, wait_until  # noqa: E402
from src import account  # noqa: E402
from test_plus_meetings import KEYS, NOTES, _meeting, _never, _speech, _waiting, plus  # noqa: E402,F401

PROVIDER_DOWN = (503, "The service's provider can't be used just now.", "provider_unavailable", {"Retry-After": "300"})
SERVICE_FAULTS = [
    PROVIDER_DOWN,
    (503, "The service has spent today's allowance.", "service_cap", {"Retry-After": "3600"}),
    (503, "Busy.", "busy", {"Retry-After": "5"}),
    (503, "The transcription failed.", "transcribe_failed", {"Retry-After": "5"}),
    (504, "Send it in shorter parts.", "transcribe_timeout"),
    (507, "The server is full.", "server_full"),
    (429, "Too many Plus requests this hour.", "plus_burst", {"Retry-After": "1200"}),
]


def _asked_plus(fake, what="transcribe") -> int:
    return len(fake.calls("POST", f"/api/plus/{what}"))


@pytest.fixture
def key(monkeypatch):
    """The person's own Groq key writes a meeting down; their Anthropic key
    writes. Returns what each was asked."""
    asked = {"heard": [], "written": []}

    def hear(audio, api_key, base, model, language):
        asked["heard"].append(len(audio) / 16000)
        return [{"start": 0.5, "end": 2.0, "text": "From my own key."}]

    def write(system, messages, model, max_tokens):
        asked["written"].append(system)
        return NOTES
    monkeypatch.setattr(mt, "_call_openai_compatible", hear)
    monkeypatch.setattr(llm, "_anthropic_complete", write)
    return asked


def _nothing_else(monkeypatch):
    """No key, no model on this computer: Plus is the only way here."""
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    for engine in (mt, meetings_ctl._engine("transcribe")):       # the engine's two copies
        monkeypatch.setattr(engine, "_local_model_ready", lambda: False)
    monkeypatch.setattr(local_llm, "should_use", lambda *a, **k: False)


# ── written down ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fault", SERVICE_FAULTS, ids=lambda f: f[2])
def test_a_fault_on_the_services_side_and_the_meeting_is_written_down_with_the_key(plus, key, fault):
    plus.transcribe_refusals = [fault]
    note = _meeting(Service(store=Store()))
    assert [s["text"] for s in note["segments"]] == ["From my own key."]
    assert note["error"] == "" and note["summary"], "and its notes are written (by Plus: only the speech failed)"
    assert _asked_plus(plus) == 1 and len(plus.writes) == 1 and key["written"] == []
    assert plus.slept == [], "the hourly cap is not waited out when there is a key"
    assert _waiting(note["id"]) == [] and Service(store=Store()).has_audio(note["id"])


def test_with_no_key_the_model_on_this_computer_writes_it_down(plus, monkeypatch):
    _nothing_else(monkeypatch)
    monkeypatch.setattr(mt, "_local_model_ready", lambda: True)
    heard = []
    monkeypatch.setattr(local_stt, "transcribe_segments",
                        lambda audio, language=None, background=False: heard.append(background) or
                        [{"start": 0.5, "end": 2.0, "text": "Heard on this computer."}])
    plus.transcribe_refusals = [PROVIDER_DOWN]
    note = _meeting(Service(store=Store()))
    assert [s["text"] for s in note["segments"]] == ["Heard on this computer."]
    assert heard == [True] and note["error"] == ""


def test_on_this_computer_chosen_and_its_model_missing_no_key_is_used(plus, key, monkeypatch):
    """The person chose on-device so the audio stays here: a key is never
    the way round a fault then, as without Plus."""
    monkeypatch.setattr(mt.local_stt, "mode_is_local", lambda stored=None: True)
    monkeypatch.setattr(mt, "_local_model_ready", lambda: False)
    plus.transcribe_refusals = [PROVIDER_DOWN]
    note = _meeting(Service(store=Store()))
    assert note["error"] == mt.PLUS_DOWN and key["heard"] == [] and _waiting(note["id"])


def test_with_nothing_else_the_meeting_keeps_its_audio_and_says_so_in_one_line(plus, monkeypatch):
    _nothing_else(monkeypatch)
    plus.transcribe_refusals = [PROVIDER_DOWN]
    ctl = meetings_ctl.MeetingsController()
    try:
        service = ctl._svc()
        note = _meeting(service)
        assert note["status"] == "ready" and note["segments"] == [] and note["summary"] == ""
        assert note["error"] == mt.PLUS_DOWN and plus.writes == []
        assert _waiting(note["id"]) and service.has_audio(note["id"]), "never lost or thrown away"
        # The page shows the line with "Try again" beside it: the line must not
        # name a key, or the page offers Settings instead.
        got = meetings_bridge.handle(ctl, "GET", f"/notes/{note['id']}", None, waffler_theme="dark")["body"]
        assert got["error"] == mt.PLUS_DOWN and got["has_audio"] and not got["segments"]
        assert not re.search("key", mt.PLUS_DOWN, re.I) and len(mt.PLUS_DOWN.split()) <= 16
        # Later, with the service back: "Try again" writes it down and the notes follow.
        r = meetings_bridge.handle(ctl, "POST", f"/notes/{note['id']}/retranscribe", {}, waffler_theme="dark")
        assert r["status"] == 200
        assert wait_until(lambda: bool(service.store.get_note(note["id"])["summary"]), timeout=30)
        after = service.store.get_note(note["id"], with_segments=True)
        assert after["segments"] and after["error"] == "" and _waiting(note["id"]) == []
    finally:
        ctl.shutdown()


def test_the_services_hourly_cap_with_nothing_else_is_waited_out_as_before(plus, monkeypatch):
    _nothing_else(monkeypatch)
    plus.transcribe_refusals = [(429, "Too many Plus requests this hour.", "plus_burst", {"Retry-After": "30"})]
    note = _meeting(Service(store=Store()))
    assert note["segments"] and note["error"] == "" and plus.slept == [30.0]


def test_a_long_meeting_part_way_goes_on_with_the_key_a_chunk_at_a_time(plus, key, monkeypatch):
    monkeypatch.setattr(mt, "PLUS_PART_S", 60.0)
    plus.hear = lambda seconds, n: [{"start_s": 1.0, "end_s": 2.0, "text": "From Plus."}]
    plus.transcribe_refusals = [None, PROVIDER_DOWN]
    service = Service(store=Store())
    said = []
    service.recorder.say_part = lambda note_id: (lambda i, n: said.append((i, n)))
    note = _meeting(service, _speech(150.0))
    assert note["error"] == "" and note["summary"]
    assert _asked_plus(plus) == 2, "the first part by Plus; after the fault Plus rests"
    first = plus.transcriptions[0]["seconds"]
    assert key["heard"] and all(s <= CHUNK_MAX_S + 0.01 for s in key["heard"]), "a chunk at a time"
    assert abs(first + sum(key["heard"]) - 150.0) < 1.0, "all of it, once"
    texts = [s["text"] for s in note["segments"]]
    assert texts[0] == "From Plus." and set(texts[1:]) == {"From my own key."}
    starts = [s["start_s"] for s in note["segments"]]
    assert starts == sorted(starts) and _waiting(note["id"]) == []
    # "Part 2 of 3" is said again as "Part 2 of 7" when the rest go in chunks.
    steps = [i for i, _ in said]
    assert steps[0] == 1 and steps == sorted(steps) and said[-1][0] == said[-1][1]


def test_after_a_fault_plus_rests_then_is_tried_again_by_itself(plus, key):
    service = Service(store=Store())
    plus.transcribe_refusals = [PROVIDER_DOWN]
    _meeting(service)
    assert _asked_plus(plus) == 1 and len(key["heard"]) == 1
    second = _meeting(service)                         # a minute later: the key at once
    assert _asked_plus(plus) == 1 and len(key["heard"]) == 2 and second["error"] == ""
    mt._rest_until.clear()                             # the rest is over
    third = _meeting(service)
    assert _asked_plus(plus) == 2 and len(key["heard"]) == 2
    assert [s["text"] for s in third["segments"]] == ["Recording 1 said this."]


def test_a_fault_while_plus_still_has_the_rest_waiting_is_caught_on_try_again(plus, key, monkeypatch):
    """A meeting that failed before there was a key (Plus was the only way)
    is tried again once a key is there: the key writes it down."""
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mt, "_local_model_ready", lambda: False)
    plus.transcribe_refusals = [PROVIDER_DOWN]
    service = Service(store=Store())
    note = _meeting(service)
    assert note["error"] == mt.PLUS_DOWN and _waiting(note["id"])
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-real-key")
    service.retranscribe(note["id"])
    assert wait_until(lambda: bool(service.store.get_note(note["id"])["summary"]), timeout=30)
    assert [s["text"] for s in service.store.segments(note["id"])] == ["From my own key."]
    assert _asked_plus(plus) == 1, "Plus rests: not asked again for this one"


# ── user faults: as before ───────────────────────────────────────────────────

@pytest.mark.parametrize("refusal, sentence", [
    ((402, "The month's minutes are used up.", "minutes_cap"), account.PLUS_MINUTES_USED),
    ((401, "Signed out."), account.SIGNED_OUT),
])
def test_the_allowance_or_signing_out_is_not_a_fault_of_the_service(plus, refusal, sentence):
    """Keys are set, and every provider raises if asked (the fixture)."""
    plus.transcribe_refusals = [refusal]
    note = _meeting(Service(store=Store()))
    assert note["error"] == sentence and note["segments"] == [] and _waiting(note["id"])
    assert not mt._rest_until, "Plus does not rest for the person's own limits"


# ── the notes and the lines ─────────────────────────────────────────────────

@pytest.mark.parametrize("fault", [PROVIDER_DOWN, (503, "The model could not be reached.", "write_failed"),
                                   (429, "Too many Plus requests this hour.", "plus_burst", {"Retry-After": "900"})],
                         ids=lambda f: f[2])
def test_notes_plus_cannot_write_are_written_with_the_key(plus, key, fault):
    plus.write_refusals = [fault]
    note = _meeting(Service(store=Store()))
    assert note["summary"] and note["title"] == "Kitchen plans" and note["error"] == ""
    assert key["written"] == [llm.notes_system("anthropic")] and _asked_plus(plus, "write") == 1
    assert plus.slept == []


def test_notes_with_no_other_writer_keep_the_transcript_and_say_so(plus, monkeypatch):
    _nothing_else(monkeypatch)
    plus.write_refusals = [PROVIDER_DOWN]
    service = Service(store=Store())
    note = _meeting(service)
    assert note["segments"] and note["summary"] == "" and note["status"] == "ready"
    assert note["error"] == llm.NOTES_PLUS_DOWN
    assert not re.search("key", llm.NOTES_PLUS_DOWN, re.I) and len(llm.NOTES_PLUS_DOWN.split()) <= 16
    # "Try again" once the service is back: written, nothing transcribed twice.
    service.enhance(note["id"], wait=True)
    after = service.store.get_note(note["id"])
    assert after["summary"] and after["error"] == "" and len(plus.transcriptions) == 1


def test_a_long_transcript_part_way_is_written_again_with_the_key_sized_for_it(plus, key, monkeypatch):
    monkeypatch.setitem(llm._PART_CHARS, "plus", 60)
    monkeypatch.setitem(llm._PART_CHARS, "anthropic", 100000)
    plus.hear = lambda seconds, n: [{"start_s": float(i), "end_s": i + 0.5, "text": f"Point number {i} was made."}
                                    for i in range(5)]
    plus.write_refusals = [None, PROVIDER_DOWN]
    note = _meeting(Service(store=Store()))
    assert note["summary"] and note["error"] == ""
    assert _asked_plus(plus, "write") == 2
    assert key["written"] == [llm.notes_system("anthropic")], "one request: it fits the key's size"


RUNNING = "this is yeah so sorry so going back to it then this is a wrap-up of everything we said"
TIDIED = "This is, yeah, so sorry. So going back to it then, this is a wrap-up of everything we said."


def test_lines_plus_cannot_tidy_are_tidied_with_the_key_after_the_notes(plus, key, monkeypatch):
    plus.hear = lambda seconds, n: [{"start_s": 0.5, "end_s": 5.0, "text": RUNNING}]
    plus.write_refusals = [PROVIDER_DOWN]              # the first request is the lines'
    asked = []

    def write(system, messages, model, max_tokens):
        asked.append(system)
        return TIDIED if system == tidy_lines.SYSTEM else NOTES
    monkeypatch.setattr(llm, "_anthropic_complete", write)
    note = _meeting(Service(store=Store()))
    assert plus.writes == [] and _asked_plus(plus, "write") == 1
    assert asked == [llm.notes_system("anthropic"), tidy_lines.SYSTEM], "the notes first, then the lines"
    assert [s["text"] for s in note["segments"]] == [TIDIED] and note["summary"] and note["error"] == ""


def test_lines_with_no_other_writer_stay_as_they_were_and_nothing_fails_by_it(plus, monkeypatch):
    _nothing_else(monkeypatch)
    plus.hear = lambda seconds, n: [{"start_s": 0.5, "end_s": 5.0, "text": RUNNING}]
    plus.write_refusals = [PROVIDER_DOWN]              # the lines' request; the notes then go through
    note = _meeting(Service(store=Store()))
    assert [s["text"] for s in note["segments"]] == [RUNNING]
    assert note["summary"] and note["error"] == "" and len(plus.writes) == 1


def test_ask_plus_cannot_answer_is_answered_with_the_key(plus, key):
    service = Service(store=Store())
    note = _meeting(service)
    plus.write_refusals = [PROVIDER_DOWN]
    out = service.ask("When do the cabinets arrive?", note["id"])
    assert out["answer"] == NOTES and key["written"] == [llm.ASK_SYSTEM]


# ── the service's answer ────────────────────────────────────────────────────

def test_the_services_hourly_cap_has_its_own_reason(plus):
    plus.transcribe_refusals = [(429, "Too many Plus requests this hour.", "plus_burst", {"Retry-After": "1200"})]
    plus.write_refusals = [(429, "Too many Plus requests this hour.", "plus_burst", {"Retry-After": "1200"})]
    flac = mt.to_flac(_speech())
    assert account.transcribe(flac) == {"ok": False, "error": account.TRANSCRIBE_TOO_OFTEN,
                                        "reason": "plus_burst", "wait_s": 1200.0}
    assert account.write("", [{"role": "user", "content": "Hi"}]) == {
        "ok": False, "error": account.WRITE_TOO_OFTEN, "reason": "plus_burst", "wait_s": 1200.0}
