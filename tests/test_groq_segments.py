"""3.16.3: Groq's answer comes with its segments, and an invented ending goes.

Groq is asked for ``verbose_json``: the same words, cut into segments, each
with its start and end in the uploaded audio and Whisper's own
``no_speech_prob``. The transcript is the segments joined, which is exactly
the ``text`` Groq used to send (checked on the owner's recordings).

The final segment is dropped when it is only a thanks, a sign-off or a known
outro ("Thank you.", "Thank you from the heart.", "Thanks for watching!",
"Bye.") AND either Whisper itself says there was no speech
(no_speech_prob >= 0.5) or the audio under it holds no voice by the module's
own measure. 2 s of the owner's room noise came back as "Thank you." with
no_speech_prob 0.70; his real takes were 0.05 to 0.08. A thanks that was
said (a voice under it, a low no_speech_prob) is always kept.

An answer that cannot be read falls back to the plain text, silently. Fakes
only: no network, no keys.
"""
import io
import os
import sys
import types
import wave

import httpx
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import groq  # noqa: E402

import transcribe_whisper as tw  # noqa: E402
from transcribe_whisper import WhisperTranscriber  # noqa: E402

SR = 16000
_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/audio/transcriptions")


# ── audio: 0.3 s of padding, 2 s of speech, 1.5 s of room noise, padding ────

def _speech(seconds, db):
    t = np.arange(int(seconds * SR)) / SR
    v = np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    return v * (10 ** (db / 20) / np.sqrt(np.mean(v ** 2)))


def _noise(seconds, db, seed=1):
    n = np.random.default_rng(seed).normal(0.0, 1.0, int(seconds * SR))
    return n * (10 ** (db / 20) / np.sqrt(np.mean(n ** 2)))


def _wav(a):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
    return b.getvalue()


def _upload(speech_s=2.0, noise_s=1.5, db=-30.0):
    body = np.concatenate([_speech(speech_s, db), _noise(noise_s, -70)])
    body = body + _noise(len(body) / SR, -70, seed=2)
    return tw._pad_audio_with_silence(_wav(body))


AUDIO = _upload()          # speech 0.3-2.3 s, room noise 2.3-3.8 s, zeros to 4.1 s
SPEECH_WINDOW = (1.5, 2.3)
NOISE_WINDOW = (2.4, 3.7)


def seg(text, window=(0.3, 2.3), nsp=0.05):
    start, end = window if window is not None else (None, None)
    return {"id": 0, "seek": 0, "start": start, "end": end, "text": text,
            "tokens": [1, 2], "temperature": 0.0, "avg_logprob": -0.3,
            "compression_ratio": 1.1, "no_speech_prob": nsp}


def answer(*segments, text=None):
    """What the Groq SDK returns for verbose_json: a Transcription whose extra
    field ``segments`` is a list of dicts."""
    from groq.types.audio import Transcription
    joined = "".join(s["text"] for s in segments)
    return Transcription.model_validate({
        "text": joined if text is None else text, "task": "transcribe",
        "language": "English", "duration": 4.1, "segments": list(segments)})


def words(response, audio=AUDIO):
    out = tw._groq_words(response, audio)
    return None if out is None else str(out)


# ── reading the answer ──────────────────────────────────────────────────────

def test_the_transcript_is_the_segments_joined_which_is_the_old_text():
    r = answer(seg(" Okay so ship it on Monday."), seg(" And tell Malak.", (2.3, 3.0)))
    assert words(r) == "Okay so ship it on Monday. And tell Malak."
    assert words(r) == r.text.strip()


def test_a_plain_text_answer_is_used_as_it_is():
    """An older SDK, or a fake, hands back a string."""
    assert words("  hello there  ") == "hello there"


@pytest.mark.parametrize("bad", [
    {"segments": "nope"},
    {"segments": [{"start": 0.0, "end": 1.0}]},                 # a segment with no text
    {"segments": [{"text": 7, "start": 0.0, "end": 1.0}]},
    {"segments": []},
    {},
])
def test_segments_that_cannot_be_read_fall_back_to_the_text(bad):
    from groq.types.audio import Transcription
    r = Transcription.model_validate(dict({"text": " Okay so ship it. Thank you."}, **bad))
    assert words(r) == "Okay so ship it. Thank you."          # nothing dropped on no evidence


def test_segments_that_do_not_spell_the_text_are_not_trusted():
    r = answer(seg(" Okay so ship it."), seg(" Thank you.", NOISE_WINDOW), text=" Something else.")
    assert words(r) == "Something else."


