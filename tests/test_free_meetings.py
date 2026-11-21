"""Free to start, in the Meetings tab: with the speech model downloaded and no
key, a meeting can be recorded.

The Meetings page (ui/meetings-app, built from natter-notes ui/) only knew
about keys, so with none it offered "Add a key" and nothing else. Now the
engine tells it what this computer can do by itself: the page's first answer
(`GET /app` through src/meetings_bridge.py) carries
`local = {"speech": bool, "writing": bool}`, each true only when that model is
downloaded and is the one in use here. Three states follow:

  1. a speech key, or speech on this computer: "New recording" as normal;
  2. recorded, and nothing to write the notes with: the meeting is finished,
     the transcript is there, and where the notes would be is one quiet line
     and one link. Not an error. A key or the writing model added later, and
     the notes are written from that meeting;
  3. neither a speech key nor the model: "Download the free model", which
     opens Settings at Keys and providers, and a quiet "Use your own key".

The models are replaced by functions; nothing is downloaded and nobody is asked.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import meetings_bridge, meetings_ctl  # noqa: E402
from src.meetings import llm, transcribe as mt  # noqa: E402
from src.meetings.store import wait_until  # noqa: E402

local_stt, local_llm = mt.local_stt, llm.local_llm
RATE = 16000
KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NATTER_CUSTOM_API_KEY")
NOTES = "TITLE: Kitchen fitting plan\n\n### Next steps\n- Tom books the electrician."


def _speech(seconds=6.0):
    t = np.arange(int(seconds * RATE)) / RATE
    tone = 0.2 * np.sin(2 * np.pi * 220 * t)
    gate = (np.sin(2 * np.pi * 0.8 * t) > -0.3).astype(np.float32)
    return (tone * gate).astype(np.float32)


class Machine:
    """This computer, for a test: which models are downloaded, which way is
    chosen, which keys are set."""

    def __init__(self, monkeypatch, data):
        self.mp, self.data = monkeypatch, data
        self.speech_model = self.writing_model = False
        self.wrote = []
        monkeypatch.setattr(local_stt, "is_available", lambda: True)
        monkeypatch.setattr(local_stt, "_is_ready", lambda name: self.speech_model)
        monkeypatch.setattr(local_stt, "transcribe_segments", self._hear)
        monkeypatch.setattr(local_llm, "is_available", lambda: True)
        monkeypatch.setattr(local_llm, "is_ready", lambda: self.writing_model)
        monkeypatch.setattr(local_llm, "chat", self._write)

    def _hear(self, audio, language=None, prompt=None, background=False):
        if not self.speech_model:
            raise local_stt.LocalSTTError(local_stt.NOT_DOWNLOADED, "not_downloaded")
        return [{"start": 0.2, "end": 2.0, "text": " The cabinets arrive on the fourteenth.",
                 "avg_logprob": -0.1},
                {"start": 2.2, "end": 4.0, "text": " Tom books the electrician."}]

    def _write(self, system, messages, **kw):
        self.wrote.append(messages)
        return {"text": NOTES, "input_tokens": 100, "output_tokens": 50, "finish_reason": "stop"}

    def choose(self, **settings):
        (self.data / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def key(self, name):
        self.mp.setenv(name, "test-not-a-real-key")


@pytest.fixture
def machine(monkeypatch, _isolated_data_dir, tmp_path):
    monkeypatch.setenv("NATTER_NO_KEYRING", "1")
    monkeypatch.setenv("WAFFLER_MEETINGS_HOME", str(tmp_path / "meetings"))
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mt.keys, "_memory", {})
    monkeypatch.setattr(meetings_ctl, "_share_waffler_keys", lambda: None)
    for name in ("_anthropic_complete", "_openai_complete", "ollama_complete", "list_models"):
        monkeypatch.setattr(llm, name, _never)
    monkeypatch.setattr(mt, "_call_openai_compatible", _never)
    return Machine(monkeypatch, _isolated_data_dir)


@pytest.fixture
def ctl(machine):
    c = meetings_ctl.MeetingsController()
    yield c
    c.shutdown()


def _never(*a, **k):
    raise AssertionError("a provider was asked")


def call(ctl, method, path, body=None):
    return meetings_bridge.handle(ctl, method, path, body, waffler_theme="dark")


def _local(ctl):
    r = call(ctl, "GET", "/app")
    assert r["status"] == 200
    return r["body"]["local"]


def _record(ctl):
    """A meeting's audio through the engine, as a recording leaves it."""
    svc = ctl._svc()
    note = svc.store.create_note(template_id="general")
    svc.ingest_audio(note["id"], _speech())
    assert wait_until(lambda: svc.store.get_note(note["id"])["status"] == "ready", timeout=20)
    return note["id"]


# ── what the page is told ────────────────────────────────────────────────────

def test_the_page_is_told_what_this_computer_can_do_by_itself(machine, ctl):
    assert _local(ctl) == {"speech": False, "writing": False}, "nothing downloaded"
    machine.speech_model = True
    assert _local(ctl)["speech"] is False, "downloaded, but speech is set to go to a provider"
    machine.choose(transcription_mode="local")
    assert _local(ctl) == {"speech": True, "writing": False}
    machine.writing_model = True
    assert _local(ctl) == {"speech": True, "writing": True}, "no key at all: the model here writes"
    machine.speech_model = False
    assert _local(ctl)["speech"] is False, "chosen, but not downloaded"


