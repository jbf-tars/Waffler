"""The Vocabulary page's question about an everyday word used as a spelling
(tests/test_vocab_everyday_sounds.py has the rule and the bridge): what it
says (ui/logic.js, run in Node) and how the page is wired (index.html,
app.js, style.css read as text)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")

APP = (UI / "app.js").read_text(encoding="utf-8")
HTML = (UI / "index.html").read_text(encoding="utf-8")
CSS = (UI / "style.css").read_text(encoding="utf-8")


def js(expr):
    code = (f"const L = require({json.dumps(str(UI / 'logic.js'))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def func(name):
    start = APP.index(f"function {name}(")
    body = APP[start:]
    return body[:body.index("\n}\n")]


# ── what it says ─────────────────────────────────────────────────────────────

@needs_node
def test_before_saving_it_says_what_would_happen_in_plain_words():
    assert js('L.vocabEverydayAsk("Noor", ["your"])') == {
        "title": '"your" is an everyday word.',
        "detail": 'If you use it, every "your" you dictate becomes Noor, in any sentence.',
        "use": "Use it anyway", "cancel": "Cancel"}
    many = js('L.vocabEverydayAsk("Noor", ["your", "our", "hour"])')
    assert many["title"] == '"your", "our" and "hour" are everyday words.'
    assert many["use"] == "Use them anyway" and "If you use them, every" in many["detail"]


@needs_node
def test_nothing_everyday_is_nothing_to_ask():
    assert js('L.vocabEverydayAsk("Noor", [])') is None
    assert js('L.vocabEverydayAsk("Noor", null)') is None
    assert js('L.vocabEverydayNotice({})') is None
    assert js('L.vocabEverydayNotice(null)') is None


@needs_node
def test_a_saved_one_says_what_happens_meanwhile_and_names_both_buttons():
    t = js('L.vocabEverydayNotice({word: "Noor", spelling: "your"})')
    assert t["title"] == '"your" for Noor is an everyday word.'
    assert "only where it is written like a name" in t["detail"]
    assert 'Keep it to change every "your" you dictate, or remove it.' in t["detail"]
    assert (t["keep"], t["remove"]) == ("Keep", "Remove")
    assert t["keepLabel"] == 'Keep "your" as a spelling of Noor'
    assert t["removeLabel"] == 'Remove "your" from Noor'


@needs_node
def test_only_the_spellings_typed_now_are_asked_about():
    assert js('L.vocabSoundsFresh(["grok", "Your"], ["grok", "your", "our"])') == ["our"]
    assert js('L.vocabSoundsFresh(null, ["our"])') == ["our"]
    assert js('L.vocabSoundsFresh(["our"], null)') == []


@needs_node
def test_no_dash_in_any_of_it():
    text = json.dumps([js('L.vocabEverydayAsk("Noor", ["your", "our"])'),
                       js('L.vocabEverydayNotice({word: "Noor", spelling: "your"})')], ensure_ascii=False)
    assert "—" not in text and "–" not in text


# ── how the page is wired ────────────────────────────────────────────────────

def test_the_box_is_a_named_group_that_is_announced_and_starts_hidden():
    assert re.search(r'<div class="v-everyday" id="vocabEveryday" role="group" '
                     r'aria-label="Everyday words used as spellings" aria-live="polite" hidden></div>', HTML)
    assert ".v-everyday[hidden] { display: none; }" in CSS
    block = CSS[CSS.index(".v-everyday {"):CSS.index(".v-undo {")]
    assert not re.search(r"#[0-9A-Fa-f]{3,6}\b", block), "a hard-coded colour"


def test_adding_and_editing_both_ask_before_the_save():
    for name in ("addVocabWord", "saveVocabSounds"):
        body = func(name)
        ask = body.index("await _vocabAskEveryday(")
        assert "if (asked.length) return;" in body[ask:]
        saves = [m.start() for m in re.finditer(r"set_vocab\(|_vocabSave\(entries\)", body)]
        assert saves and all(ask < at for at in saves), name
        # Once the person said so, each one is kept, so it is not asked again.
        assert "_vocabKeepEveryday(" in body


def test_the_question_is_put_only_when_the_app_calls_a_spelling_everyday():
    body = func("_vocabAskEveryday")
    assert "if (confirmed) return [];" in body
    assert "await _vocabEverydayOf(fresh)" in body and "if (!everyday.length) return [];" in body
    assert "check_everyday_sounds" in func("_vocabEverydayOf")


def test_cancel_saves_nothing_and_use_it_anyway_runs_the_save():
    body = func("renderVocabEveryday")
    use = body[body.index('[data-act="use"]'):body.index('[data-act="cancel"]')]
    assert "if (go) go();" in use
    cancel = body[body.index('[data-act="cancel"]'):body.index("box.querySelectorAll('.ve-r[data-i]')")]
    assert "go(" not in cancel and "set_vocab" not in cancel and "_vocabEverydayAsk = null;" in cancel
    # Focus lands on Cancel: Enter or Space never confirms by accident.
    assert "'#vocabEveryday .ve-ask [data-act=\"cancel\"]'" in func("_vocabAskEveryday")


def test_keep_and_remove_go_through_the_bridge_and_refresh_the_page():
    body = func("answerVocabEveryday")
    assert "pywebview.api.answer_everyday_sound(word, spelling, keep)" in body
    assert "_vocabEveryday = Array.isArray(res.pending) ? res.pending : [];" in body
    assert "renderVocab();" in body and "_vocabRunTry();" in body
    render = func("renderVocabEveryday")
    assert "answerVocabEveryday(item.word, item.spelling, true)" in render
    assert "answerVocabEveryday(item.word, item.spelling, false)" in render


def test_every_button_has_a_type_and_what_is_shown_is_escaped():
    render = func("renderVocabEveryday")
    for tag in re.findall(r"<button\b[^>]*>", render):
        assert 'type="button"' in tag, tag
    assert "${t." not in render.replace("${escHtml(t.", "")
    assert 'aria-label="${escHtml(t.keepLabel)}"' in render and 'aria-label="${escHtml(t.removeLabel)}"' in render


def test_the_page_takes_the_unanswered_ones_from_the_app_on_load_and_after_every_save():
    assert "_vocabEveryday = Array.isArray(book.everyday) ? book.everyday : [];" in func("_vocabReloadBook")
    assert "if (res && Array.isArray(res.everyday)) _vocabEveryday = res.everyday;" in func("_vocabTakeSaved")
    assert func("renderVocab").count("renderVocabEveryday();") == 1
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    set_vocab = app[app.index("    def set_vocab("):app.index("    def get_vocab_book(")]
    assert 'result["everyday"] = everyday_sounds_pending(' in set_vocab
