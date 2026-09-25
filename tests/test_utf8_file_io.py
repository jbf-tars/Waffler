#!/usr/bin/env python3
"""Every text file the app reads or writes is UTF-8, on every platform.

The bug: OpenAIStyler opened prompts/normal.txt with a bare open(path, 'r').
With no encoding Python uses the locale's, which is UTF-8 on a Mac but cp1252
on Windows. The prompt is UTF-8 (12 em-dashes, 6 arrows, a "≥", four "…", a
"€" and a "£"), so on Windows the model was sent "â€”" for every em-dash and
garbled arrows and symbols, while a Mac sent the real text: the two platforms
ran different prompts. The same bare open() and read_text() calls touched
settings.json, vocab.json, config.yaml, snippets.json, the .env file and
app.log.

These tests pin the prompt to its real text and scan src/*.py and app.py so a
text-mode open(), read_text() or write_text() without an encoding cannot come
back. No network, no keys, no private data.
"""

import ast
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import style_openai  # noqa: E402
from style_openai import OpenAIStyler  # noqa: E402


# ── (a) The styler sends the prompt file's real text ────────────────────────

def _windows_default_open(real_open):
    """An open() that behaves like Windows: no encoding means cp1252.

    Installed as ``style_openai.open`` so the test reproduces the Windows bug
    on any platform, including a Mac or Linux CI runner whose locale is UTF-8.
    """
    def fake_open(file, mode="r", buffering=-1, encoding=None, *args, **kwargs):
        if "b" not in mode and encoding is None:
            encoding = "cp1252"
        return real_open(file, mode, buffering, encoding, *args, **kwargs)
    return fake_open


@pytest.mark.parametrize("style", ["normal", "email"])
def test_prompt_template_is_the_file_decoded_as_utf8(monkeypatch, style):
    import builtins
    monkeypatch.setattr(style_openai, "open", _windows_default_open(builtins.open),
                        raising=False)
    # The real constructor, with no keys: builds no client, calls nothing.
    styler = OpenAIStyler(api_key="", groq_api_key="", cerebras_api_key="",
                          prompt_style=style)

    # Decoded as UTF-8 by hand. Line endings are normalised the way a
    # text-mode read does, because a Windows checkout has CRLF on disk.
    raw = (ROOT / "prompts" / f"{style}.txt").read_bytes()
    expected = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    assert styler.prompt_template == expected


def test_prompt_really_contains_the_characters_that_were_garbled():
    """Guards the test above: it only proves something while the prompt has
    non-ASCII text that cp1252 would mangle."""
    raw = (ROOT / "prompts" / "normal.txt").read_bytes()
    text = raw.decode("utf-8")
    for ch in ("\u2014", "\u2192", "\u2265", "\u2026", "\u20ac"):
        assert ch in text, f"prompts/normal.txt no longer contains {ch!r}"
    assert raw.decode("cp1252") != text


def test_loaded_prompt_has_no_mojibake(monkeypatch):
    import builtins
    monkeypatch.setattr(style_openai, "open", _windows_default_open(builtins.open),
                        raising=False)
    styler = OpenAIStyler(api_key="", groq_api_key="", cerebras_api_key="")
    t = styler.prompt_template
    assert "\xe2\u20ac" not in t, "em-dash decoded as cp1252 (\"\xe2\u20ac\")"
    assert "\u2014" in t and "\u2192" in t


# ── BOM tolerance for user-editable JSON ────────────────────────────────────

