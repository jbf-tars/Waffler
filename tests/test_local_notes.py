"""Meeting notes and Ask written by the model on this computer
(src/local_llm.py through src/meetings/llm.py): used when the model is here
and there is no key, or the person chose it; notes give way to a dictation;
a failure is a plain sentence; no provider is ever asked in its place.

The model is replaced by a function. The real one is scripts/local_llm_check.py.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import local_llm  # noqa: E402
import local_stt  # noqa: E402
from meetings import llm, transcribe as mt  # noqa: E402
from meetings.service import Service  # noqa: E402
from meetings.store import Store, wait_until  # noqa: E402

KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NATTER_CUSTOM_API_KEY")
NOTES = "TITLE: Kitchen fitting plan\n\n### Cabinets\n- Arrive on the 14th.\n\n### Next steps\n- Tom books the electrician."


class FakeLocal:
    def __init__(self):
        self.calls, self.replies, self.error = [], [NOTES], None

    def __call__(self, system, messages, **kw):
        self.calls.append({"system": system, "messages": messages, **kw})
        if self.error:
            raise self.error
        text = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if kw.get("on_text"):
            kw["on_text"](text)
        return {"text": text, "input_tokens": 100, "output_tokens": 50, "finish_reason": "stop"}


@pytest.fixture
def no_keys(monkeypatch, _isolated_data_dir):
    monkeypatch.setenv("NATTER_NO_KEYRING", "1")
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("WAFFLER_MEETINGS_HOME", raising=False)
    monkeypatch.delenv("NATTER_HOME", raising=False)
    monkeypatch.setattr(mt.keys, "_memory", {})
    return _isolated_data_dir


@pytest.fixture
def here(monkeypatch, no_keys):
    """The writing model is downloaded and this build has its engine."""
    monkeypatch.setattr(local_llm, "is_available", lambda: True)
    monkeypatch.setattr(local_llm, "is_ready", lambda: True)
    fake = FakeLocal()
    monkeypatch.setattr(local_llm, "chat", fake)
    return fake


def _never(*a, **k):
    raise AssertionError("a provider was asked")


@pytest.fixture
def no_provider(monkeypatch):
    for name in ("_anthropic_complete", "_openai_complete", "ollama_complete", "list_models"):
        monkeypatch.setattr(llm, name, _never)


# ── who writes ───────────────────────────────────────────────────────────────

def test_with_no_key_and_no_model_it_is_the_plain_line_as_before(no_keys, monkeypatch):
    monkeypatch.setattr(local_llm, "is_ready", lambda: False)
    assert llm.can_write({}) is False
    with pytest.raises(llm.LLMError) as e:
        llm.resolve_provider({})
    assert str(e.value) == llm.NOTES_NEED_KEY and e.value.kind == "no_key"


def test_with_no_key_the_model_here_writes(here):
    assert llm.resolve_provider({}) == "local" and llm.can_write({}) is True
    assert llm.resolve_provider({"llm_provider": "auto"}) == "local"
    # A provider chosen long ago whose key has gone: this computer, not an error.
    assert llm.resolve_provider({"llm_provider": "anthropic"}) == "local"


def test_with_a_key_nothing_changes_unless_the_person_chose_this_computer(here, monkeypatch, no_keys):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    assert llm.resolve_provider({}) == "groq"
    (no_keys / "settings.json").write_text(json.dumps({"writing_mode": "local"}), encoding="utf-8")
    assert llm.resolve_provider({}) == "local"
    assert llm.resolve_provider({"llm_provider": "groq"}) == "local", "the choice in Settings wins"
    (no_keys / "settings.json").write_text(json.dumps({"writing_mode": "cloud"}), encoding="utf-8")
    assert llm.resolve_provider({}) == "groq"


def test_someones_own_local_endpoint_is_still_theirs(here):
    settings = {"llm_provider": "custom", "custom_base_url": "http://127.0.0.1:11434/v1"}
    assert llm.resolve_provider(settings) == "custom"


def test_without_the_engine_in_this_build_it_is_never_chosen(here, monkeypatch):
    monkeypatch.setattr(local_llm, "is_available", lambda: False)
    assert llm.can_write({}) is False


# ── notes ────────────────────────────────────────────────────────────────────

def test_notes_are_written_here_in_the_background_with_the_apps_prompt(here, no_provider):
    title, notes = llm.write_notes("Me: The cabinets arrive on the fourteenth.", "", "Write clear notes.", {},
                                   when="Thursday 21 November 2024, 10:00")
    assert title == "Kitchen fitting plan" and notes.startswith("### Cabinets")
    (call,) = here.calls
    assert call["background"] is True, "a dictation goes ahead of notes"
    assert call["temperature"] == 0.0
    assert call["max_tokens"] <= 3000
    system = call["system"]
    # The app's own prompt, whole, with two rules more for a small model.
    assert llm.NOTES_SYSTEM.split("\n\nOutput format")[0] in system
    assert system.rstrip().endswith(llm.NOTES_SYSTEM.split("\n\nOutput format")[1].rstrip())
    assert llm.LOCAL_NOTES_RULES in system
    assert system.index(llm.LOCAL_NOTES_RULES) < system.index("Output format"), "among the rules, before the format"
    user = call["messages"][-1]["content"]
    assert "Recorded: Thursday 21 November 2024, 10:00" in user and "<transcript>" in user


def test_a_provider_is_sent_the_prompt_it_always_was(no_keys):
    seen = []
    llm.write_notes("Me: hello there everyone.", "", "Write clear notes.", {},
                    backend=lambda system, messages: seen.append(system) or NOTES)
    assert seen == [llm.NOTES_SYSTEM]
    assert "Never add a month" in llm.LOCAL_NOTES_RULES and "Never add a month" not in llm.NOTES_SYSTEM


def test_notes_that_came_without_a_title_are_given_one(here, no_provider):
    here.replies = ["### Cabinets\n- Arrive on the 14th.", "Kitchen fitting plan\n"]
    title, notes = llm.write_notes("Me: The cabinets arrive on the fourteenth.", "", "Write clear notes.", {})
    assert title == "Kitchen fitting plan" and notes == "### Cabinets\n- Arrive on the 14th."
    assert len(here.calls) == 2
    second = here.calls[1]
    assert second["background"] is True and second["max_tokens"] <= 40
    assert "### Cabinets" in second["messages"][-1]["content"]


def test_a_title_that_cannot_be_had_is_left_empty_and_the_notes_are_kept(here, no_provider):
    here.replies = ["### Cabinets\n- Arrive on the 14th.", "Here is a very long reply that is not a title at all, "
                    "because it runs on and on and on."]
    title, notes = llm.write_notes("Me: The cabinets arrive.", "", "Write clear notes.", {})
    assert title == "" and notes.startswith("### Cabinets")


def test_a_long_meeting_goes_through_in_parts_that_fit_the_model(here, no_provider):
    assert llm._PART_CHARS["local"] == 36000
    line = "Them: " + "word " * 60
    transcript = "\n".join(line for _ in range(200))      # about 61,000 characters
    here.replies = ["- digest one", "- digest two", NOTES]
    title, _notes = llm.write_notes(transcript, "", "Write clear notes.", {})
    assert title == "Kitchen fitting plan" and len(here.calls) == 3
    assert all(c["background"] is True for c in here.calls)
    assert "Detailed digest" in here.calls[-1]["messages"][-1]["content"]


@pytest.mark.parametrize("kind, message", [
    ("failed", local_llm.FAILED), ("too_long", local_llm.TOO_LONG), ("not_downloaded", local_llm.NOT_DOWNLOADED),
])
def test_a_failure_here_is_a_plain_sentence_and_never_a_provider(here, no_provider, kind, message):
    here.error = local_llm.LocalLLMError(message, kind)
    with pytest.raises(llm.LLMError) as e:
        llm.write_notes("Me: hello there.", "", "Write clear notes.", {})
    assert str(e.value) == message and e.value.kind == "local"
    assert len(here.calls) == 1, "asked once: no retries, nobody else"


# ── Ask ──────────────────────────────────────────────────────────────────────

def test_a_question_is_answered_here_as_it_is_written_and_ahead_of_notes(here, no_provider):
    here.replies = ["The cabinets arrive on the 14th."]
    seen = []
    answer = llm.ask("When do the cabinets arrive?", "## Note: Kitchen\nThey arrive on the 14th.", [], {},
                     on_text=seen.append)
    assert answer == "The cabinets arrive on the 14th." and seen == [answer]
    (call,) = here.calls
    assert call["background"] is False, "someone is waiting for it"
    assert call["system"] == llm.ASK_SYSTEM


# ── a whole meeting, no key ──────────────────────────────────────────────────

def test_a_meeting_with_no_key_gets_its_notes_from_the_model_here(here, no_provider, monkeypatch, no_keys, tmp_path):
    monkeypatch.setattr(local_stt, "transcribe_segments", lambda audio, language=None, prompt=None, background=False: [
        {"start": 0.2, "end": 2.0, "text": " The cabinets arrive on the fourteenth.", "avg_logprob": -0.1},
        {"start": 2.2, "end": 4.0, "text": " Tom books the electrician."}])
    (no_keys / "settings.json").write_text(json.dumps({"transcription_mode": "local"}), encoding="utf-8")
    service = Service(store=Store())
    t = np.arange(int(6.0 * 16000)) / 16000
    audio = (0.2 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 0.8 * t) > -0.3)).astype(np.float32)
    note = service.store.create_note(template_id="general")
    service.ingest_audio(note["id"], audio)
    assert wait_until(lambda: service.store.get_note(note["id"])["status"] == "ready", timeout=20)
    note = service.store.get_note(note["id"])
    assert note["error"] == "" and note["title"] == "Kitchen fitting plan"
    assert "Tom books the electrician" in note["summary"]
    assert "cabinets arrive on the fourteenth" in here.calls[0]["messages"][-1]["content"]
    # And a question about it.
    here.replies = ["On the 14th."]
    here.calls.clear()
    assert service.ask("When do the cabinets arrive?", note["id"])["answer"] == "On the 14th."
