"""An invented "Thank you." is judged the same through Waffler Plus as with
the person's own Groq key.

With a key, Groq's answer comes with its segments (times in the upload and
Whisper's no_speech_prob), and _groq_words drops a final sign-off that the
audio under it or Whisper itself says nobody said. Since 26 Nov 2026 the
account service's dictation answer carries the segments its words were made
of, and the Plus path puts them through that same filter. Here the same take
goes both ways, with the same segments, and must come out the same.

The real transcriber runs against a stand-in Groq client and a stand-in
account service (tests/_account_fake.py): no network, no provider, no model.
"""
import io
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import transcribe_whisper as tw  # noqa: E402
from test_plus_dictation import choose, plus  # noqa: E402,F401

SR = 16000

# The take as recorded: 2 s of speech and 0.9 s of a quiet room (too short a
# silent end to be cut). transcribe_sync pads 0.3 s of zeros at both ends, so
# in the upload the speech is 0.3-2.3 s and the room 2.3-3.2 s.
SPEECH = (0.3, 2.3)
ROOM = (2.4, 3.1)


def _take() -> bytes:
    t = np.arange(int(2.0 * SR)) / SR
    v = np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    speech = v * (10 ** (-25 / 20) / np.sqrt(np.mean(v ** 2)))
    body = np.concatenate([speech, np.zeros(int(0.9 * SR))])
    body = body + np.random.default_rng(3).normal(0, 10 ** (-70 / 20), len(body))
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(body, -1, 1) * 32767).astype(np.int16).tobytes())
    return b.getvalue()


TAKE = _take()
WORDS = " Can you send the invoice to Sarah by Friday afternoon?"
TIDY = "Can you send the invoice to Sarah by Friday afternoon?"

# A sign-off the dictation's text rules (_strip_hallucinations) cannot judge
# alone: "Bye." Only the segments can say whether anyone said it. (A final
# "Thank you." as its own sentence is taken off by the text rules on both
# paths already, said or not; it is here once to show that holds too.)
CASES = {
    # (the provider's segments: text, window, no_speech_prob), what is typed with a key
    "a goodbye over the quiet room goes": (
        [(WORDS, (0.3, 2.0), 0.05), (" Bye.", ROOM, 0.1)], TIDY),
    "a goodbye Whisper doubts, over the quiet room, goes": (
        [(WORDS, (0.3, 2.0), 0.05), (" Bye.", ROOM, 0.7)], TIDY),
    "a goodbye with a voice under it stays": (
        [(WORDS, (0.3, 1.6), 0.05), (" Bye.", (1.6, 2.3), 0.1)], TIDY + " Bye."),
    "a goodbye Whisper doubts but with a clear voice under it stays": (
        [(WORDS, (0.3, 1.6), 0.05), (" Bye.", (1.6, 2.3), 0.7)], TIDY + " Bye."),
    "a thanks and a goodbye over the room both go": (
        [(WORDS, (0.3, 2.0), 0.05), (" Thank you.", (2.35, 2.7), 0.2), (" Bye.", (2.7, 3.1), 0.2)], TIDY),
    "a thanks over the quiet room goes": (
        [(WORDS, (0.3, 2.0), 0.05), (" Thank you.", ROOM, 0.1)], TIDY),
    "no sign-off: nothing changes": (
        [(WORDS, (0.3, 2.3), 0.05)], TIDY),
}


def _groq_answer(segments):
    from groq.types.audio import Transcription
    rows = [{"id": i, "seek": 0, "start": w[0], "end": w[1], "text": text, "tokens": [1],
             "temperature": 0.0, "avg_logprob": -0.3, "compression_ratio": 1.1, "no_speech_prob": nsp}
            for i, (text, w, nsp) in enumerate(segments)]
    return Transcription.model_validate({"text": "".join(r["text"] for r in rows), "task": "transcribe",
                                         "language": "English", "duration": 3.5, "segments": rows})