def test_an_answer_with_no_text_and_no_segments_reads_as_nothing():
    assert tw._groq_words(types.SimpleNamespace(), AUDIO) is None
    assert tw._groq_words({"segments": "nope"}, AUDIO) is None


def test_typed_segments_like_the_openai_sdk_makes_are_read_too():
    r = types.SimpleNamespace(text=" Ship it. Thank you.", segments=[
        types.SimpleNamespace(**seg(" Ship it.")),
        types.SimpleNamespace(**seg(" Thank you.", NOISE_WINDOW, nsp=0.1))])
    assert words(r) == "Ship it."


# ── the final segment: the decision table ───────────────────────────────────

@pytest.mark.parametrize("window,nsp,dropped", [
    (NOISE_WINDOW, 0.10, True),       # no voice under it
    (NOISE_WINDOW, 0.70, True),       # no voice, and Whisper says no speech
    (SPEECH_WINDOW, 0.70, True),      # Whisper says no speech
    (SPEECH_WINDOW, 0.10, False),     # said: a voice under it, a low no_speech_prob
    (NOISE_WINDOW, None, True),       # no no_speech_prob: the audio decides
    (SPEECH_WINDOW, None, False),
    (None, 0.70, True),               # no times: no_speech_prob decides
    (None, 0.10, False),
    (None, None, False),              # no evidence at all: kept
])
def test_a_final_thank_you_is_dropped_only_on_evidence(window, nsp, dropped):
    r = answer(seg(" Okay so ship it on Monday.", (0.3, 1.5)), seg(" Thank you.", window, nsp))
    expected = "Okay so ship it on Monday." + ("" if dropped else " Thank you.")
    assert words(r) == expected


def test_the_threshold_is_the_chosen_one():
    assert tw._SEGMENT_NO_SPEECH_PROB == 0.5
    r = answer(seg(" Ship it.", (0.3, 1.5)), seg(" Thank you.", SPEECH_WINDOW, 0.49))
    assert words(r).endswith("Thank you.")
    r = answer(seg(" Ship it.", (0.3, 1.5)), seg(" Thank you.", SPEECH_WINDOW, 0.5))
    assert words(r) == "Ship it."


def test_the_audio_a_segment_before_it_claims_is_not_counted():
    """Whisper's times overlap: an invented ending that starts inside the last
    real word is judged on the audio after that word's segment."""
    r = answer(seg(" Okay so ship it on Monday.", (0.3, 2.3)), seg(" Thank you.", (2.0, 3.7), 0.1))
    assert words(r) == "Okay so ship it on Monday."


def test_times_past_the_end_of_the_audio_hold_no_voice():
    r = answer(seg(" Ship it.", (0.3, 2.3)), seg(" Thank you.", (4.5, 6.0), 0.1))
    assert words(r) == "Ship it."


@pytest.mark.parametrize("outro", [
    " Thank you.", " Thanks.", " THANK YOU!", " Thank you from the heart.",
    " Thank you very much.", " Thank you so much for watching.", " Thanks for watching!",
    " Thank you for your attention.", " Okay, thank you.", " Thank you. Bye.", " Bye.",
    " Bye-bye.", " Goodbye!", " See you next time.", " See you in the next video.",
    " Please subscribe.", " Like and subscribe!", " Subtitles by the Amara.org community",
    " you", " [Music]", " Take care.", " Have a great day.",
])
def test_every_kind_of_invented_ending_goes(outro):
    assert tw._is_outro_segment(outro)
    r = answer(seg(" Okay so ship it on Monday.", (0.3, 2.3)), seg(outro, NOISE_WINDOW, 0.1))
    assert words(r) == "Okay so ship it on Monday."


@pytest.mark.parametrize("real", [
    " And ship it on Friday.",
    " Thank you for the report on Friday and the slides.",     # a long tail is real content
    " Thanks to the new parser the build is faster now.",
    " We should thank you later.",
    " No thank you.",
    " So that is the plan, thank you.",                         # a thanks inside a sentence
])
def test_anything_else_stays_whatever_the_audio(real):
    assert not tw._is_outro_segment(real)
    r = answer(seg(" Okay so ship it on Monday.", (0.3, 2.3)), seg(real, NOISE_WINDOW, 0.9))
    assert words(r) == "Okay so ship it on Monday." + real


def test_a_run_of_invented_endings_all_go():
    r = answer(seg(" Okay so ship it on Monday.", (0.3, 2.3)),
               seg(" Thank you.", (2.4, 3.0), 0.1), seg(" Bye.", (3.0, 3.7), 0.1))
    assert words(r) == "Okay so ship it on Monday."


