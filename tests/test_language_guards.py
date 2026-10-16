"""English-only fix-ups must not damage text in another language.

The pipeline was written for English. Four places could alter or misjudge
text that is not:

* ``_basic_clean`` (the short-sentence path that skips the model, and the
  fallback) removes "um" and "er" as hesitations. They are ordinary words in
  Portuguese, German and Dutch.
* ``_fix_mid_sentence_caps`` lowercases English words such as "Can" and "Will"
  in the middle of a sentence; in other languages they are names or nouns.
* ``_retry_if_incomplete`` and ``quality.assess`` compare whitespace-separated
  words with seconds of speech. In Chinese, Japanese and Thai a sentence is
  one "word", so every long take looked impossibly short: it was sent to a
  second provider and carried a "Check this one" mark for nothing.

English behaviour is unchanged to the character. Offline.
"""
import io
import os
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import languages  # noqa: E402
import quality  # noqa: E402
import style_openai  # noqa: E402
import transcribe_whisper as tw  # noqa: E402
from style_openai import OpenAIStyler  # noqa: E402


def _styler():
    return object.__new__(OpenAIStyler)


def _setting(monkeypatch, language):
    settings = {} if language is None else {"language": language}
    monkeypatch.setattr(tw, "load_settings", lambda: settings)


# ── _basic_clean ─────────────────────────────────────────────────────────────

HESITATING = "um I think er we should ah go uh now"


@pytest.mark.parametrize("language", [None, "en"])
def test_english_still_loses_its_hesitations(monkeypatch, language):
    _setting(monkeypatch, language)
    assert _styler()._basic_clean(HESITATING) == "I think we should go now"


@pytest.mark.parametrize("language, text", [
    ("pt", "quero um café agora"),                 # "um" is "a" / "one"
    ("de", "er kommt um drei Uhr"),                # "er" is "he", "um" is "at"
    ("nl", "er is een probleem"),                  # "er" is "there"
    ("auto", "quero um café e er kommt um drei"),  # language not known
    ("es", "dame un café por favor"),
])
def test_words_that_are_real_words_elsewhere_are_kept(monkeypatch, language, text):
    _setting(monkeypatch, language)
    assert _styler()._basic_clean(text) == text


@pytest.mark.parametrize("language", ["pt", "de", "auto"])
def test_the_unambiguous_hesitations_still_go(monkeypatch, language):
    _setting(monkeypatch, language)
    assert _styler()._basic_clean("uh quero um café erm agora ah") == "quero um café agora"


def test_the_language_can_be_given_instead_of_read(monkeypatch):
    _setting(monkeypatch, "en")
    assert _styler()._basic_clean("quero um café", "pt") == "quero um café"
    _setting(monkeypatch, "pt")
    assert _styler()._basic_clean("um quero er", "en") == "quero"


def test_a_short_portuguese_dictation_reaches_the_page_whole(monkeypatch):
    """The path that skips the model: five words or fewer."""
    _setting(monkeypatch, "pt")
    s = _styler()
    styled, usage = s.style("quero um café")
    assert styled == "quero um café" and usage["api_used"] is False


def test_the_unreadable_language_is_english(monkeypatch):
    monkeypatch.setattr(tw, "load_settings", lambda: (_ for _ in ()).throw(OSError("locked")))
    assert style_openai._dictation_language() == "en"
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": ["fr"]})
    assert style_openai._dictation_language() == "en"
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": ""})
    assert style_openai._dictation_language() == "en"


# ── _fix_mid_sentence_caps ───────────────────────────────────────────────────

def test_english_caps_are_fixed_as_before(monkeypatch):
    _setting(monkeypatch, "en")
    assert _styler()._fix_mid_sentence_caps("consumed What I mean") == "consumed what I mean"
    assert _styler()._fix_mid_sentence_caps("I told Will about it") == "I told will about it"


@pytest.mark.parametrize("language, text", [
    ("tr", "Bugün Can geldi"),                    # a name
    ("de", "Er hat Will und May gesehen"),        # names, mid-sentence capitals
    ("auto", "Ayer vi a Can en la tienda"),
    ("es", "Dijo que Do es una nota"),
])
def test_other_languages_keep_their_capitals(monkeypatch, language, text):
    _setting(monkeypatch, language)
    assert _styler()._fix_mid_sentence_caps(text) == text


# ── scripts without spaces ───────────────────────────────────────────────────

@pytest.mark.parametrize("text, unspaced", [
    ("今天我们讨论一下预算和发货计划。", True),
    ("これはテストです。", True),
    ("สวัสดีครับ วันนี้อากาศดี", True),
    ("Hello world, this is English.", False),
    ("Hola, esta es una prueba.", False),
    ("Привет, это проверка", False),
    ("", False), (None, False), ("123 456 !", False),
    ("send it to 田中 on Monday please", False),      # a name in English text
])
def test_a_script_without_spaces_is_told_from_the_text(text, unspaced):
    assert languages.is_unspaced(text) is unspaced


def _wav(seconds, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    samples = (6000 * np.sin(2 * np.pi * 180.0 * t)).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())
    return out.getvalue()


def _transcriber():
    t = object.__new__(tw.WhisperTranscriber)
    t._backend = "api"
    t._cloud_order = ["groq", "openai"]
    t._groq_skip_until = 0.0
    t.client = object()
    t._groq_client = object()
    t._last_cloud_provider = "groq"
    return t


def test_a_long_chinese_take_is_not_sent_to_a_second_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(tw.WhisperTranscriber, "_dispatch_one",
                        lambda self, ab, exclude=None: calls.append(exclude) or "x")
    chinese = "今天我们讨论一下预算和发货计划，然后看看下个月能交付什么。"
    assert _transcriber()._retry_if_incomplete(_wav(30.0), chinese) == chinese
    assert calls == [], "words per second means nothing without spaces"


def test_a_long_english_take_that_is_too_short_still_retries(monkeypatch):
    """The rule is unchanged where it works."""
    full = " ".join(["word"] * 120)
    monkeypatch.setattr(tw.WhisperTranscriber, "_dispatch_one", lambda self, ab, exclude=None: full)
    assert _transcriber()._retry_if_incomplete(_wav(30.0), "word word word word") == full


def _assess(text, words, seconds=30.0):
    return quality.assess(speech_seconds=seconds, transcript_words=words, styled_words=words,
                          asr_filtered=False, styling_provider="groq", styled_text=text)


def test_a_long_japanese_take_is_not_marked_as_missing_words():
    q = _assess("今日は予算と出荷の予定について話しました。", 2)
    assert "low_word_rate" not in q["flags"] and q["level"] == "ok"


def test_the_same_numbers_in_english_are_still_marked():
    q = _assess("Just a few words here.", 5)
    assert "low_word_rate" in q["flags"] and q["level"] == "low"
