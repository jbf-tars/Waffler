"""A "sounds like" that is an everyday word.

A spelling such as "your" for a name rewrites every "your" a person dictates:
in a review of real use one such pair changed every "your" and "our" in 13 of
192 takes, all of them wrongly. So:

  * a one-word everyday spelling is ASKED about (the Vocabulary page), and
    only a spelling the person confirmed is taken everywhere ("because you
    said so" now means that confirmation);
  * until it is answered it is neither dropped nor applied blindly: it is
    taken only where the word is written like a name, with a capital in the
    middle of a sentence;
  * Remove takes the spelling off the entry; nothing else about it changes.

A spelling of several words ("waffle her") or one that is not an everyday
word ("grok") is taken as before. The names below are invented.
"""
import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import transcribe_whisper as tw  # noqa: E402

WORDS = ["Noor", "Groq", "Waffler"]
SOUNDS = {"Noor": ["your", "our"], "Groq": ["grok"], "Waffler": ["waffle her"]}


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(tw, "VOCAB_FILE", tmp_path / "vocab.json")
    monkeypatch.setattr(tw, "VOCAB_SOUNDS_FILE", tmp_path / "vocab_sounds.json")
    return tmp_path


# ── which spellings are asked about ──────────────────────────────────────────

@pytest.mark.parametrize("spelling", ["your", "Our", "the", "mark", "plus", "posters", " place "])
def test_one_everyday_word_is_asked_about(spelling):
    assert tw.is_everyday_spelling(spelling)


@pytest.mark.parametrize("spelling", ["grok", "waffle her", "your own", "woffler", "nash", "c#", "", None, 7])
def test_anything_else_is_not(spelling):
    assert not tw.is_everyday_spelling(spelling)


# ── unanswered: only where it is written like a name ─────────────────────────

@pytest.mark.parametrize("text", [
    "I looked at your website and our plan for it.",
    "Your report is late. Our team knows.",
    "is this your app or our app",
    "He said: Your turn.\nOur turn next.",
    "\"Your call,\" she said.",
    "SEND YOUR REPORT NOW",
])
def test_ordinary_speech_is_never_rewritten(files, text):
    assert tw.apply_vocab_changes(text, WORDS, SOUNDS) == (text, [])


def test_a_capital_in_the_middle_of_a_sentence_is_a_name(files):
    text, changes = tw.apply_vocab_changes("I asked Your about it, and then Our rang back.", WORDS, SOUNDS)
    assert text == "I asked Noor about it, and then Noor rang back."
    assert changes == [("Your", "Noor"), ("Our", "Noor")]


def test_the_other_spellings_of_the_same_list_are_taken_as_before(files):
    text, changes = tw.apply_vocab_changes("ask grok and waffle her about your plan", WORDS, SOUNDS)
    assert text == "ask Groq and Waffler about your plan"
    assert changes == [("grok", "Groq"), ("waffle her", "Waffler")]


def test_the_entrys_own_spelling_still_gets_its_capital(files):
    assert tw.apply_vocab_changes("ask noor about your plan", WORDS, SOUNDS)[0] == "ask Noor about your plan"


def test_try_a_sentence_marks_only_what_changed(files):
    text, changes, spans = tw.apply_vocab_marked("your friend Your is here", WORDS, SOUNDS)
    assert text == "your friend Noor is here" and spans == [(12, 16)]


# ── the question and its two answers ─────────────────────────────────────────

def test_the_unanswered_ones_are_listed_for_the_page(files):
    tw.save_vocab(WORDS, SOUNDS)
    assert tw.everyday_sounds_pending() == [
        {"word": "Noor", "spelling": "your"}, {"word": "Noor", "spelling": "our"}]


def test_keep_is_the_person_saying_so(files):
    tw.save_vocab(WORDS, SOUNDS)
    r = tw.answer_everyday_sound("Noor", "your", True)
    assert r["ok"] and r["pending"] == [{"word": "Noor", "spelling": "our"}]
    assert json.loads((files / "vocab_sounds_kept.json").read_text(encoding="utf-8")) == {"Noor": ["your"]}
    # Kept: taken everywhere, as every spelling was before. The other one waits.
    text, changes = tw.apply_vocab_changes("your plan and our plan", WORDS, tw.load_sounds_like(WORDS))
    assert text == "Noor plan and our plan" and changes == [("your", "Noor")]
    assert tw.load_sounds_like(WORDS)["Noor"] == ["your", "our"]


