"""The vocab-echo discard runs only when a prompt was sent.

Whisper given the Vocabulary as its prompt sometimes hands the list back over
silence, and transcribe_sync throws such a transcript away
(_is_vocab_echo). The test it uses is the words alone: a short transcript made
mostly of Vocabulary words. That is also what a real, short dictation about a
person's own product looks like: "Open Waffler.", "Use Claude", "Rohan".

Groq is sent no prompt (v3.14.97), so nothing it returns can be an echo of
one, and yet the discard ran on its words too: with "Waffler" and "Claude"
listed, "Open Waffler." came back as nothing, with only a print() to say so.
The discard now runs only for a recording where some request really carried
the prompt, and when it does throw a transcript away it says so in app.log
(how long, never the words).

Offline: fake providers, no keys, no microphone.
"""
import io
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import transcribe_whisper as tw  # noqa: E402
from transcribe_whisper import WhisperTranscriber  # noqa: E402

SR = 16000
VOCAB = ["Waffler", "Claude", "Rohan", "Opus", "Tailscale"]
SHORT_AND_REAL = ["Open Waffler.", "Use Claude", "Rohan", "Ask Claude Opus", "Waffler.",
                  "Tailscale"]


def _wav(seconds, amp=6000, freq=180.0):
    t = np.arange(int(seconds * SR)) / SR
    samples = (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(samples.tobytes())
    return out.getvalue()


class _Client:
    """An SDK client whose transcription answers ``text`` and keeps what it
    was asked."""

    def __init__(self, text):
        self.text, self.asked = text, []
        self.audio = self
        self.transcriptions = self

    def create(self, **kwargs):
        self.asked.append(kwargs)
        return self.text


@pytest.fixture
def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(tw, "_wlog", lines.append)
    return lines


def _transcriber(monkeypatch, text, *, order=("groq", "openai"), vocab=VOCAB):
    monkeypatch.setattr(tw, "load_vocab", lambda: list(vocab))
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": "en"})
    monkeypatch.delenv("WAFFLER_WHISPER_PROMPT", raising=False)
    t = object.__new__(WhisperTranscriber)
    t.model = "whisper-1"
    t._backend = order[0] if order[0] == "groq" else "api"
    t._cloud_order = list(order)
    t._groq_skip_until = 0.0
    t._groq_client = _Client(text)
    t.client = _Client(text)
    monkeypatch.setattr(WhisperTranscriber, "_refresh_backend", lambda self: None)
    monkeypatch.setattr(WhisperTranscriber, "_retry_if_incomplete", lambda self, ab, tr: tr)
    return t


# ── Groq: no prompt, so nothing is an echo ───────────────────────────────────

@pytest.mark.parametrize("said", SHORT_AND_REAL)
def test_a_short_dictation_of_vocabulary_words_is_kept_on_groq(monkeypatch, logged, said):
    assert tw._is_vocab_echo(said, VOCAB), "the words alone still look like an echo"
    t = _transcriber(monkeypatch, said)
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: self._transcribe_groq(ab))
    assert t.transcribe_sync(_wav(1.5)) == said
    assert t.last_asr_filtered is False
    assert all("prompt" not in asked for asked in t._groq_client.asked)
    assert not any("echo" in line for line in logged)


def test_even_the_whole_list_said_aloud_is_kept_when_no_prompt_went(monkeypatch, logged):
    said = ", ".join(VOCAB)
    t = _transcriber(monkeypatch, said)
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: self._transcribe_groq(ab))
    assert t.transcribe_sync(_wav(4.0)) == said


def test_no_vocabulary_means_no_prompt_and_no_discard(monkeypatch, logged):
    t = _transcriber(monkeypatch, "Open Waffler.", order=("openai", "groq"), vocab=[])
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: self._transcribe_api(ab))
    assert t.transcribe_sync(_wav(1.5)) == "Open Waffler."
    assert "prompt" not in t.client.asked[0]


# ── a prompt was sent: the discard still does its job ────────────────────────

def test_an_echo_of_the_prompt_is_still_discarded_on_openai(monkeypatch, logged):
    echo = ", ".join(VOCAB)
    t = _transcriber(monkeypatch, echo, order=("openai", "groq"))
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: self._transcribe_api(ab))
    assert t.transcribe_sync(_wav(1.5)) == ""
    assert t.client.asked[0]["prompt"] == echo
    assert t.last_asr_response == echo and t.last_asr_filtered is True
    # Said in app.log, as a length and never the words.
    line = next(l for l in logged if "vocab-echo" in l)
    assert str(len(echo)) in line and not any(w in line for w in VOCAB)


def test_and_on_the_local_model(monkeypatch, logged):
    echo = ", ".join(VOCAB)
    t = _transcriber(monkeypatch, echo)
    asked = []

    class _Local:
        @staticmethod
        def transcribe(audio, language=None, prompt=None, **_kw):
            asked.append(prompt)
            return echo

    monkeypatch.setattr(tw, "_local_stt", _Local, raising=False)
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: self._transcribe_local(ab))
    assert t.transcribe_sync(_wav(1.5)) == ""
    assert asked == [echo] and t.last_asr_filtered is True


def test_and_on_groq_when_the_owners_switch_sends_the_prompt(monkeypatch, logged):
    echo = ", ".join(VOCAB)
    t = _transcriber(monkeypatch, echo)
    monkeypatch.setenv("WAFFLER_WHISPER_PROMPT", "1")
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: self._transcribe_groq(ab))
    assert t.transcribe_sync(_wav(1.5)) == ""
    assert t._groq_client.asked[0]["prompt"] == echo


def test_a_fallback_that_carried_the_prompt_counts(monkeypatch, logged):
    """Groq could not be reached and OpenAI took the recording, with the
    prompt: what comes back may be an echo."""
    echo = ", ".join(VOCAB)
    t = _transcriber(monkeypatch, echo)

    def groq_down_then_openai(self, ab, exclude=None):
        return self._transcribe_api(ab)

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", groq_down_then_openai)
    assert t.transcribe_sync(_wav(1.5)) == ""


# ── per recording ────────────────────────────────────────────────────────────

def test_the_prompt_of_an_earlier_recording_does_not_carry_over(monkeypatch, logged):
    echo = ", ".join(VOCAB)
    t = _transcriber(monkeypatch, echo, order=("openai", "groq"))
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: self._transcribe_api(ab))
    assert t.transcribe_sync(_wav(1.5)) == ""                # a prompt went: discarded
    t._groq_client.text = "Open Waffler."
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: self._transcribe_groq(ab))
    assert t.transcribe_sync(_wav(1.5)) == "Open Waffler."   # none went this time: kept


def test_ordinary_speech_is_untouched_either_way(monkeypatch, logged):
    said = "Can we move the Waffler review to Thursday at ten?"
    for order, path in ((("groq", "openai"), "_transcribe_groq"), (("openai", "groq"), "_transcribe_api")):
        t = _transcriber(monkeypatch, said, order=order)
        monkeypatch.setattr(WhisperTranscriber, "_dispatch_one",
                            lambda self, ab, exclude=None, _p=path: getattr(self, _p)(ab))
        assert t.transcribe_sync(_wav(4.0)) == said and t.last_asr_filtered is False