def test_speech_here_needs_the_engine_in_this_build(machine, ctl, monkeypatch):
    machine.speech_model = True
    machine.choose(transcription_mode="local")
    monkeypatch.setattr(local_stt, "is_available", lambda: False)
    assert _local(ctl)["speech"] is False


def test_the_writing_model_counts_only_when_it_is_the_one_that_would_write(machine, ctl):
    machine.writing_model = True
    machine.key("GROQ_API_KEY")
    assert _local(ctl)["writing"] is False, "with a key and no choice made, the key writes"
    machine.choose(writing_mode="local")
    assert _local(ctl)["writing"] is True, "chosen over the key in Settings"


def test_local_is_two_plain_booleans_and_never_a_path_or_a_key(machine, ctl):
    machine.speech_model = machine.writing_model = True
    machine.choose(transcription_mode="local")
    local = _local(ctl)
    assert sorted(local) == ["speech", "writing"] and all(type(v) is bool for v in local.values())


def test_a_fault_in_either_model_s_module_is_a_no_not_a_broken_page(machine, ctl, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk gone")
    machine.choose(transcription_mode="local")
    monkeypatch.setattr(local_stt, "_is_ready", boom)
    monkeypatch.setattr(local_llm, "is_ready", boom)
    assert _local(ctl) == {"speech": False, "writing": False}


# ── state 1 and 2: recorded with the free model, nothing to write notes ──────

def test_a_meeting_with_speech_here_and_no_way_to_write_notes_ends_calmly(machine, ctl):
    machine.speech_model = True
    machine.choose(transcription_mode="local")
    assert _local(ctl) == {"speech": True, "writing": False}                  # state 1: it can record
    assert not any(k["set"] for k in call(ctl, "GET", "/app")["body"]["keys"].values())
    note_id = _record(ctl)
    note = call(ctl, "GET", f"/notes/{note_id}")["body"]
    assert note["status"] == "ready", "finished: never left 'processing' (the page's spinner)"
    assert [s["text"] for s in note["segments"]] == ["The cabinets arrive on the fourteenth.",
                                                     "Tom books the electrician."]
    # State 2. Nothing for the page to show as a problem: it says its own one
    # quiet line, from `local` and the keys. The engine's sentence stays in
    # the store for everything else that reads it (Home, the account).
    assert note["summary"] == "" and note["error"] == ""
    assert ctl._svc().store.get_note(note_id)["error"] == llm.NOTES_NEED_KEY
    assert machine.wrote == []


def test_a_real_problem_is_still_told_to_the_page(machine, ctl):
    machine.choose(transcription_mode="local")                 # chosen, not downloaded
    note_id = _record(ctl)
    note = call(ctl, "GET", f"/notes/{note_id}")["body"]
    assert note["segments"] == [] and note["error"] == local_stt.NOT_DOWNLOADED


def test_the_writing_model_added_later_writes_that_meeting_s_notes(machine, ctl):
    machine.speech_model = True
    machine.choose(transcription_mode="local")
    note_id = _record(ctl)
    r = call(ctl, "POST", f"/notes/{note_id}/enhance", {})
    assert r["status"] == 400 and r["body"]["detail"] == llm.NOTES_NEED_KEY, "still nothing to write with"
    machine.writing_model = True                               # downloaded in Settings since
    assert _local(ctl) == {"speech": True, "writing": True}
    assert call(ctl, "POST", f"/notes/{note_id}/enhance", {})["status"] == 200
    store = ctl._svc().store
    assert wait_until(lambda: store.get_note(note_id)["status"] == "ready", timeout=20)
    note = call(ctl, "GET", f"/notes/{note_id}")["body"]
    assert "Tom books the electrician." in note["summary"] and note["title"] == "Kitchen fitting plan"
    assert note["error"] == "" and len(machine.wrote) == 1
    assert "Tom books the electrician." in machine.wrote[0][-1]["content"]


def test_a_key_added_later_is_enough_to_ask_for_the_notes(machine, ctl):
    machine.speech_model = True
    machine.choose(transcription_mode="local")
    note_id = _record(ctl)
    machine.key("ANTHROPIC_API_KEY")
    assert call(ctl, "GET", "/app")["body"]["keys"]["anthropic"]["set"] is True
    assert llm.resolve_provider(ctl._svc().store.settings()) == "anthropic"
    assert call(ctl, "GET", f"/notes/{note_id}")["body"]["error"] == ""


# ── state 3: neither a speech key nor the model ──────────────────────────────

def test_with_neither_a_key_nor_the_model_the_page_is_told_so(machine, ctl):
    info = call(ctl, "GET", "/app")["body"]
    assert info["local"] == {"speech": False, "writing": False}
    assert not any(k["set"] for k in info["keys"].values())
    machine.key("GROQ_API_KEY")                                # their own key instead
    info = call(ctl, "GET", "/app")["body"]
    assert info["keys"]["groq"]["set"] is True and info["local"]["speech"] is False
