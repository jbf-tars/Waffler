"""Whisper prompt-induced decoder derailment (v3.14.97).

Live finding, 2026-09-10, from retained audio: an 80 s dictation sent to Groq
whisper-large-v3 WITH the custom-vocabulary prompt ("Ashkan, COBie, Morta,
... Malak, XBim") came back as 159 words ending "...and the rest of the
team." -- the entire final window replaced by a list completion. The same
audio with NO prompt returned all 214 words, twice. Every list-shaped prompt
variant derailed ("Thank you for watching!", "Subtitles by the Amara.org
community", "and so on."). The phrase had appeared 27 times in one user's
history since June.

These tests pin the four layers of the fix without any network, private
audio, or keys:

  1. Groq Whisper is called without the vocab prompt (env override restores).
  2. ``_speech_seconds`` counts speech on a normally gained mic instead of
     undercounting it ~2.5x (which had blinded every downstream gate).
  3. A transcript that ends on a derailment phrase triggers the alternate
     provider retry even when its word rate looks healthy, and a modestly
     fuller safe alternate is accepted.
  4. The hallucination filter strips an own-sentence derailment tail but
     leaves the phrase alone inside a real sentence.
"""
import io
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import transcribe_whisper as tw  # noqa: E402
from transcribe_whisper import (  # noqa: E402
    WhisperTranscriber,
    _ends_on_derailment,
    _speech_seconds,
    _strip_hallucinations,
    _is_whisper_hallucination,
)

SR = 16000


def _wav(samples: np.ndarray) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.astype(np.int16).tobytes())
    return out.getvalue()


def _tone(seconds, amp, freq=180.0):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _noise(seconds, rms, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.normal(0.0, rms, int(seconds * SR))).astype(np.int16)


# ── 1. the prompt is no longer sent to Groq Whisper ─────────────────────────

class _FakeTranscriptions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return "hello"


class _FakeGroq:
    def __init__(self):
        self.audio = type("A", (), {})()
        self.audio.transcriptions = _FakeTranscriptions()


def _bare_transcriber():
    t = object.__new__(WhisperTranscriber)
    t._groq_client = _FakeGroq()
    return t


def test_groq_whisper_is_called_without_vocab_prompt(monkeypatch):
    monkeypatch.delenv("WAFFLER_WHISPER_PROMPT", raising=False)
    monkeypatch.setattr(tw, "load_vocab", lambda: ["Ashkan", "COBie", "Malak"])
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": "en"})
    t = _bare_transcriber()
    assert t._transcribe_groq(_wav(_tone(1.0, 3000))) == "hello"
    kw = t._groq_client.audio.transcriptions.kwargs
    assert kw["model"] == "whisper-large-v3"
    assert "prompt" not in kw, kw
    assert kw["language"] == "en"


def test_env_override_restores_vocab_prompt(monkeypatch):
    monkeypatch.setenv("WAFFLER_WHISPER_PROMPT", "1")
    monkeypatch.setattr(tw, "load_vocab", lambda: ["Ashkan", "COBie", "Malak"])
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": "en"})
    t = _bare_transcriber()
    t._transcribe_groq(_wav(_tone(1.0, 3000)))
    assert t._groq_client.audio.transcriptions.kwargs["prompt"] == "Ashkan, COBie, Malak"


# ── 2. the speech measure ───────────────────────────────────────────────────

def test_quiet_mic_speech_is_counted():
    """A normally gained laptop mic sits around RMS 40-60 during speech. The
    old fixed floor of 150 measured this as silence."""
    clip = _wav(np.concatenate([_tone(10.0, amp=70), np.zeros(5 * SR, dtype=np.int16)]))
    s = _speech_seconds(clip)
    assert 9.0 <= s <= 10.5, s


def test_noisy_mic_hiss_is_not_counted():
    """Constant hiss at RMS 30 with 5 s of real speech: the noise-relative
    term must keep the hiss out of the count."""
    clip = _wav(np.concatenate([_noise(10.0, rms=30), _tone(5.0, amp=3000) + _noise(5.0, rms=30, seed=1)]))
    s = _speech_seconds(clip)
    assert 4.5 <= s <= 5.5, s


def test_digital_silence_is_zero():
    assert _speech_seconds(_wav(np.zeros(8 * SR, dtype=np.int16))) == 0.0


# ── 3. the retry trigger ────────────────────────────────────────────────────

BODY = ("Okay everything does look a lot better but there are a few things I would "
        "like to change. The name of the page is not in line with the filters "
        "button and it needs to be in line otherwise it looks off. There is a lot "
        "of white space and I do not know what to do with that. The second "
        "screenshot is the filter again and the blue box goes all the way down to "
        "the bottom where it does not need to. The contrast is still off and the "
        "filters look a bit thin.")
