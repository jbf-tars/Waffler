"""The keys file (.env in the data folder) takes the three keys, one line each.

Before: _update_env_var wrote any name and any value. A value with a line
break in it added a line of its own, and .env is loaded with override=True,
so a page that someone else's script got into (the page can reach this method
through pywebview) could have pointed Groq or OpenAI at another server, or
named a program for Waffler to start. Now only OPENAI_API_KEY, GROQ_API_KEY
and CEREBRAS_API_KEY are written, and a key with a space, a line break or
any other control character in it is refused with a plain sentence.
"""
import ast
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


def _update_env_var(data_dir):
    klass = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "_update_env_var")
    ns = {"os": os, "DATA_DIR": Path(data_dir)}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    return ns["_update_env_var"]


def test_a_key_is_written_and_then_replaced(tmp_path):
    put = _update_env_var(tmp_path)
    put(object(), "OPENAI_API_KEY", "sk-proj-first")
    put(object(), "GROQ_API_KEY", "gsk_second")
    put(object(), "OPENAI_API_KEY", "sk-proj-third")
    assert (tmp_path / ".env").read_text(encoding="utf-8").splitlines() == [
        "OPENAI_API_KEY=sk-proj-third", "GROQ_API_KEY=gsk_second"]


def test_only_the_three_keys_go_in_the_file(tmp_path):
    put = _update_env_var(tmp_path)
    for name in ("GROQ_BASE_URL", "OPENAI_BASE_URL", "WAFFLER_LLAMA_SERVER", "HTTPS_PROXY", ""):
        with pytest.raises(ValueError):
            put(object(), name, "https://elsewhere.example")
    assert not (tmp_path / ".env").exists()


def test_a_key_with_a_line_break_or_a_space_is_refused(tmp_path):
    put = _update_env_var(tmp_path)
    put(object(), "GROQ_API_KEY", "gsk_good")
    before = (tmp_path / ".env").read_text(encoding="utf-8")
    for bad in ("gsk_x\nWAFFLER_LLAMA_SERVER=C:\\elsewhere.exe", "gsk_x\rOPENAI_BASE_URL=https://x",
                "gsk_x y", "gsk_x\t", "gsk_\x00x", "gsk_\x1bx"):
        with pytest.raises(ValueError) as refused:
            put(object(), "GROQ_API_KEY", bad)
        assert "key" in str(refused.value).lower()
    assert (tmp_path / ".env").read_text(encoding="utf-8") == before