def test_a_take_that_was_only_an_invented_thank_you_is_empty():
    """The 27 October take: room noise alone, "Thank you.", no_speech_prob 0.70."""
    noise_only = tw._pad_audio_with_silence(_wav(_noise(2.0, -50)))
    r = answer(seg(" Thank you.", (0.0, 2.28), 0.698))
    assert words(r, noise_only) == ""


def test_a_thanks_that_was_said_is_marked_as_heard():
    r = answer(seg(" Okay so ship it on Monday.", (0.3, 1.5)), seg(" Thank you.", SPEECH_WINDOW, 0.05))
    out = tw._groq_words(r, AUDIO)
    assert out.endswith("Thank you.") and out.end_verified is True
    plain = tw._groq_words(answer(seg(" Okay so ship it on Monday.")), AUDIO)
    assert plain.end_verified is False
    assert tw._groq_words("hello", AUDIO).end_verified is False


# ── the request ─────────────────────────────────────────────────────────────

class FakeTranscriptions:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append({k: v for k, v in kwargs.items() if k != "file"})
        item = self.outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _groq_transcriber(*outcomes):
    t = object.__new__(WhisperTranscriber)
    t._groq_client = types.SimpleNamespace(
        audio=types.SimpleNamespace(transcriptions=FakeTranscriptions(*outcomes)))
    return t


@pytest.fixture(autouse=True)
def plain_settings(monkeypatch):
    monkeypatch.delenv("WAFFLER_WHISPER_PROMPT", raising=False)
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": "en"})


def calls(t):
    return t._groq_client.audio.transcriptions.calls


def test_groq_is_asked_for_segments():
    t = _groq_transcriber(answer(seg(" Okay so ship it on Monday.", (0.3, 2.3)),
                                 seg(" Thank you.", NOISE_WINDOW, 0.1)))
    assert t._transcribe_groq(AUDIO) == "Okay so ship it on Monday."
    assert calls(t)[0]["response_format"] == "verbose_json"
    assert calls(t)[0]["model"] == "whisper-large-v3"


def test_a_refused_detailed_answer_falls_back_to_plain_text(monkeypatch):
    lines = []
    monkeypatch.setattr(tw, "_wlog", lines.append)
    refusal = groq.BadRequestError(
        "Error code: 400 - response_format verbose_json is not supported",
        response=httpx.Response(400, request=_REQ), body=None)
    t = _groq_transcriber(refusal, " Okay so ship it. Thank you.")
    assert t._transcribe_groq(AUDIO) == "Okay so ship it. Thank you."
    assert [c["response_format"] for c in calls(t)] == ["verbose_json", "text"]
    assert lines and not any(ch.isdigit() for ch in " ".join(lines))
    assert "ship" not in " ".join(lines)


def test_an_unreadable_answer_falls_back_to_plain_text(monkeypatch):
    lines = []
    monkeypatch.setattr(tw, "_wlog", lines.append)
    t = _groq_transcriber(types.SimpleNamespace(), " Okay so ship it.")
    assert t._transcribe_groq(AUDIO) == "Okay so ship it."
    assert [c["response_format"] for c in calls(t)] == ["verbose_json", "text"]
    assert lines and not any(ch.isdigit() for ch in " ".join(lines))


@pytest.mark.parametrize("err", [
    groq.APIConnectionError(request=_REQ),
    groq.RateLimitError("Error code: 429", response=httpx.Response(429, request=_REQ), body=None),
    groq.BadRequestError("Error code: 400 - file is not valid audio",
                         response=httpx.Response(400, request=_REQ), body=None),
])
def test_other_failures_are_not_asked_twice(err):
    """A connection error, a limit or a bad file is the provider's answer: the
    failover and retry rules decide what happens next, as before."""
    t = _groq_transcriber(err)
    with pytest.raises(type(err)):
        t._transcribe_groq(AUDIO)
    assert len(calls(t)) == 1


def test_each_piece_of_a_long_take_is_judged_on_its_own_audio(monkeypatch):
    """Segments are per piece: piece one ends on an invented thank-you over
    room noise, piece two on a thanks that was said."""
    first, second = _upload(), _upload(speech_s=2.5, noise_s=0.6)
    t = _groq_transcriber(
        answer(seg(" First part.", (0.3, 2.3)), seg(" Thank you.", NOISE_WINDOW, 0.1)),
        answer(seg(" Second part.", (0.3, 2.0)), seg(" Thanks, James.", (2.0, 2.8), 0.1)))
    t._backend = "groq"
    t._cloud_order = ["groq", "openai"]
    t.client = None
    out = t._dispatch_pieces([first, second])
    assert out == "First part. Second part. Thanks, James."
    # The end of the take is the last piece's: its thanks had a voice under it.
    assert out.end_verified is True
