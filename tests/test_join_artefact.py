"""3.16.3: two more ways an invented "Thank you." got through, closed with audio evidence.

1. The join artefact. Whisper writes each segment on its own and the joins are
   plain spaces, so a sentence the speaker left unfinished followed by an
   invented segment reads "...maybe like Thank you." or "...expand on
   betfred Thank you.": a capital after a lower-case word, with no full stop
   between. The old filter only looked for a thanks as its own sentence after
   a full stop, so both were pasted. In the owner's 1,225 dictations that
   shape ended four of them (these two and two "Thank you for watching!"),
   all invented; every thanks they really said had a full stop or a comma
   before it. It is now dropped, but only with audio evidence that the end
   of the take held no speech (``trailing_silence_s``, from the cut), and
   never when the word before it dangles or Groq's segments showed a voice
   under it.

2. The lone "Thank you." on a take of room noise. It was kept whenever the
   take measured 1.5 s of speech, and a lifted noise floor fakes that: the
   27 October take was 2.1 s long and measured 2.0 s. The decision to keep a
   take that is nothing but a stock phrase now also asks the strict measure
   (_voiced_seconds), which gives room noise none.

Pure tests with synthetic audio and fake providers: no network, no keys.
"""
import io
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import transcribe_whisper as tw  # noqa: E402
from transcribe_whisper import WhisperTranscriber, _strip_hallucinations  # noqa: E402

SR = 16000

UNFINISHED = ("So I was thinking we could probably move the launch to next week, "
              "maybe not maybe like Thank you.")
BETFRED = "I want you to look at the odds feed and expand on betfred Thank you."


def strip(text, trailing=1.2, speech=30.0, voiced=None):
    return _strip_hallucinations(text, speech_seconds=speech, voiced_seconds=voiced,
                                 trailing_silence_s=trailing)


# ── 1. the join artefact ────────────────────────────────────────────────────

def test_the_owners_two_invented_endings_go_when_the_end_was_silent():
    assert strip(UNFINISHED).endswith("maybe not maybe like")
    assert strip(BETFRED) == "I want you to look at the odds feed and expand on betfred"


@pytest.mark.parametrize("trailing", [None, 0.0, 0.3, 0.6, 0.99])
def test_without_evidence_of_a_silent_end_they_stay(trailing):
    assert strip(UNFINISHED, trailing=trailing) == UNFINISHED
    assert strip(BETFRED, trailing=trailing) == BETFRED


def test_the_evidence_threshold_is_the_cut_minimum():
    assert strip(BETFRED, trailing=tw._TAIL_MIN_S) != BETFRED


@pytest.mark.parametrize("ending,stripped", [
    ("expand on betfred Thanks.", "expand on betfred"),
    ("expand on betfred Thank you!", "expand on betfred"),
    ("expand on betfred Thank you very much.", "expand on betfred"),
    ("expand on betfred Thank you so much.", "expand on betfred"),
    ("expand on betfred Thank you from the heart.", "expand on betfred"),
    ("expand on betfred Thank you for watching!", "expand on betfred"),
    ("expand on betfred, Thank you.", "expand on betfred"),
])
def test_the_join_artefact_in_its_forms(ending, stripped):
    assert strip("I want you to look at the odds feed and " + ending) == (
        "I want you to look at the odds feed and " + stripped)


@pytest.mark.parametrize("text", [
    # A dangling word proves the thanks was part of the sentence.
    "Please send the deck to Priya and Thank you.",
    "Can you pass this on to Thank you.",
    # A lower-case thanks ran on from the words before it in one segment.
    "I want you to look at the odds feed and expand on betfred thank you.",
    "I want you to look at the odds feed and expand on betfred, thank you.",
    # A capital word before it: not the shape of a join.
    "I want you to look at the odds feed for the API Thank you.",
    # A name, or more words, after the thanks: the speaker's own.
    "I want you to look at the odds feed and expand on betfred Thank you, James.",
    "I want you to look at the odds feed and expand on betfred Thank you for the help with it.",
    # A thanks that is not at the end.
    "Thank you for the notes, I will look at them tomorrow.",
])
def test_real_speech_is_never_touched(text):
    assert strip(text, trailing=3.0) == text


def test_the_pinned_legitimate_endings_survive_the_new_rule_too():
    import test_transcript_filter_fidelity as pinned
    for _label, text in pinned.LEGITIMATE:
        assert strip(text, trailing=3.0) == text


def test_never_blank_and_idempotent():
    once = strip(BETFRED)
    assert strip(once) == once
    assert strip("Thank you.", trailing=3.0, speech=4.0) == "Thank you."     # nothing before it


# ── 2. a lone stock phrase on a take of room noise ──────────────────────────