def _plus_answer(segments):
    """What the account service sends for the same provider answer when its
    own rule kept every line: the segments' text without Whisper's leading
    space, "raw" their join (natter/hosted.py segment_for_app)."""
    rows = [{"start_s": w[0], "end_s": w[1], "text": text.strip(), "no_speech_prob": nsp}
            for text, w, nsp in segments]
    return {"raw": " ".join(r["text"] for r in rows), "segments": rows}


class _Uploads:
    def __init__(self, answer):
        self.answer, self.files = answer, []

    def create(self, **kwargs):
        self.files.append(kwargs["file"])
        return self.answer


def with_key(segments, monkeypatch):
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": "en"})
    t = tw.WhisperTranscriber(api_key="", groq_api_key="gsk_test_not_a_real_key")
    uploads = _Uploads(_groq_answer(segments))
    t._groq_client = types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=uploads))
    t.plus_allowed = False
    return t.transcribe_sync(TAKE), uploads


def through_plus(segments, fake, tidied=None, send_segments=True):
    answer = _plus_answer(segments)
    fake.heard = {"raw": answer["raw"], "text": tidied if tidied is not None else answer["raw"]}
    if send_segments:
        fake.heard["segments"] = answer["segments"]
    t = tw.WhisperTranscriber(api_key="", groq_api_key="")
    t.plus_allowed = True
    return t.transcribe_sync(TAKE), t


def _samples(upload) -> np.ndarray:
    import soundfile as sf
    _name, data, _mime = upload
    audio, _rate = sf.read(io.BytesIO(data), dtype="int16")
    return audio


@pytest.mark.parametrize("case", list(CASES))
def test_the_same_take_comes_out_the_same_with_a_key_and_through_plus(case, plus, monkeypatch):
    segments, expected = CASES[case]
    keyed, uploads = with_key(segments, monkeypatch)
    plussed, t = through_plus(segments, plus)
    assert keyed == expected
    assert plussed == keyed
    # The segments' times mean the same audio both ways: the very upload Groq gets.
    sent = plus.dictations[-1]["file"]
    assert np.array_equal(_samples((None, sent["bytes"], sent["type"])), _samples(uploads.files[-1]))


def test_a_dropped_ending_also_leaves_the_services_tidied_text(plus):
    segments, _ = CASES["a thanks and a goodbye over the room both go"]
    heard, t = through_plus(segments, plus, tidied="Can you send the invoice to Sarah by Friday? Thank you. Bye.")
    assert heard == TIDY
    assert t.last_plus == {"text": "Can you send the invoice to Sarah by Friday?", "cleaned": True, "code": ""}


def test_an_ending_the_tidied_text_does_not_show_sends_the_words_as_heard(plus):
    segments, _ = CASES["a goodbye over the quiet room goes"]
    _heard, t = through_plus(segments, plus, tidied="Can you send Sarah the invoice, bye, by Friday?")
    assert t.last_plus["trimmed"] is True          # app.py _plus_words then types the words as heard


def test_a_kept_thanks_keeps_the_tidied_text_as_the_service_wrote_it(plus):
    segments, _ = CASES["a goodbye with a voice under it stays"]
    _heard, t = through_plus(segments, plus, tidied="Can you send it? Bye.")
    assert t.last_plus == {"text": "Can you send it? Bye.", "cleaned": True, "code": ""}


def test_an_older_service_that_sends_no_segments_is_used_as_before(plus):
    segments, _ = CASES["a goodbye over the quiet room goes"]
    heard, t = through_plus(segments, plus, send_segments=False)
    # Unchecked, as before 26 Nov 2026: the words come back as the service heard them.
    assert heard == TIDY + " Bye."
    assert "trimmed" not in t.last_plus


def test_without_segments_the_gap_is_real():
    """What this closes: without the segments, the dictation filter alone
    keeps an invented goodbye after a full sentence over a short quiet end."""
    out = tw._strip_hallucinations(TIDY + " Bye.", speech_seconds=2.0, voiced_seconds=2.0,
                                   trailing_silence_s=0.9)
    assert out.endswith("Bye.")


