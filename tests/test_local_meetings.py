"""Meetings with ``transcription_mode: "local"``: the same model dictation
uses writes the transcript (src/local_stt.py, replaced here), no provider is
asked, and with no key the meeting ends with its transcript and one plain line
where the notes would be. Never an error, never a spinner that does not end.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import local_stt  # noqa: E402
from meetings import llm, transcribe as mt  # noqa: E402
from meetings.service import Service  # noqa: E402
from meetings.store import Store, wait_until  # noqa: E402

RATE = 16000
KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NATTER_CUSTOM_API_KEY")


def _speech(seconds=6.0):
    """Bursts of tone with gaps: enough voice for the engine's voice check."""
    t = np.arange(int(seconds * RATE)) / RATE
    tone = 0.2 * np.sin(2 * np.pi * 220 * t)
    gate = (np.sin(2 * np.pi * 0.8 * t) > -0.3).astype(np.float32)
    return (tone * gate).astype(np.float32)


@pytest.fixture
def no_keys(monkeypatch, _isolated_data_dir):
    """No key anywhere, and never the real keychain."""
    monkeypatch.setenv("NATTER_NO_KEYRING", "1")
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("WAFFLER_MEETINGS_HOME", raising=False)
    monkeypatch.delenv("NATTER_HOME", raising=False)
    monkeypatch.setattr(mt.keys, "_memory", {})
    return _isolated_data_dir


@pytest.fixture
def local(monkeypatch, no_keys):
    asked = []
    state = {"error": None}

    def segments(audio, language=None, prompt=None, background=False):
        asked.append({"samples": len(audio), "language": language, "background": background})
        if state["error"] is not None:
            raise state["error"]
        return [{"start": 0.2, "end": 2.0, "text": " The cabinets arrive on the fourteenth.",
                 "avg_logprob": -0.1},
                {"start": 2.2, "end": 4.0, "text": " Tom books the electrician."}]
    monkeypatch.setattr(local_stt, "transcribe_segments", segments)
    (no_keys / "settings.json").write_text(json.dumps({"transcription_mode": "local"}), encoding="utf-8")
    return {"asked": asked, "state": state, "data": no_keys}


def _never(*a, **k):
    raise AssertionError("a provider was asked while the mode is local")


def _ingest(service, audio=None):
    note = service.store.create_note(template_id="general")
    service.ingest_audio(note["id"], _speech() if audio is None else audio)
    assert wait_until(lambda: service.store.get_note(note["id"])["status"] == "ready", timeout=20)
    return service.store.get_note(note["id"], with_segments=True)


# ── one implementation ───────────────────────────────────────────────────────

def test_meetings_and_dictation_share_one_module_and_so_one_model():
    import importlib
    import transcribe_whisper
    by_package = importlib.import_module("src.local_stt")
    assert mt.local_stt is local_stt is by_package is transcribe_whisper._local_stt
    assert not (ROOT / "src" / "meetings" / "local_whisper.py").exists(), \
        "the meetings engine had its own faster-whisper: there is one implementation now"
    for path in (ROOT / "src" / "meetings").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "local_whisper" not in text and "faster_whisper" not in text, path.name


# ── routing ──────────────────────────────────────────────────────────────────

def test_wafflers_setting_sends_a_meeting_to_the_local_model(local, monkeypatch):
    monkeypatch.setattr(mt, "_call_openai_compatible", _never)
    audio = _speech()
    segs = mt.transcribe(audio, {"language": "en", "transcribe_provider": "groq"})
    assert [s["text"] for s in segs] == ["The cabinets arrive on the fourteenth.",
                                         "Tom books the electrician."]
    assert segs[0]["start_s"] == 0.2 and segs[1]["end_s"] == 4.0
    asked = local["asked"]
    assert asked == [{"samples": len(audio), "language": "en", "background": True}], \
        "a meeting is background work: a dictation goes first"


def test_cloud_mode_still_goes_to_the_providers(local, monkeypatch):
    (local["data"] / "settings.json").write_text(json.dumps({"transcription_mode": "cloud"}), encoding="utf-8")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_a_real_key")
    calls = []

    def cloud(audio, api_key, base_url, model, language):
        calls.append(model)
        return [{"start": 0.0, "end": 1.0, "text": "From the cloud."}]
    monkeypatch.setattr(mt, "_call_openai_compatible", cloud)
    segs = mt.transcribe(_speech(), {"language": "en"})
    assert [s["text"] for s in segs] == ["From the cloud."] and calls == ["whisper-large-v3"]
    assert local["asked"] == []


