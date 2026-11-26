"""The Vocabulary feature end to end: saving the list, the page's add and
remove, and showing what the Vocabulary changed in a dictation.

Bugs fixed in 3.15:
  * a failed save still showed "Added" (the page ignored set_vocab's answer);
  * vocab.json was written in place, so a crash mid-write left a file that
    loads as an empty list, and the next Add replaced the whole list with
    one word;
  * nothing was checked: non-strings, blank or repeated entries and
    any length went straight into vocab.json;
  * the only record of a correction was a line in app.log.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import transcribe_whisper as tw  # noqa: E402

LOGIC = ROOT / "ui" / "logic.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")


def js(expr):
    code = (f"const L = require({json.dumps(str(LOGIC))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True,
                         timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture
def vocab_file(tmp_path, monkeypatch):
    f = tmp_path / "vocab.json"
    monkeypatch.setattr(tw, "VOCAB_FILE", f)
    return f


# ── saving ───────────────────────────────────────────────────────────────────

def test_save_writes_utf8_and_reads_back(vocab_file):
    r = tw.save_vocab(["Sinéad", "Łukasz", "James Donnelly"])
    assert r == {"ok": True, "count": 3, "words": ["Sinéad", "Łukasz", "James Donnelly"]}
    raw = vocab_file.read_bytes().decode("utf-8")
    assert "Sinéad" in raw            # written as UTF-8, not \u escapes
    assert tw.load_vocab() == ["Sinéad", "Łukasz", "James Donnelly"]


def test_save_tidies_the_list(vocab_file):
    r = tw.save_vocab(["  Ronan ", "ronan", "", "James  Donnelly", None, 7])
    assert r["ok"] and r["words"] == ["Ronan", "James Donnelly"]


def test_save_refuses_what_is_not_a_list(vocab_file):
    assert tw.save_vocab("Ronan")["ok"] is False
    assert not vocab_file.exists()


def test_save_refuses_an_overlong_entry(vocab_file):
    r = tw.save_vocab(["x" * (tw.VOCAB_MAX_ENTRY_LEN + 1)])
    assert r["ok"] is False and str(tw.VOCAB_MAX_ENTRY_LEN) in r["error"]
    assert tw.save_vocab(["x" * tw.VOCAB_MAX_ENTRY_LEN])["ok"] is True


def test_save_refuses_a_list_over_the_limit(vocab_file):
    words = [f"Entry{i}" for i in range(tw.VOCAB_MAX_ENTRIES + 1)]
    r = tw.save_vocab(words)
    assert r["ok"] is False and "full" in r["error"]
    assert tw.save_vocab(words[:-1])["ok"] is True


def test_an_unreadable_file_is_kept_not_overwritten(vocab_file):
    vocab_file.write_text('["Ronan", "Kar', encoding="utf-8")     # cut short
    assert tw.load_vocab() == []
    r = tw.save_vocab(["Ashkan"])
    assert r["ok"] and "kept as vocab.unreadable-" in r["log"]
    kept = list(vocab_file.parent.glob("vocab.unreadable-*.json"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == '["Ronan", "Kar'
    assert tw.load_vocab() == ["Ashkan"]


def test_a_readable_file_is_simply_replaced(vocab_file):
    tw.save_vocab(["Ronan"])
    r = tw.save_vocab(["Ronan", "Karin"])
    assert "log" not in r
    assert not list(vocab_file.parent.glob("vocab.unreadable-*"))


def test_the_next_dictation_uses_the_saved_list_without_a_restart(vocab_file):
    assert tw.apply_vocab_corrections("karen rang", tw.load_vocab())[0] == "karen rang"
    tw.save_vocab(["Karin"])
    assert tw.apply_vocab_corrections("karen rang", tw.load_vocab())[0] == "Karin rang"
    tw.save_vocab([])
    assert tw.apply_vocab_corrections("karen rang", tw.load_vocab())[0] == "karen rang"


def test_the_app_saves_through_save_vocab():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src[src.index("    def set_vocab("):src.index("    def demo_overlay_show(")]
    assert "save_vocab(words)" in body
    assert "write_text" not in body


# ── what the Vocabulary changed, kept with the dictation ─────────────────────

def test_the_pipeline_keeps_the_changes_with_the_journal_entry():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'item["vocab_changes"]' in src
    assert 'new_item["vocab_changes"]' in src          # recordings sent later too
    assert "apply_vocab_corrections(" not in src


def test_the_journal_card_no_longer_says_it():
    """A card used to say "Vocabulary changed Karen to Karin" under the text.
    The change is still kept with the entry (above) and counted in Settings,
    Words; the card says nothing, a correction is simply made
    (tests/test_words_section.py)."""
    src = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    logic = (ROOT / "ui" / "logic.js").read_text(encoding="utf-8")
    assert "vocabChangesLine" not in src and "function vocabChangesLine" not in logic
    assert "Vocabulary changed" not in src and "Vocabulary changed" not in logic


# ── the page's Add ───────────────────────────────────────────────────────────

@needs_node
def test_add_decisions():
    assert js("L.vocabAdd(['Ronan'], '   ', 60).action") == "empty"
    assert js("L.vocabAdd(['Ronan'], 'x'.repeat(61), 60).action") == "too_long"
    assert js("L.vocabAdd(['Ronan'], 'Ronan', 60).action") == "same"
    assert js("L.vocabAdd(['Cobie', 'Ronan'], 'COBie', 60)") == {
        "action": "respell", "word": "COBie", "existing": "Cobie", "next": ["COBie", "Ronan"]}
    assert js("L.vocabAdd(['Ronan'], '  James   Donnelly ', 60)") == {
        "action": "add", "word": "James Donnelly", "next": ["Ronan", "James Donnelly"]}


@needs_node
def test_the_page_limit_matches_the_app():
    assert js("L.VOCAB_MAX_ENTRY_LEN") == tw.VOCAB_MAX_ENTRY_LEN


def test_the_page_checks_the_save_answer():
    src = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    add = src[src.index("async function addVocabWord()"):src.index("async function deleteVocabWord(")]
    remove = src[src.index("async function deleteVocabWord("):src.index("async function openPracticeEditor(")]
    for body in (add, remove):
        assert "res.ok" in body and "res.words" in body
