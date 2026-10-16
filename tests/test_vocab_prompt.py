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
    assert "vocab_prompt_hint" in calls("_transcribe_mlx")
    groq = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_transcribe_groq")
    assert "vocab_prompt_hint" not in {c.func.id for c in ast.walk(groq)
                                       if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    # Groq's prompt is still off unless the owner's switch is set.
    assert 'os.environ.get("WAFFLER_WHISPER_PROMPT") == "1"' in ast.get_source_segment(src, groq)