def test_local_mode_with_keys_present_still_stays_on_this_computer(local, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_a_real_key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(mt, "_call_openai_compatible", _never)
    assert mt.transcribe(_speech(), {"language": "en"})
    assert len(local["asked"]) == 1


def test_a_chunk_with_no_voice_asks_nobody(local):
    assert mt.transcribe(np.zeros(RATE * 5, dtype=np.float32), {}) == []
    assert local["asked"] == []


@pytest.mark.parametrize("kind, sentence, expect", [
    ("not_downloaded", local_stt.NOT_DOWNLOADED, "no_local"),
    ("unavailable", local_stt.NOT_INSTALLED, "no_local"),
    ("failed", local_stt.TRANSCRIBE_FAILED, "other"),
])
def test_a_local_failure_is_a_plain_sentence_and_never_the_cloud(local, monkeypatch, kind, sentence, expect):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_a_real_key")
    monkeypatch.setattr(mt, "_call_openai_compatible", _never)
    local["state"]["error"] = local_stt.LocalSTTError(sentence, kind)
    with pytest.raises(mt.TranscribeError) as e:
        mt.transcribe(_speech(), {"language": "en"})
    assert str(e.value) == sentence and e.value.kind == expect


def test_local_is_offered_only_when_the_engine_is_in_this_build(no_keys, monkeypatch):
    monkeypatch.setattr(local_stt, "is_available", lambda: True)
    assert mt.providers_available() == ["local"]
    monkeypatch.setattr(local_stt, "is_available", lambda: False)
    assert mt.providers_available() == []


# ── a whole meeting, no key ──────────────────────────────────────────────────

def test_with_no_key_a_meeting_gets_its_transcript_and_a_plain_line_for_the_notes(local):
    service = Service(store=Store())
    assert service.store.path.parent == local["data"] / "meetings"
    note = _ingest(service)
    assert note["status"] == "ready", "never left 'processing' (the page's spinner)"
    assert [s["text"] for s in note["segments"]] == ["The cabinets arrive on the fourteenth.",
                                                     "Tom books the electrician."]
    assert note["summary"] == "" and note["error"] == llm.NOTES_NEED_KEY
    assert "key" in note["error"] and "Waffler Plus" in note["error"]
    assert "transcript is saved" in note["error"] and chr(0x2014) not in note["error"]
    assert not service._enhancing


def test_asking_for_notes_again_with_no_key_ends_at_once_with_the_same_line(local):
    service = Service(store=Store())
    note = _ingest(service)
    service.enhance(note["id"], wait=True)
    again = service.store.get_note(note["id"])
    assert again["status"] == "ready" and again["error"] == llm.NOTES_NEED_KEY


def test_a_question_with_no_key_is_answered_with_a_plain_line(local):
    service = Service(store=Store())
    note = _ingest(service)
    with pytest.raises(llm.LLMError) as e:
        service.ask("What did Tom agree to?", note["id"])
    assert str(e.value) == llm.ASK_NEEDS_KEY and e.value.kind == "no_key"
    assert "Waffler Plus" in llm.ASK_NEEDS_KEY


def test_with_a_key_the_notes_are_written_from_the_local_transcript(local, monkeypatch):
    seen = {}

    def model(system, messages):
        seen["prompt"] = messages[-1]["content"]
        return "TITLE: Kitchen refit\n\n### Decisions\n- Cabinets on the 14th"
    service = Service(store=Store(), llm_backend=model)
    note = _ingest(service)
    assert note["title"] == "Kitchen refit" and "Cabinets on the 14th" in note["summary"]
    assert note["error"] == "" and "Tom books the electrician." in seen["prompt"]


def test_a_model_that_is_not_downloaded_keeps_the_audio_and_says_so(local):
    local["state"]["error"] = local_stt.LocalSTTError(local_stt.NOT_DOWNLOADED, "not_downloaded")
    service = Service(store=Store())
    note = _ingest(service)
    assert note["status"] == "ready" and note["segments"] == []
    assert note["error"] == local_stt.NOT_DOWNLOADED, "the reason, not the line about notes"
    assert service.has_audio(note["id"]), "kept, so the transcript can be made after the download"
    # Downloaded later: the same audio becomes the transcript.
    local["state"]["error"] = None
    service.retranscribe(note["id"])
    assert wait_until(lambda: bool(service.store.segments(note["id"])), timeout=20)
    assert wait_until(lambda: service.store.get_note(note["id"])["status"] == "ready", timeout=20)
    assert service.store.get_note(note["id"])["error"] == llm.NOTES_NEED_KEY
