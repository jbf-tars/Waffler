"""A speech request never goes out with no language unless the person chose
Auto-detect: unset or empty sends the computer's own language (Whisper left
to guess writes Norwegian over silence). A pick always wins."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

import languages  # noqa: E402
import transcribe_whisper  # noqa: E402
from meetings import transcribe as mt  # noqa: E402

RATE = 16000


def _locale(monkeypatch, *names):
    monkeypatch.setattr(languages, "_locale_names", lambda: list(names))


@pytest.mark.parametrize("name, code", [
    ("en_GB.UTF-8", "en"), ("en_US", "en"), ("de_DE.UTF-8", "de"), ("pt-BR", "pt"),
    ("nb_NO", "no"), ("zh_CN", "zh"), ("cy_GB", "cy"), ("fr", "fr")])
def test_the_computers_language_is_read_from_its_locale(monkeypatch, name, code):
    _locale(monkeypatch, name)
    assert languages.system_language() == code


@pytest.mark.parametrize("names", [(), ("C",), ("POSIX",), ("xx_YY",)])
def test_a_locale_that_says_nothing_gives_none_and_resolve_falls_back_to_english(monkeypatch, names):
    _locale(monkeypatch, *names)
    assert languages.system_language() is None
    assert languages.resolve(None) == "en" and languages.resolve("") == "en"


def test_a_pick_wins_and_auto_is_a_pick(monkeypatch):
    _locale(monkeypatch, "de_DE")
    assert languages.resolve("fr") == "fr"
    assert languages.resolve("auto") is None, "Auto-detect keeps working for who chose it"
    assert languages.resolve(None) == "de" and languages.resolve("") == "de" and languages.resolve("  ") == "de"
    assert languages.effective("auto") == "auto" and languages.effective(None) == "de"


class _Rec:
    def __init__(self):
        self.kwargs = None
        self.audio = self
        self.transcriptions = self

    def create(self, **kw):
        self.kwargs = kw
        return "hello"


@pytest.mark.parametrize("path", ["groq", "api"])
@pytest.mark.parametrize("setting, sent", [(None, "de"), ("", "de"), ("auto", None), ("fr", "fr")])
def test_dictation_in_the_cloud_sends_the_computers_language_when_unset(monkeypatch, path, setting, sent):
    _locale(monkeypatch, "de_DE.UTF-8")
    stored = {} if setting is None else {"language": setting}
    monkeypatch.setattr(transcribe_whisper, "load_settings", lambda: stored)
    t = object.__new__(transcribe_whisper.WhisperTranscriber)
    rec = _Rec()
    t._groq_client = rec
    t.client = rec
    t.model = "whisper-1"
    (t._transcribe_groq if path == "groq" else t._transcribe_api)(b"RIFF" + b"\0" * 64)
    assert rec.kwargs.get("language") == sent


@pytest.mark.parametrize("setting, sent", [(None, "de"), ("", "de"), ("auto", None), ("fr", "fr")])
def test_dictation_on_this_computer_and_on_plus_send_it_too(monkeypatch, setting, sent):
    _locale(monkeypatch, "de_DE.UTF-8")
    stored = {} if setting is None else {"language": setting}
    monkeypatch.setattr(transcribe_whisper, "load_settings", lambda: stored)
    monkeypatch.setattr(transcribe_whisper, "load_vocab", lambda: [])
    t = object.__new__(transcribe_whisper.WhisperTranscriber)
    seen = {}
    monkeypatch.setattr(transcribe_whisper._local_stt, "transcribe",
                        lambda audio, language=None, prompt=None: seen.update(language=language) or "hi")
    t._transcribe_local(b"RIFF" + b"\0" * 64)
    assert seen["language"] == sent

    sent_plus = {}

    class Acct:
        FAILED = "no"

        def dictate(self, data, **kw):
            sent_plus.update(kw)
            return {"ok": True, "text": "hi", "raw": "hi", "cleaned": True, "code": "", "seconds": 1}
    monkeypatch.setattr(transcribe_whisper, "_account", lambda: Acct())
    t.plus_tidy = True
    t.last_plus = None
    monkeypatch.setattr(transcribe_whisper, "_upload_file", lambda a: ("a.wav", a, "audio/wav"))
    t._transcribe_plus(b"RIFF" + b"\0" * 64)
    assert sent_plus["language"] == sent


def _voice(seconds):
    t = np.arange(int(seconds * RATE)) / RATE
    return (0.1 * np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 4 * t) > -0.5)).astype(np.float32)


@pytest.mark.parametrize("setting, sent", [(None, "de"), ("", "de"), ("auto", ""), ("en", "en"), ("fr", "fr")])
def test_meetings_send_the_picked_language_or_the_computers_never_none_by_default(monkeypatch, setting, sent):
    """Own key (backend stands in for the provider), imports and "transcribe
    again" all go through mt.transcribe."""
    _locale(monkeypatch, "de_DE.UTF-8")
    monkeypatch.setattr(mt, "uses_plus", lambda: False)
    monkeypatch.setattr(mt, "uses_local", lambda s=None: False)
    got = []
    settings = {} if setting is None else {"language": setting}
    mt.transcribe(_voice(3.0), settings, backend=lambda a, p, m, lang: got.append(lang) or [])
    assert got == [sent]


@pytest.mark.parametrize("setting, sent", [("", "de"), ("auto", ""), ("en", "en")])
def test_meetings_on_plus_and_on_this_computer_send_it_too(monkeypatch, setting, sent):
    _locale(monkeypatch, "de_DE.UTF-8")
    audio = _voice(3.0)
    plus = []
    monkeypatch.setattr(mt, "uses_plus", lambda: True)
    monkeypatch.setattr(mt, "_transcribe_plus", lambda a, lang, **k: plus.append(lang) or [])
    mt.transcribe(audio, {"language": setting})
    assert plus == [sent]
    monkeypatch.setattr(mt, "uses_plus", lambda: False)
    monkeypatch.setattr(mt, "uses_local", lambda s=None: True)
    local = []
    mt.transcribe(audio, {"language": setting}, backend=lambda a, p, m, lang: local.append(lang) or [])
    assert local == [sent]


def test_the_meetings_page_offers_both_choices_and_the_default_is_english():
    from meetings.store import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["language"] == "en"
    js = next((ROOT / "ui" / "meetings-app" / "assets").glob("index-*.js")).read_text(encoding="utf-8")
    assert 'value:"auto",children:"Detect automatically"' in js
    assert 'value:"",children:"This computer' in js
