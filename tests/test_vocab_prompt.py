"""The Vocabulary as a prompt, starred and capped (src/transcribe_whisper.py
vocab_prompt_hint, src/vocab_rank.py).

OpenAI's transcription and the local model get the words as a bare list.
Groq gets none (a word list derailed an 80 second clip there, v3.14.97) and
must stay that way. A short list is sent exactly as it always was.
"""
import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import transcribe_whisper as tw  # noqa: E402
import vocab_rank  # noqa: E402


@pytest.fixture(autouse=True)
def _no_usage_source():
    tw.set_vocab_usage_source(None)
    yield
    tw.set_vocab_usage_source(None)


def test_a_short_list_with_no_stars_is_the_prompt_it_always_was():
    words = ["Waffler", "Groq", "PostHog", "Sinéad"]
    assert tw.vocab_prompt_hint(words) == tw.vocab_to_prompt(words) == "Waffler, Groq, PostHog, Sinéad"


def test_no_words_no_prompt():
    assert tw.vocab_prompt_hint([]) == ""
    assert tw.vocab_prompt_hint(None) == ""


def test_stars_come_first(tmp_path, monkeypatch):
    (tmp_path / "vocab_stars.json").write_text(json.dumps(["Sinéad"]), encoding="utf-8")
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path))
    assert tw.vocab_prompt_hint(["Waffler", "Groq", "Sinéad"]) == "Sinéad, Waffler, Groq"


def test_a_long_list_is_cut_starred_then_most_used(tmp_path, monkeypatch):
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path))
    words = [f"Name{i:03d}" for i in range(500)]
    (tmp_path / "vocab_stars.json").write_text(json.dumps(["Name499", "Name400"]), encoding="utf-8")
    tw.set_vocab_usage_source(lambda: {"name321": 30, "NAME007": 5})
    hint = tw.vocab_prompt_hint(words)
    parts = hint.split(", ")
    assert parts[:4] == ["Name400", "Name499", "Name321", "Name007"]
    assert len(parts) <= vocab_rank.PROMPT_MAX_ENTRIES and len(hint) <= vocab_rank.PROMPT_MAX_CHARS
    assert tw.vocab_prompt_hint(words) == hint                       # the same every time


def test_a_usage_source_that_fails_does_not_cost_the_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path))

    def broken():
        raise OSError("history.json is locked")

    tw.set_vocab_usage_source(broken)
    hint = tw.vocab_prompt_hint([f"Name{i:03d}" for i in range(500)])
    assert hint.startswith("Name000, Name001") and len(hint) <= vocab_rank.PROMPT_MAX_CHARS


def test_if_the_ranking_itself_fails_the_whole_list_is_sent_as_before(monkeypatch):
    monkeypatch.setattr(vocab_rank, "prompt_words", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert tw.vocab_prompt_hint(["a", "b"]) == "a, b"


def test_only_the_openai_and_local_paths_use_the_ranked_prompt_and_groq_still_sends_none():
    src = (ROOT / "src" / "transcribe_whisper.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WhisperTranscriber")

    def calls(name):
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
        return {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}

    assert "vocab_prompt_hint" in calls("_transcribe_api")
    # The one local path (src/local_stt.py); it was "_transcribe_mlx" while
    # there were two local engines wired in here.
    assert "vocab_prompt_hint" in calls("_transcribe_local")
    groq = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_transcribe_groq")
    assert "vocab_prompt_hint" not in {c.func.id for c in ast.walk(groq)
                                       if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    # Groq's prompt is still off unless the owner's switch is set.
    assert 'os.environ.get("WAFFLER_WHISPER_PROMPT") == "1"' in ast.get_source_segment(src, groq)


# ── does the words-as-a-prompt reach the speech service at all? ──────────────
# Stars and the ranking only decide what goes into the prompt. Groq is sent no
# prompt, so with Groq doing the listening they change nothing, and the page
# hides them rather than show controls that do nothing (get_vocab_book,
# "prompt_used").

def _t(backend="groq", order=("groq", "openai"), groq=True, openai=True):
    t = object.__new__(tw.WhisperTranscriber)
    t._backend = backend
    t._cloud_order = list(order)
    t._groq_client = object() if groq else None
    t.client = object() if openai else None
    return t


@pytest.mark.parametrize("kwargs, want", [
    (dict(), False),                                              # Groq first: no prompt
    (dict(openai=False), False),                                  # Groq only
    (dict(order=("openai", "groq")), True),                       # OpenAI first
    (dict(groq=False, backend="api"), True),                      # OpenAI only
    (dict(order=("groq", "openai"), groq=False), True),           # Groq listed first but no key for it
    (dict(backend="local"), True),                                # the model on this computer
    (dict(backend="local", groq=False, openai=False), True),
    (dict(groq=False, openai=False, backend="api"), False),       # nobody to send it to
])
def test_whether_the_prompt_reaches_whoever_listens(monkeypatch, kwargs, want):
    monkeypatch.delenv("WAFFLER_WHISPER_PROMPT", raising=False)
    monkeypatch.setattr(tw.WhisperTranscriber, "_refresh_backend", lambda self: self._backend)
    assert _t(**kwargs).takes_vocab_prompt() is want


def test_the_owners_switch_sends_it_to_groq_too(monkeypatch):
    monkeypatch.setattr(tw.WhisperTranscriber, "_refresh_backend", lambda self: self._backend)
    monkeypatch.setenv("WAFFLER_WHISPER_PROMPT", "1")
    assert _t().takes_vocab_prompt() is True


def _book(ns_extra):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    node = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "get_vocab_book")

    class Cache:
        def vocab_usage(self):
            return {"by_entry": {}, "recent": []}

    ns = {"_history_cache": Cache(), "_log_to_file": lambda m: None, **ns_extra}
    exec(compile(ast.Module([node], []), "<app.py>", "exec"), ns)
    return ns["get_vocab_book"](None)


@pytest.mark.parametrize("takes", [True, False])
def test_the_page_is_told(tmp_path, monkeypatch, takes):
    monkeypatch.setattr(tw, "VOCAB_FILE", tmp_path / "vocab.json")
    monkeypatch.setattr(tw, "VOCAB_SOUNDS_FILE", tmp_path / "vocab_sounds.json")
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path))
    import types
    pipeline = types.SimpleNamespace(transcriber=types.SimpleNamespace(takes_vocab_prompt=lambda: takes))
    book = _book({"_pipeline": pipeline})
    assert book["ok"] and book["prompt_used"] is takes


def test_before_the_app_is_listening_the_answer_is_no(tmp_path, monkeypatch):
    monkeypatch.setattr(tw, "VOCAB_FILE", tmp_path / "vocab.json")
    monkeypatch.setattr(tw, "VOCAB_SOUNDS_FILE", tmp_path / "vocab_sounds.json")
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path))
    assert _book({"_pipeline": None})["prompt_used"] is False       # during setup
    assert _book({})["prompt_used"] is False
    assert _book({})["ok"] is True
