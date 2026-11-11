"""Clips of 7 s or longer go up to the speech-to-text providers as FLAC.

FLAC is lossless and about half the size of WAV for speech; on 15 s clips
Groq's whisper-large-v3 answered measurably sooner with it. Shorter clips stay
WAV (nothing to gain). If soundfile (libsndfile) will not load, or a clip will
not encode, the clip goes up as WAV as before and one plain line is logged.
The filename's extension and the MIME type always agree. Fakes only: no
network, no keys.
"""
import io
import os
import sys
import types
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import transcribe_whisper as tw  # noqa: E402
from transcribe_whisper import WhisperTranscriber  # noqa: E402

SR = 16000


def wav(seconds, width=2, rate=SR, channels=1):
    n = int(seconds * rate)
    t = np.arange(n) / rate
    sig = 0.3 * np.sin(2 * np.pi * 220 * t) * (1 + 0.5 * np.sin(2 * np.pi * 3 * t))
    if width == 2:
        frames = (sig * 32767).astype("<i2")
    else:
        frames = ((sig + 1) * 127).astype(np.uint8)
    if channels > 1:
        frames = np.repeat(frames, channels)
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames.tobytes())
    return b.getvalue()


@pytest.fixture(autouse=True)
def fresh_loader(monkeypatch):
    """Each test loads soundfile afresh, so a fake or missing one is seen."""
    monkeypatch.setattr(tw, "_soundfile", None)


# ── which encoding ──────────────────────────────────────────────────────────

def test_a_short_clip_goes_up_as_the_same_wav():
    audio = wav(3.0)
    assert tw._upload_file(audio) == ("audio.wav", audio, "audio/wav")


def test_a_long_clip_goes_up_as_flac():
    pytest.importorskip("soundfile")
    audio = wav(10.0)
    name, data, mime = tw._upload_file(audio)
    assert (name, mime) == ("audio.flac", "audio/flac")
    assert data[:4] == b"fLaC"
    assert len(data) < len(audio)


@pytest.mark.parametrize("seconds,expected", [(6.9, "audio.wav"), (7.0, "audio.flac")])
def test_the_threshold_is_seven_seconds(seconds, expected):
    pytest.importorskip("soundfile")
    assert tw._upload_file(wav(seconds))[0] == expected


def test_flac_holds_exactly_the_same_samples():
    sf = pytest.importorskip("soundfile")
    for channels in (1, 2):
        audio = wav(8.0, channels=channels)
        data, rate = sf.read(io.BytesIO(tw._upload_file(audio)[1]), dtype="int16",
                             always_2d=True)
        with wave.open(io.BytesIO(audio), "rb") as w:
            original = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        assert rate == SR and data.shape[1] == channels
        assert np.array_equal(data.reshape(-1), original)


def test_anything_but_16_bit_wav_goes_up_unchanged():
    eight_bit = wav(10.0, width=1)
    assert tw._upload_file(eight_bit) == ("audio.wav", eight_bit, "audio/wav")
    junk = b"not a wav at all" * 1000
    assert tw._upload_file(junk) == ("audio.wav", junk, "audio/wav")


def test_name_and_type_always_agree():
    for seconds in (1.0, 12.0):
        name, _, mime = tw._upload_file(wav(seconds))
        assert mime == {"wav": "audio/wav", "flac": "audio/flac"}[name.rsplit(".", 1)[1]]


# ── falling back to WAV ─────────────────────────────────────────────────────

def test_no_soundfile_means_wav_and_one_plain_log_line(monkeypatch):
    lines = []
    monkeypatch.setattr(tw, "_wlog", lines.append)
    monkeypatch.setitem(sys.modules, "soundfile", None)   # import raises ImportError
    audio = wav(10.0)
    assert tw._upload_file(audio) == ("audio.wav", audio, "audio/wav")
    assert tw._upload_file(audio) == ("audio.wav", audio, "audio/wav")
    assert len(lines) == 1 and "WAV" in lines[0]


def test_a_missing_native_library_means_wav(monkeypatch):
    """soundfile raises OSError, not ImportError, when libsndfile is absent."""
    lines = []
    monkeypatch.setattr(tw, "_wlog", lines.append)
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "soundfile":
            raise OSError("sndfile library not found")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    audio = wav(10.0)
    assert tw._upload_file(audio)[0] == "audio.wav"
    assert len(lines) == 1 and "OSError" in lines[0]


def test_an_encode_failure_means_wav(monkeypatch):
    lines = []
    monkeypatch.setattr(tw, "_wlog", lines.append)

    def broken(*a, **k):
        raise RuntimeError("Error opening <_io.BytesIO>: unspecified")

    monkeypatch.setitem(sys.modules, "soundfile", types.SimpleNamespace(SoundFile=broken))
    audio = wav(10.0)
    assert tw._upload_file(audio) == ("audio.wav", audio, "audio/wav")
    assert len(lines) == 1 and "RuntimeError" in lines[0] and "unspecified" not in lines[0]


# ── the requests ────────────────────────────────────────────────────────────

class FakeTranscriptions:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.answer


@pytest.fixture
def plain_settings(monkeypatch):
    monkeypatch.delenv("WAFFLER_WHISPER_PROMPT", raising=False)
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": "en"})
    monkeypatch.setattr(tw, "load_vocab", lambda: [])


@pytest.mark.parametrize("seconds,name,mime", [
    (3.0, "audio.wav", "audio/wav"), (10.0, "audio.flac", "audio/flac")])
def test_groq_gets_the_chosen_file(plain_settings, seconds, name, mime):
    if name.endswith("flac"):
        pytest.importorskip("soundfile")
    audio = wav(seconds)
    fake = FakeTranscriptions(" Okay.")
    t = object.__new__(WhisperTranscriber)
    t._groq_client = types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=fake))
    t._transcribe_groq(audio)
    sent = fake.calls[0]["file"]
    assert (sent[0], sent[2]) == (name, mime)
    # The timeout is still worked out from the clip's WAV size.
    assert fake.calls[0]["timeout"] == tw._request_timeout(len(audio))


@pytest.mark.parametrize("seconds,name,mime", [
    (3.0, "audio.wav", "audio/wav"), (10.0, "audio.flac", "audio/flac")])
def test_openai_gets_the_chosen_file(plain_settings, seconds, name, mime):
    if name.endswith("flac"):
        pytest.importorskip("soundfile")
    fake = FakeTranscriptions(" Okay. ")
    t = object.__new__(WhisperTranscriber)
    t.model = tw.OPENAI_TRANSCRIBE_MODEL
    t.client = types.SimpleNamespace(audio=types.SimpleNamespace(transcriptions=fake))
    assert t._transcribe_api(wav(seconds)) == "Okay."
    sent = fake.calls[0]["file"]
    assert (sent[0], sent[2]) == (name, mime)
