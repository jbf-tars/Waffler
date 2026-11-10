"""A take that speech to text returned with no punctuation is punctuated.

Whisper sometimes writes a whole take in lower case with no punctuation (in
a review of real use: about one take in four of twelve words or more). The
clean-up removed the fillers and pasted the rest as one unbroken line,
because the prompt also says to keep a lower-case register. For those takes,
and only those, a note after the spelling rule says the missing punctuation is
a transcription artefact. Every invented example below was written for this
file. The live measurement is scripts/cleanup_regression.py (UNPUNCT cases).

Offline: the provider is a stub.
"""
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import style_openai as so  # noqa: E402
from style_openai import OpenAIStyler  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

LONG_RUN_ON = (
    "we should hold the quiz in the village hall again because everybody knows where it is but "
    "the sound system in there is terrible last year nobody at the back could hear the questions "
    "and the microphone kept cutting out and then the projector would not talk to the laptop")
SHORT_RUN_ON = "what time does the hardware shop close on saturdays because i need the paint"
NO_FULL_STOP = "Can you ask the courier to collect the parcel and bring it back on Monday"
CAPITAL_BUT_LONG = (
    "We should hold the quiz in the village hall again because everybody knows where it is but "
    "the sound system in there is terrible last year nobody at the back could hear the questions "
    "and the microphone kept cutting out")


@pytest.mark.parametrize("text", [LONG_RUN_ON, SHORT_RUN_ON, NO_FULL_STOP, CAPITAL_BUT_LONG])
def test_a_take_without_its_punctuation_is_recognised(text):
    assert so._looks_unpunctuated(text)


@pytest.mark.parametrize("text", [
    # One properly written sentence with no comma in it.
    "Can you check whether the boiler service is booked for next week because I have not had "
    "a confirmation email yet and it is getting cold.",
    # Ordinary punctuated speech.
    "So I had a look at the plans last night, and I think the patio is in the wrong place. "
    "The sun comes round at about four, so the seating would be in shade.",
    # A lower-case register the speaker punctuated: kept as it is (prompt rule 11).
    "yo can you grab me a coffee when you go on lunch break, the usual please, ta",
    # Too short to say anything about.
    "sounds good see you there",
    "and yes move it to monday please thanks",
    "",
])
def test_a_punctuated_or_short_take_is_not(text):
    assert not so._looks_unpunctuated(text)


def test_none_is_not_a_take():
    assert not so._looks_unpunctuated(None)


# ── what the model is asked ──────────────────────────────────────────────────

def _answer(text):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text),
                                       finish_reason="stop")],
        usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=3))


class Client:
    def __init__(self, text):
        self.text, self.calls = text, []
        self.chat = self.completions = self

    def create(self, **kw):
        self.calls.append(kw)
        return _answer(self.text)


def _styler(client):
    s = object.__new__(OpenAIStyler)
    s._provider_order = ["groq"]
    s._use_groq, s._use_cerebras = True, False
    s._groq_client, s._cerebras_client, s.client = client, None, None
    s.model = "gpt-6-luna"
    s._groq_models, s._groq_model = ("openai/gpt-oss-120b",), "openai/gpt-oss-120b"
    s._groq_skip_until = s._cerebras_skip_until = 0.0
    s.prompt_template = (ROOT / "prompts" / "normal.txt").read_text(encoding="utf-8")
    return s


def _prompt_for(text, monkeypatch, language="en"):
    monkeypatch.setattr(so, "_dictation_language", lambda: language)
    client = Client(text)
    _styler(client).style(text)
    [sent] = client.calls
    return sent["messages"][1]["content"]


def test_an_unpunctuated_take_is_sent_with_the_note(monkeypatch):
    prompt = _prompt_for(LONG_RUN_ON, monkeypatch)
    assert so._UNPUNCTUATED_NOTE in prompt
    # After the spelling rule and straight before the transcript: everything
    # above it is the prompt every other take sends, so the provider's cache
    # of that prompt serves both.
    template = (ROOT / "prompts" / "normal.txt").read_text(encoding="utf-8")
    shared = template.split("{dialect_instruction}")[0]
    assert prompt.startswith(shared)
    assert prompt.rstrip("\n").endswith(f"{so._UNPUNCTUATED_NOTE}\n\nTranscript: {LONG_RUN_ON}")


def test_a_punctuated_take_is_sent_exactly_what_it_always_was(monkeypatch):
    text = ("So I had a look at the plans last night, and I think the patio is in the wrong "
            "place. The sun comes round at about four, so the seating would be in shade.")
    prompt = _prompt_for(text, monkeypatch)
    assert "PUNCTUATION NOTE" not in prompt
    template = (ROOT / "prompts" / "normal.txt").read_text(encoding="utf-8")
    assert prompt == template.format(transcript=text,
                                     dialect_instruction=so._dialect_instruction({}))


def test_the_note_is_for_english_takes(monkeypatch):
    # The evidence is English; another language keeps its own rule alone.
    assert "PUNCTUATION NOTE" not in _prompt_for(LONG_RUN_ON, monkeypatch, language="de")


def test_the_note_asks_for_punctuation_and_nothing_else():
    note = so._UNPUNCTUATED_NOTE
    assert "full stops, commas and question marks" in note
    assert "keep every word that is not filler, in the order spoken" in note
    assert "{" not in note and "}" not in note      # it goes through str.format's slot
    assert "—" not in note and "–" not in note
