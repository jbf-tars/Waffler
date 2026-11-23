"""Dictation: words the model wrote with nobody speaking never reach the
paste, and a made-up sign-off in front of real words goes. One copy of the
rules (src/meetings/transcribe.py: `invented`, `strip_signoffs`), used by
Meetings and by dictation alike, in any language, from every engine."""
import io
import json
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import local_stt  # noqa: E402
import transcribe_whisper as tw  # noqa: E402

SR = 16000
rng = np.random.default_rng(5)


def _wav_of(samples):
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out.getvalue()


def _voice(seconds, dbfs=-20.0):
    t = np.arange(int(seconds * SR)) / SR
    on = (np.sin(2 * np.pi * 4 * t) > -0.5).astype(np.float32)
    return (10 ** (dbfs / 20) * np.sqrt(2) * np.sin(2 * np.pi * 180 * t) * on).astype(np.float32)


def _quiet_room(seconds, dbfs=-70.0):
    return (rng.standard_normal(int(seconds * SR)) * 10 ** (dbfs / 20)).astype(np.float32)


def _cough(seconds=0.3, dbfs=-20.0):
    return (rng.standard_normal(int(seconds * SR)) * 10 ** (dbfs / 20)).astype(np.float32)


@pytest.fixture
def heard(monkeypatch, _isolated_data_dir):
    state = types.SimpleNamespace(text="")
    monkeypatch.setattr(local_stt, "transcribe", lambda audio, language=None, prompt=None, background=False: state.text)
    monkeypatch.setattr(tw, "load_vocab", lambda: [])
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": "en"})
    (_isolated_data_dir / "settings.json").write_text(json.dumps({"transcription_mode": "local"}), encoding="utf-8")
    return state


def _say(heard, text, samples):
    t = tw.WhisperTranscriber(api_key="sk-test-not-a-real-key", groq_api_key="gsk_test_not_a_real_key")
    heard.text = text
    return t, t.transcribe_sync(_wav_of(samples))


@pytest.mark.parametrize("text", ["Kjell, du har en kjell.", "The", "Y Llywodraeth Cymru yw'r Llywodraeth Cymru?",
                                  "Teksting av Nicolai Winther", "字幕由Amara.org社区提供"])
def test_a_quiet_take_gets_nothing_whatever_the_model_wrote(heard, text):
    t, out = _say(heard, text, np.concatenate([_quiet_room(3.0), _cough(0.05), _quiet_room(3.0)]))
    assert out == "" and t.last_asr_filtered is True


def test_a_long_line_over_a_cough_gets_nothing(heard):
    audio = np.concatenate([_quiet_room(6.0), _cough(0.3), _quiet_room(6.0)])
    t, out = _say(heard, "Det var en gang en liten gutt som het Ola og han bodde i Bergen", audio)
    assert out == ""


def test_one_phrase_over_and_over_gets_nothing(heard):
    loop = " ".join(["Y Llywodraeth"] * 12)
    t, out = _say(heard, loop, np.concatenate([_quiet_room(3.0), _voice(0.5), _quiet_room(3.0)]))
    assert out == ""


@pytest.mark.parametrize("text", ["Ship it on Monday, then tell Sarah.", "Yes.", "Kjell, du har en kjell.", "Thank you."])
def test_the_same_words_said_aloud_stay(heard, text):
    """The rule judges the audio, not the language or the words."""
    t, out = _say(heard, text, np.concatenate([_quiet_room(0.5), _voice(3.0), _quiet_room(0.5)]))
    assert out == text


def test_a_quiet_voice_stays(heard):
    t, out = _say(heard, "Ship it on Monday, then tell Sarah.",
                  np.concatenate([_quiet_room(0.5), _voice(3.0, dbfs=-38.0), _quiet_room(0.5)]))
    assert out == "Ship it on Monday, then tell Sarah."


def test_a_long_real_dictation_is_not_judged_a_loop(heard):
    """Long real speech squeezes well, so the loop rule is left to short takes."""
    sentences = ["We agreed the budget is nine thousand pounds for the kitchen.",
                 "The electrician comes on Tuesday and the plumber on Thursday.",
                 "Please send the revised quote to Priya before the end of the week.",
                 "The cabinets arrive on the fourteenth and the worktop a week later.",
                 "Tom will check the measurements again and report back to everyone."]
    text = " ".join(sentences[i % 5] for i in range(30))
    t, out = _say(heard, text, _voice(150.0))
    assert out == text


# ── a made-up sign-off in front of real words ────────────────────────────────

def test_a_sign_off_in_front_of_real_words_goes(heard):
    t, out = _say(heard, "Thank you for watching So that the plan is to sort out the rest.",
                  np.concatenate([_voice(4.0)]))
    assert out == "So that the plan is to sort out the rest."


def test_a_voiced_thank_you_alone_stays(heard):
    t, out = _say(heard, "Thank you.", np.concatenate([_quiet_room(0.5), _voice(2.0), _quiet_room(0.5)]))
    assert out == "Thank you."


def test_a_take_that_is_loud_all_the_way_through_is_not_judged(heard):
    """No quiet stretch to tell a voice from (a microphone that squeezes
    speech flat): the voice meter would read none, so the words stay."""
    t = np.arange(int(8.0 * SR)) / SR
    flat = (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    t, out = _say(heard, "Ship it on Monday, then tell Sarah.", flat)
    assert out == "Ship it on Monday, then tell Sarah."