def test_the_whole_dictation_pastes_the_tidied_text_without_the_invented_ending(plus, monkeypatch):
    """At the clean-up step (app.py _plus_words) the service's tidied text,
    without the ending the audio showed nobody said, is what goes in."""
    import _pipeline_harness as H
    from test_plus_dictation import dictate, transcriber
    segments, _ = CASES["a goodbye over the quiet room goes"]
    answer = _plus_answer(segments)
    plus.heard = {"raw": answer["raw"], "text": "Can you send Sarah the invoice by Friday? Bye.",
                  "segments": answer["segments"]}
    plus.vocab = []
    t, _cloud = transcriber(monkeypatch)
    t.plus_allowed = False                                # the app sets it, not the test
    p = dictate(H.make_pipeline(plus.data, transcriber=t, styler=H.FakeStyler(), wav=TAKE))
    assert p.styler.calls == 0
    assert p.clipboard.pastes == ["Can you send Sarah the invoice by Friday?"]


# ── a meeting's lines (src/meetings/transcribe.py) ──────────────────────────

def _voice(seconds):
    t = np.arange(int(seconds * SR)) / SR
    return (0.1 * np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 4 * t) > -0.5)).astype(np.float32)


MEETING_LINES = [
    # A line with a voice under it that Whisper is sure nobody said: the
    # meeting rule takes a sign-off on no_speech_prob alone.
    [{"start": 0.0, "end": 2.0, "text": " Shall we ship it on Monday?", "no_speech_prob": 0.7},
     {"start": 2.0, "end": 2.8, "text": " Thank you.", "no_speech_prob": 0.7}],
    [{"start": 0.0, "end": 2.0, "text": " Shall we ship it on Monday?", "no_speech_prob": 0.05},
     {"start": 2.0, "end": 2.8, "text": " Thank you.", "no_speech_prob": 0.05}],
]


@pytest.mark.parametrize("lines", MEETING_LINES)
def test_a_meetings_lines_come_out_the_same_with_a_key_and_through_plus(lines, monkeypatch):
    from meetings import transcribe as mt
    audio = _voice(3.0)
    monkeypatch.setattr(mt, "uses_local", lambda s=None: False)
    monkeypatch.setattr(mt, "uses_plus", lambda: False)
    keyed = mt.transcribe(audio, {"language": "en"}, backend=lambda a, p, m, lang: lines)

    # What the service sends for the same answer (natter/hosted.py segment_for_app).
    answer = {"ok": True, "seconds": 3.0, "language": "en", "segments": [
        {"start_s": g["start"], "end_s": g["end"], "text": g["text"].strip(),
         "no_speech_prob": g["no_speech_prob"]} for g in lines]}
    monkeypatch.setattr(mt, "uses_plus", lambda: True)
    monkeypatch.setattr(mt, "_account", lambda: types.SimpleNamespace(
        transcribe=lambda flac, language=None: answer, FAILED="failed"))
    plussed = mt.transcribe(audio, {"language": "en"})
    assert [g["text"] for g in plussed] == [g["text"] for g in keyed]
    assert [(g["start_s"], g["end_s"]) for g in plussed] == [(g["start_s"], g["end_s"]) for g in keyed]


def test_the_account_client_reads_no_speech_prob_and_tolerates_its_absence():
    from src import account
    assert account._segments([{"start_s": 0, "end_s": 1.5, "text": "Hi", "no_speech_prob": 0.3}]) == [
        {"start_s": 0.0, "end_s": 1.5, "text": "Hi", "no_speech_prob": 0.3}]
    assert account._segments([{"start_s": 0, "end_s": 1.5, "text": "Hi", "words": []}]) == [
        {"start_s": 0.0, "end_s": 1.5, "text": "Hi"}]
    assert account._segments(None) is None
    assert account._segments([{"start_s": "0", "end_s": 1, "text": "Hi"}]) is None