def test_a_lone_thank_you_with_no_voice_in_the_take_goes():
    assert _strip_hallucinations("Thank you.", speech_seconds=2.0, voiced_seconds=0.0) == ""
    assert _strip_hallucinations("Thanks.", speech_seconds=2.0, voiced_seconds=0.1) == ""


def test_a_lone_thank_you_that_was_said_stays():
    assert _strip_hallucinations("Thank you.", speech_seconds=2.0, voiced_seconds=0.6) == "Thank you."


def test_without_the_strict_measure_the_old_rule_holds():
    """The pinned rule: with 4 s of measured speech, "Thank you." is the user."""
    assert _strip_hallucinations("Thank you.", speech_seconds=4.0) == "Thank you."
    assert _strip_hallucinations("Thank you.", speech_seconds=0.2) == ""


def test_the_strict_measure_only_decides_a_whole_stock_phrase():
    """Real words around it: the strict measure changes nothing."""
    t = "So that is the plan for the sprint. Thanks for watching!"
    assert _strip_hallucinations(t, speech_seconds=12.0, voiced_seconds=0.0) == \
        _strip_hallucinations(t, speech_seconds=12.0)


# ── in the transcriber ──────────────────────────────────────────────────────

def _speech(seconds, db):
    t = np.arange(int(seconds * SR)) / SR
    v = np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    return v * (10 ** (db / 20) / np.sqrt(np.mean(v ** 2)))


def _noise(seconds, db, seed=1):
    n = np.random.default_rng(seed).normal(0.0, 1.0, int(seconds * SR))
    return n * (10 ** (db / 20) / np.sqrt(np.mean(n ** 2)))


def _wav(*parts, floor_db=-70.0):
    a = np.concatenate(parts)
    if floor_db is not None:
        a = a + _noise(len(a) / SR, floor_db, seed=5)
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
    return b.getvalue()


def _transcriber(monkeypatch, heard):
    t = object.__new__(WhisperTranscriber)
    t._backend = "groq"
    t._cloud_order = ["groq", "openai"]
    t._groq_skip_until = 0.0
    t.client = None
    t._groq_client = object()
    monkeypatch.setattr(WhisperTranscriber, "_dispatch_one", lambda self, ab, exclude=None: heard)
    monkeypatch.setattr(WhisperTranscriber, "_retry_if_incomplete", lambda self, ab, tr: tr)
    monkeypatch.setattr(tw, "load_vocab", lambda: [])
    return t


SILENT_END = (_noise(0.5, -70, 2), _speech(4.0, -40), _noise(2.0, -70, 3))
NO_SILENT_END = (_noise(0.5, -70, 2), _speech(4.0, -40))


def test_the_transcriber_drops_the_join_artefact_after_a_silent_end(monkeypatch):
    t = _transcriber(monkeypatch, BETFRED)
    assert t.transcribe_sync(_wav(*SILENT_END)) == \
        "I want you to look at the odds feed and expand on betfred"
    assert t.last_asr_response == BETFRED and type(t.last_asr_response) is str
    assert t.last_asr_filtered is True


def test_the_transcriber_keeps_it_when_the_take_ended_on_speech(monkeypatch):
    t = _transcriber(monkeypatch, BETFRED)
    assert t.transcribe_sync(_wav(*NO_SILENT_END)) == BETFRED


def test_a_thanks_groq_heard_said_is_kept_whatever_the_end(monkeypatch):
    heard = tw._Heard(BETFRED)
    heard.end_verified = True
    t = _transcriber(monkeypatch, heard)
    assert t.transcribe_sync(_wav(*SILENT_END)) == BETFRED


def test_room_noise_that_came_back_as_thank_you_is_nothing(monkeypatch):
    """The 27 October take, end to end: 2.1 s of room noise, "Thank you."
    from the provider, and the loose measure counting the noise as 2 s of
    speech (the padding's zeros pull its floor to nothing). Nothing is pasted
    now; the provider's words stay recoverable."""
    t = _transcriber(monkeypatch, "Thank you.")
    take = _wav(_noise(2.1, -62, 4), floor_db=None)
    assert t.transcribe_sync(take) == ""
    assert t.last_speech_seconds >= tw._NEAR_SILENCE_S          # the loose measure was fooled
    assert t.last_asr_response == "Thank you."
    assert t.last_asr_filtered is True


def test_a_short_thank_you_that_was_said_is_still_pasted(monkeypatch):
    t = _transcriber(monkeypatch, "Thank you.")
    take = _wav(_noise(1.0, -55, 4), _speech(0.6, -30), _noise(1.0, -55, 6), floor_db=None)
    assert t.transcribe_sync(take) == "Thank you."