RECOVERED_TAIL = (" They should look better, a bit bigger and just better, they are so "
                  "basic right now. We want them to look extra cool but the contrast is "
                  "off and the clear filters need looking at. Do you understand?")


def _retry_transcriber():
    t = object.__new__(WhisperTranscriber)
    t._backend = "api"
    t._cloud_order = ["groq", "openai"]
    t._groq_skip_until = 0.0
    t.client = object()
    t._groq_client = object()
    t._last_cloud_provider = "groq"
    t.last_retry_fired = False
    t.last_retry_rejected = False
    return t


@pytest.mark.parametrize("tail", [
    " and the rest of the team.",
    " Thank you for watching!",
    " And the likes.",
    " Subtitles by the Amara.org community",
])
def test_healthy_rate_but_derailed_tail_triggers_retry(monkeypatch, tail):
    t = _retry_transcriber()
    audio = _wav(_tone(40.0, 3000))  # 40 s speech; BODY is ~85 words = 2.1 w/s
    derailed = BODY + tail
    full = BODY + RECOVERED_TAIL
    calls = []

    def fake_dispatch(self, ab, exclude=None):
        calls.append(exclude)
        return full

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", fake_dispatch)
    out = t._retry_if_incomplete(audio, derailed)
    assert calls == ["groq"]
    assert out == full
    assert t.last_retry_fired is True


def test_derailed_tail_with_no_fuller_alternate_keeps_original(monkeypatch):
    t = _retry_transcriber()
    audio = _wav(_tone(40.0, 3000))
    derailed = BODY + " and the rest of the team."

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", lambda self, ab, exclude=None: derailed)
    assert t._retry_if_incomplete(audio, derailed) == derailed


def test_phrase_inside_a_real_sentence_does_not_trigger(monkeypatch):
    t = _retry_transcriber()
    audio = _wav(_tone(40.0, 3000))
    real = BODY + " Please send this over to Malak and the rest of the team."

    def boom(self, ab, exclude=None):
        raise AssertionError("must not retry a healthy transcript")

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", boom)
    assert t._retry_if_incomplete(audio, real) == real


def test_recalibrated_rate_catches_half_lost_clip(monkeypatch):
    """The 08:47 clip: 60 s of speech, 79 words (1.31 w/s). The old 1.0 floor
    let it through; it must now retry."""
    t = _retry_transcriber()
    audio = _wav(_tone(60.0, 3000))
    short = " ".join(["word"] * 79)
    full = " ".join(["word"] * 160)
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", lambda self, ab, exclude=None: full)
    assert t._retry_if_incomplete(audio, short) == full


def test_normal_slow_speech_still_exempt(monkeypatch):
    t = _retry_transcriber()
    audio = _wav(_tone(60.0, 3000))
    healthy = " ".join(["word"] * 100)  # 1.67 w/s

    def boom(self, ab, exclude=None):
        raise AssertionError("must not retry")

    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", boom)
    assert t._retry_if_incomplete(audio, healthy) == healthy


# ── 4. the filter ───────────────────────────────────────────────────────────

def test_ends_on_derailment_detection():
    assert _ends_on_derailment("look a bit sheer. and the rest of the team.")
    assert _ends_on_derailment("waiting on IT. Thank you for watching!")
    assert _ends_on_derailment("and the rest of the team.")
    assert not _ends_on_derailment("send it to Malak and the rest of the team.")
    assert not _ends_on_derailment("forward that to the rest of the team.")
    assert not _ends_on_derailment("")


def test_own_sentence_derailment_tail_is_stripped_with_real_speech():
    t = "And the actual filters look a bit sheer. and the rest of the team."
    assert _strip_hallucinations(t, speech_seconds=57.0) == "And the actual filters look a bit sheer."
    t = "So the setup is proven, but I'm still waiting on IT. Thank you for watching!"
    assert _strip_hallucinations(t, speech_seconds=60.0) == "So the setup is proven, but I'm still waiting on IT."


def test_phrase_inside_sentence_is_kept():
    for t in ("Please send it over to Malak and the rest of the team.",
              "Can you forward that to the rest of the team.",
              "Number of opportunities. and so on. I'd like more of that."):
        assert _strip_hallucinations(t, speech_seconds=8.0) == t


def test_bare_phrase_is_a_whole_output_hallucination_only_on_silence():
    assert _is_whisper_hallucination("and the rest of the team.")
    assert _strip_hallucinations("and the rest of the team.", speech_seconds=0.9) == ""
    # Six words in two seconds of speech is plausible; keep the words.
    assert _strip_hallucinations("and the rest of the team.", speech_seconds=2.0) == "and the rest of the team."
