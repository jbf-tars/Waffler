"""The language picker: the list, the don't-translate rule, and what stays the same.

Waffler listens in the language in Settings (``language``, "en" unless set,
"auto" to let Whisper decide) and the clean-up prompt, which is written in
English, is told to keep that language. The rule goes into the existing
``{dialect_instruction}`` slot; ``prompts/normal.txt`` is not touched. In
English, which is the default, the prompt must be byte for byte what it was
before the picker existed.

app.py's bridge methods are lifted out of its source and run against a
temporary settings.json. Offline: no keys, no network.
"""
import ast
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import languages  # noqa: E402
import style_openai  # noqa: E402
import transcribe_whisper  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

TEXT = ("bonjour je voudrais reserver une table pour ce soir s'il vous plait vraiment "
        "merci beaucoup")

# The sentences the prompt carried before the picker, spelled out here so that
# a change to any of them fails a test instead of passing unnoticed.
NEUTRAL = "Use the same spelling as the user. Do not change spelling conventions."
BRITISH = ("Use British English spelling (e.g. colour, organise, centre, behaviour, realise, "
           "programme, defence, licence, favour, catalogue).")
AMERICAN = ("Use American English spelling (e.g. color, organize, center, behavior, realize, "
            "program, defense, license, favor, catalog).")


# ── the list ─────────────────────────────────────────────────────────────────

def test_list_has_auto_and_english_first_and_unique_codes():
    codes = [c for c, _ in languages.LANGUAGES]
    assert codes[0] == "auto" and codes[1] == "en", codes[:3]
    assert len(codes) == len(set(codes)), "duplicate language code"


def test_every_listed_code_is_valid_and_named():
    for code, name in languages.LANGUAGES:
        assert languages.is_valid(code)
        assert languages.language_name(code) == name and name


def test_unknown_codes_are_not_valid():
    for bad in ("", "xx", "English", "EN", "en-GB", " en"):
        assert not languages.is_valid(bad), bad


# ── the rule ─────────────────────────────────────────────────────────────────

def test_english_needs_no_extra_rule():
    assert languages.language_instruction("en") is None
    assert languages.language_instruction("") is None
    assert languages.language_instruction(None) is None


def test_named_language_forbids_translation():
    text = languages.language_instruction("es")
    assert "Spanish" in text and "Never translate" in text, text


def test_auto_forbids_translation_too():
    text = languages.language_instruction("auto")
    assert "any language" in text and "Never translate" in text, text


# ── what fills the prompt's {dialect_instruction} ───────────────────────────

@pytest.mark.parametrize("settings, expected", [
    ({}, NEUTRAL),
    ({"language": "en"}, NEUTRAL),
    ({"language": "en", "dialect": "auto"}, NEUTRAL),
    ({"dialect": "en-GB"}, BRITISH),
    ({"language": "en", "dialect": "en-GB"}, BRITISH),
    ({"dialect": "en-US"}, AMERICAN),
    ({"language": "en", "dialect": "en-US"}, AMERICAN),
    # What cannot be a language is English, the default.
    ({"language": ""}, NEUTRAL),
    ({"language": "klingon", "dialect": "en-GB"}, BRITISH),
    ({"language": None}, NEUTRAL),
    ({"language": ["fr"]}, NEUTRAL),
    ({"language": 5, "dialect": "en-US"}, AMERICAN),
])
def test_english_and_default_settings_keep_the_spelling_sentences_to_the_byte(settings, expected):
    assert style_openai._dialect_instruction(settings) == expected


@pytest.mark.parametrize("dialect", ["auto", "en-GB", "en-US"])
def test_another_language_replaces_the_english_spelling_rule(dialect):
    got = style_openai._dialect_instruction({"language": "fr", "dialect": dialect})
    assert got == languages.language_instruction("fr")
    assert "British" not in got and "American" not in got and "spelling as the user" not in got


def test_auto_replaces_it_too():
    got = style_openai._dialect_instruction({"language": "auto", "dialect": "en-GB"})
    assert got == languages.language_instruction("auto")


# ── the prompt the model is actually sent ────────────────────────────────────

def _prompt_for(monkeypatch, settings):
    """The prompt string the styler hands to Groq for one dictation."""
    monkeypatch.setattr(transcribe_whisper, "load_settings", lambda: settings)
    s = style_openai.OpenAIStyler(groq_api_key="gsk_" + "x" * 20)
    seen = {}

    def fake_groq(prompt, start):
        seen["prompt"] = prompt
        return TEXT, {"provider": "groq", "input_tokens": 0, "output_tokens": 0,
                      "api_used": True}

    monkeypatch.setattr(s, "_style_groq", fake_groq)
    s._groq_skip_until = 0
    s.style(TEXT)
    return s, seen["prompt"]


@pytest.mark.parametrize("settings, sentence", [
    ({}, NEUTRAL),
    ({"language": "en"}, NEUTRAL),
    ({"language": "en", "dialect": "en-GB"}, BRITISH),
    ({"dialect": "en-US"}, AMERICAN),
])
def test_the_english_prompt_is_byte_identical_to_the_one_before_the_picker(
        monkeypatch, settings, sentence):
    template = (ROOT / "prompts" / "normal.txt").read_text(encoding="utf-8")
    s, prompt = _prompt_for(monkeypatch, settings)
    assert prompt == template.format(transcript=TEXT, dialect_instruction=sentence)
    assert s.prompt_template == template