def test_vocab_and_settings_survive_a_bom(monkeypatch, tmp_path):
    """Notepad can save UTF-8 with a byte-order mark. json.loads rejects one,
    and the loaders swallow the error, so the user's words and settings would
    silently vanish."""
    import transcribe_whisper as tw

    vocab = tmp_path / "vocab.json"
    vocab.write_bytes("\ufeff".encode("utf-8") + json.dumps(["Zo\xeb", "\u0141ukasz"]).encode("utf-8"))
    settings = tmp_path / "settings.json"
    settings.write_bytes(b"\xef\xbb\xbf" + json.dumps({"language": "fr", "note": "café"},
                                                      ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(tw, "VOCAB_FILE", vocab)
    monkeypatch.setattr(tw, "SETTINGS_FILE", settings)

    assert tw.load_vocab() == ["Zoë", "Łukasz"]
    assert tw.load_settings() == {"language": "fr", "note": "café"}


# ── (b) No text-mode file I/O without an explicit encoding ──────────────────

_OPEN_LIKE = {"open", "io.open", "codecs.open", "os.fdopen"}
_TEMPFILE = {"tempfile.NamedTemporaryFile", "tempfile.TemporaryFile",
             "tempfile.SpooledTemporaryFile"}
_PATH_MODES = {"r", "w", "a", "x", "r+", "w+", "a+", "x+",
               "rt", "wt", "at", "xt", "rt+", "wt+", "at+", "xt+",
               "r+t", "w+t", "a+t", "x+t"}


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return f"{ast.unparse(func.value)}.{func.attr}"
    return ""


def _kw(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def _has_encoding(call: ast.Call, positional_index: int) -> bool:
    val = _kw(call, "encoding")
    if val is None and len(call.args) > positional_index:
        val = call.args[positional_index]
    if val is None:
        return False
    return not (isinstance(val, ast.Constant) and val.value is None)


def _mode(call: ast.Call, positional_index: int, default: str):
    """The call's mode as a string, or None when it is not a literal."""
    val = _kw(call, "mode")
    if val is None and len(call.args) > positional_index:
        val = call.args[positional_index]
    if val is None:
        return default
    if isinstance(val, ast.Constant) and isinstance(val.value, str):
        return val.value
    return None


def find_unencoded_text_io(source: str, filename: str = "<src>") -> list:
    """Every text-mode file open/read/write in ``source`` with no encoding.

    Covers open(), io.open(), codecs.open(), os.fdopen(), Path.open(),
    Path.read_text(), Path.write_text() and text-mode tempfile objects.
    Binary modes are exempt. A mode that is not a literal is reported too,
    since it cannot be checked.
    """
    problems = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
        where = f"{filename}:{node.lineno}: {ast.unparse(node)[:100]}"

        if name in _OPEN_LIKE:
            mode, enc_idx = _mode(node, 1, "r"), 3
        elif name in _TEMPFILE:
            mode, enc_idx = _mode(node, 0, "w+b"), 2
        elif attr == "read_text":
            mode, enc_idx = "r", 0
        elif attr == "write_text":
            mode, enc_idx = "w", 1
        elif attr == "open":
            # pathlib.Path.open(mode, buffering, encoding, ...). Other .open()
            # calls (wave.open(buf, "rb"), webbrowser.open(url),
            # Image.open(path), ZipFile.open(name)) take a file or URL first,
            # so they are recognised by a first argument that is not a mode.
            first = node.args[0] if node.args else None
            if first is not None and not (
                    isinstance(first, ast.Constant) and first.value in _PATH_MODES):
                continue
            mode, enc_idx = _mode(node, 0, "r"), 2
        else:
            continue

        if mode is None:
            if not _has_encoding(node, enc_idx):
                problems.append(f"{where}  (mode is not a literal; cannot tell text from binary)")
            continue
        if "b" in mode:
            continue
        if not _has_encoding(node, enc_idx):
            problems.append(where)
    return problems


def _app_sources():
    files = sorted((ROOT / "src").glob("*.py")) + [ROOT / "app.py"]
    assert len(files) > 10, "source scan found almost nothing; paths changed?"
    return files


def test_no_text_file_io_without_an_encoding():
    problems = []
    for path in _app_sources():
        rel = path.relative_to(ROOT).as_posix()
        problems += find_unencoded_text_io(path.read_text(encoding="utf-8"), rel)
    assert not problems, (
        "Text-mode file I/O without an encoding decodes as cp1252 on Windows "
        "and UTF-8 on a Mac. Pass encoding='utf-8' (or 'utf-8-sig' when reading "
        "a file a user might edit):\n  " + "\n  ".join(problems))


# The scanner has to catch what it claims to, or the test above proves nothing.

@pytest.mark.parametrize("code", [
    "open(p)",
    "open(p, 'r')",
    "open(p, 'a')",
    "open(p, mode='w')",
    "open(p, 'w', encoding=None)",
    "io.open(p, 'r')",
    "codecs.open(p, 'r')",
    "os.fdopen(fd, 'w')",
    "p.read_text()",
    "p.write_text(s)",
    "p.open()",
    "p.open('a')",
    "p.open(mode='w')",
    "tempfile.NamedTemporaryFile(mode='w')",
    "open(p, m)",
])
def test_scanner_flags_unencoded_text_io(code):
    assert find_unencoded_text_io(code), f"scanner missed: {code}"


@pytest.mark.parametrize("code", [
    "open(p, 'rb')",
    "open(p, 'wb')",
    "open(p, mode='ab')",
    "open(p, encoding='utf-8')",
    "open(p, 'r', encoding='utf-8-sig')",
    "open(p, 'w', -1, 'utf-8')",
    "os.fdopen(fd, 'w', encoding='utf-8')",
    "p.read_text(encoding='utf-8')",
    "p.read_text('utf-8')",
    "p.write_text(s, encoding='utf-8')",
    "p.write_text(s, 'utf-8')",
    "p.open('rb')",
    "p.open('r', encoding='utf-8')",
    "wave.open(buf, 'rb')",
    "webbrowser.open(url)",
    "Image.open(path)",
    "zf.open(name)",
    "tempfile.NamedTemporaryFile(suffix='.wav', delete=False)",
    "open(p, m, encoding='utf-8')",
])
def test_scanner_allows_binary_and_encoded_io(code):
    assert not find_unencoded_text_io(code), f"scanner wrongly flagged: {code}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