def test_remove_takes_the_spelling_off_and_nothing_else(files):
    tw.save_vocab(WORDS, SOUNDS)
    r = tw.answer_everyday_sound("noor", "Your", False)
    assert r["ok"] and r["sounds_like"] == {"Noor": ["our"], "Groq": ["grok"], "Waffler": ["waffle her"]}
    assert r["pending"] == [{"word": "Noor", "spelling": "our"}]
    assert tw.load_vocab() == WORDS
    tw.answer_everyday_sound("Noor", "our", False)
    assert "Noor" not in tw.load_sounds_like(WORDS) and tw.everyday_sounds_pending() == []


def test_an_answer_about_something_not_saved_changes_nothing(files):
    tw.save_vocab(WORDS, SOUNDS)
    for word, spelling in (("Nobody", "your"), ("Noor", "their"), ("Groq", "grok"), (None, None)):
        r = tw.answer_everyday_sound(word, spelling, True)
        assert not r["ok"] and r["error"]
    assert not (files / "vocab_sounds_kept.json").exists()


def test_a_kept_answer_goes_when_its_spelling_goes(files):
    tw.save_vocab(WORDS, SOUNDS)
    tw.answer_everyday_sound("Noor", "your", True)
    tw.save_vocab(WORDS, {"Groq": ["grok"]})                 # the spelling is taken off
    assert tw.load_sounds_kept(WORDS) == {}
    tw.save_vocab(WORDS, SOUNDS)                             # typed again later: asked again
    assert {"word": "Noor", "spelling": "your"} in tw.everyday_sounds_pending()


def test_an_unreadable_answers_file_is_no_answers(files):
    tw.save_vocab(WORDS, SOUNDS)
    (files / "vocab_sounds_kept.json").write_text("{not json", encoding="utf-8")
    assert tw.load_sounds_kept(WORDS) == {}
    assert tw.apply_vocab_changes("your plan", WORDS, SOUNDS)[0] == "your plan"
    (files / "vocab_sounds_kept.json").write_text(json.dumps(["your"]), encoding="utf-8")
    assert tw.load_sounds_kept(WORDS) == {}


def test_answers_given_in_the_call_are_used_instead_of_the_file(files):
    kept = {"Noor": ["your"]}
    assert tw.apply_vocab_changes("your plan", WORDS, SOUNDS, kept=kept)[0] == "Noor plan"
    assert tw.apply_vocab_changes("your plan", WORDS, SOUNDS, kept={})[0] == "your plan"


# ── the bridge ───────────────────────────────────────────────────────────────

def _lift(*names):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            out.append(ast.get_source_segment((ROOT / "app.py").read_text(encoding="utf-8"), node))
    return "import textwrap\n" + "\n\n".join(__import__("textwrap").dedent(s) for s in out)


class _Cache:
    def vocab_usage(self):
        return {"by_entry": {}, "recent": []}


def _api():
    logged = []
    ns = {"_history_cache": _Cache(), "_log_to_file": logged.append}
    exec(compile(_lift("get_vocab_book", "check_everyday_sounds", "answer_everyday_sound"),
                 "<app.py>", "exec"), ns)
    return ns, logged


def test_the_page_is_told_what_to_ask(files):
    tw.save_vocab(WORDS, SOUNDS)
    ns, _ = _api()
    book = ns["get_vocab_book"](None)
    assert book["ok"] and book["everyday"] == [
        {"word": "Noor", "spelling": "your"}, {"word": "Noor", "spelling": "our"}]


def test_the_page_can_ask_before_saving(files):
    ns, _ = _api()
    assert ns["check_everyday_sounds"](None, ["grok", "your", "waffle her", "Mark"]) == {
        "ok": True, "everyday": ["your", "Mark"]}
    assert ns["check_everyday_sounds"](None, None) == {"ok": True, "everyday": []}


def test_the_page_answers_through_the_bridge(files):
    tw.save_vocab(WORDS, SOUNDS)
    ns, logged = _api()
    r = ns["answer_everyday_sound"](None, "Noor", "your", True)
    assert r["ok"] and r["pending"] == [{"word": "Noor", "spelling": "our"}]
    r = ns["answer_everyday_sound"](None, "Noor", "our", False)
    assert r["ok"] and r["pending"] == [] and r["sounds_like"]["Noor"] == ["your"]
    # What was answered is logged without the words themselves.
    assert logged and all("your" not in line and "Noor" not in line for line in logged)


def test_the_answers_file_stays_with_the_list_when_the_data_is_cleared():
    import privacy_data
    assert "vocab_sounds_kept.json" in privacy_data.KEPT_FILES
    assert not set(privacy_data.KEPT_FILES) & set(privacy_data.DATA_FILES)