def test_the_prompt_template_still_has_only_its_two_slots():
    """The language rule rides in the dialect slot; no third placeholder."""
    template = (ROOT / "prompts" / "normal.txt").read_text(encoding="utf-8")
    template.format(transcript="x", dialect_instruction="y")      # raises on a third
    assert "{dialect_instruction}" in template and "{transcript}" in template
    assert "language" not in template.split("{dialect_instruction}")[1].split("{transcript}")[0]


def test_styler_prompt_carries_the_language_rule(monkeypatch):
    _s, p = _prompt_for(monkeypatch, {"language": "fr", "dialect": "en-GB"})
    assert "Never translate" in p and "French" in p, "language rule missing from prompt"
    assert "British English" not in p, "English dialect rule must not apply to French"
    assert p.count(languages.language_instruction("fr")) == 1


# ── the bridge: get_languages, get_settings, save_settings ───────────────────

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _method(name, **ns_extra):
    fn = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"os": os, "_pipeline": None, "_languages": languages,
          "_log_to_file": lambda m: None}
    ns.update(ns_extra)
    exec(compile(ast.Module([fn], []), "<app.py Api>", "exec"), ns)
    return ns[name]


def test_get_languages_is_the_picker_list():
    got = _method("get_languages")(object())
    assert got[0] == {"code": "auto", "name": "Auto-detect"}
    assert got[1] == {"code": "en", "name": "English"}
    assert [(g["code"], g["name"]) for g in got] == languages.LANGUAGES
    assert {"code": "es", "name": "Spanish"} in got


def test_save_settings_stores_a_known_language(tmp_path):
    save = _method("save_settings")
    api = settings_api(tmp_path, {"hotkey_keys": ["fn"]})
    for code in ("es", "auto", "en"):
        r = save(api, {"language": code})
        assert r["ok"] is True, r
        assert api.data["language"] == code
    assert api.data["hotkey_keys"] == ["fn"]          # the rest is kept


@pytest.mark.parametrize("bad", ["", "xx", "English", "EN", "en-GB", None, 7, ["es"], {"a": 1}])
def test_save_settings_refuses_a_language_that_does_not_exist(tmp_path, bad):
    save = _method("save_settings")
    api = settings_api(tmp_path, {"language": "fr", "dialect": "en-GB"})
    r = save(api, {"language": bad, "dialect": "en-US"})
    assert r["ok"] is False
    assert r["error"] == "Waffler doesn't know that language. Pick one from the list."
    assert chr(0x2014) not in r["error"]
    # Nothing in the payload was saved, the language included.
    assert api.data == {"language": "fr", "dialect": "en-GB"}
    assert api.saves == 0


def test_save_settings_without_a_language_leaves_it_alone(tmp_path):
    save = _method("save_settings")
    api = settings_api(tmp_path, {"language": "de"})
    assert save(api, {"auto_paste": False})["ok"] is True
    assert api.data == {"language": "de", "auto_paste": False}


def test_get_settings_reports_the_language(tmp_path):
    import hotkey_rules  # noqa: F401  (src on the path, as the app has it)
    get = _method("get_settings")
    api = types.SimpleNamespace(_load_settings_file=lambda: {"language": "es"})
    assert get(api)["language"] == "es"
    api = types.SimpleNamespace(_load_settings_file=lambda: {})
    assert get(api)["language"] == "en"


# ── the settings reach speech to text: "auto" is left out everywhere ─────────

class _Recorder:
    """A provider client that records the keyword arguments of a transcription."""

    def __init__(self):
        self.kwargs = None
        self.audio = self
        self.transcriptions = self

    def create(self, **kw):
        self.kwargs = kw
        return "hello"


def _transcriber(monkeypatch, language, which):
    monkeypatch.setattr(transcribe_whisper, "load_settings", lambda: {"language": language})
    t = object.__new__(transcribe_whisper.WhisperTranscriber)
    rec = _Recorder()
    t._groq_client = rec
    t.client = rec
    t.model = "whisper-1"
    return t, rec


@pytest.mark.parametrize("language, sent", [("auto", None), ("en", "en"), ("es", "es"), ("ja", "ja")])
@pytest.mark.parametrize("path", ["groq", "api"])
def test_auto_sends_no_language_to_the_cloud_providers(monkeypatch, language, sent, path):
    t, rec = _transcriber(monkeypatch, language, path)
    audio = b"RIFF" + b"\0" * 64
    (t._transcribe_groq if path == "groq" else t._transcribe_api)(audio)
    assert rec.kwargs.get("language") == sent
    if sent is None:
        assert "language" not in rec.kwargs


def test_local_whisper_reads_auto_the_same_way():
    """The two local paths, read as source: 'auto' is skipped, never passed."""
    src = (ROOT / "src" / "transcribe_whisper.py").read_text(encoding="utf-8")
    assert src.count('if lang and lang != "auto":') == 3          # mlx, groq, openai
    assert 'fw_lang  = lang if lang != "auto" else None' in src   # faster-whisper
