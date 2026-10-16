"""Snippets on the Vocabulary page: what the window decides, and how it is wired.

The editor was deleted in 3.15.0 with the old pages, so nobody could create a
snippet. It is a section under the words on the Vocabulary page (no new
page), in the Atelier design: a form to add, rows to change or remove, an undo
line, an empty state that says what snippets are for, and the keyboard for all
of it. Its small decisions live in ui/logic.js and run here in Node; the rest
is read from the source, as the other window tests do.
"""
import json
import re
from pathlib import Path

import pytest

import snippets as S
from test_ui_logic import code_only, js, needs_node, read

ROOT = Path(__file__).resolve().parent.parent


def sn(*pairs):
    return [{"trigger": t, "expansion": e} for t, e in pairs]


def check(have, trigger, expansion, editing=-1):
    return js(f"L.snippetCheck({json.dumps(have)}, {json.dumps(trigger)}, {json.dumps(expansion)}, {editing})")


# ── the limits are the app's ─────────────────────────────────────────────────

@needs_node
def test_the_window_holds_the_same_limits_as_the_app():
    assert js("[L.SNIPPET_MAX_TRIGGER, L.SNIPPET_MAX_EXPANSION, L.SNIPPET_MAX_COUNT]") == [
        S.MAX_TRIGGER, S.MAX_EXPANSION, S.MAX_SNIPPETS]


@needs_node
def test_a_good_snippet_is_kept_tidy():
    assert check([], "  my   address ", "  12 Acacia Avenue\nLondon \n") == {
        "ok": True, "trigger": "my address", "expansion": "12 Acacia Avenue\nLondon"}


@pytest.mark.parametrize("trigger, expansion, field, error", [
    ("", "x", "trigger", "Type the phrase you'll say first."),
    ("   ", "x", "trigger", "Type the phrase you'll say first."),
    ("t" * 61, "x", "trigger", "Keep the phrase to 60 characters or fewer."),
    ("t", "", "text", "Type the text to paste."),
    ("t", "  \n ", "text", "Type the text to paste."),
    ("t", "e" * 4001, "text", "Keep the text to 4,000 characters or fewer."),
])
@needs_node
def test_what_is_wrong_is_said_in_a_sentence_with_the_box_to_go_to(trigger, expansion, field, error):
    got = check([], trigger, expansion)
    assert got == {"ok": False, "field": field, "error": error}
    for ch in ("—", "–"):
        assert ch not in got["error"]


@needs_node
def test_the_limits_are_met_exactly():
    assert check([], "t" * 60, "e" * 4000)["ok"] is True


@needs_node
def test_the_same_trigger_in_other_capitals_is_refused_and_names_the_one_saved():
    have = sn(("My address", "x"))
    assert check(have, "  my   ADDRESS ", "y") == {
        "ok": False, "field": "trigger", "error": '"My address" is already a snippet.'}
    # Changing the same row to another spelling of itself is allowed.
    assert check(have, "my address", "y", 0)["ok"] is True
    # ...but not onto another row's trigger.
    two = sn(("a", "x"), ("b", "y"))
    assert check(two, "B", "z", 0)["error"] == '"b" is already a snippet.'


@needs_node
def test_the_two_hundred_first_snippet_is_refused_when_adding_only():
    full = sn(*[(f"t{i}", "x") for i in range(200)])
    assert check(full, "one more", "x") == {
        "ok": False, "field": "trigger", "error": "You can keep up to 200 snippets."}
    assert check(full, "t5", "changed", 5)["ok"] is True


@pytest.mark.parametrize("have, trigger, expansion", [
    ([], "a", "b"), (sn(("a", "x")), "A", "b"), (sn(("é", "x")), "É", "b"),
    ([], " a  b ", "x"), ([], "t" * 60, "e" * 4000), ([], "t" * 61, "x"), ([], "x", "e" * 4001),
    ([], "", "x"), ([], "x", ""),
])
@needs_node
def test_the_window_and_the_app_agree_on_what_can_be_saved(have, trigger, expansion):
    """The page checks first so it can say why; the app checks again. They
    must not disagree about what is acceptable."""
    page_ok = check(have, trigger, expansion)["ok"]
    saved, error = S.clean(have + [{"trigger": trigger, "expansion": expansion}])
    app_adds_it = error is None and len(saved) == len(have) + 1
    assert page_ok == app_adds_it


@needs_node
def test_the_summary_and_the_counter_and_the_undo_line():
    assert js("[L.snippetSummary(0), L.snippetSummary(1), L.snippetSummary(2), L.snippetSummary(1200)]") == [
        "0 snippets", "1 snippet", "2 snippets", "1,200 snippets"]
    assert js("[L.snippetCounter(0), L.snippetCounter(3599), L.snippetCounter(3600), L.snippetCounter(4000)]") == [
        "", "", "3,600 of 4,000 characters", "4,000 of 4,000 characters"]
    assert js("[L.snippetDone('remove', ' my   address '), L.snippetDone('edit', 'sign off')]") == [
        'Removed "my address".', 'Changed "sign off".']
    assert js("L.snippetTrigger('  a \\n b\\t c ')") == "a b c"


# ── the section on the Vocabulary page ───────────────────────────────────────

def _section():
    html = read("index.html")
    return html[html.index('<section class="snips"'):html.index('<aside class="vside"')]


def test_the_section_is_on_the_vocabulary_page_below_the_words_and_adds_no_page():
    html = read("index.html")
    panel = html[html.index('id="vocabularyPanel"'):html.index('<!-- Update dialog')]
    assert panel.index('id="vocabList"') < panel.index('id="snippetsSection"') < panel.index('id="vocabSide"')
    nav = html[html.index('<nav class="topbar-nav"'):html.index("</nav>")]
    assert nav.count("<button") == 3, "no new page for snippets"
    assert "snippet" not in nav.lower()


def test_the_section_is_labelled_and_every_control_has_a_name():
    sec = _section()
    assert 'aria-labelledby="snipTitle"' in sec and '<h2 class="sh2" id="snipTitle">Snippets' in sec
    assert 'aria-label="When you say"' in sec and 'aria-label="Waffler pastes"' in sec
    assert 'onsubmit="addSnippet(); return false;"' in sec
    assert '<button type="submit" class="btn btn-pri vocab-add-btn">Add</button>' in sec
    # An error is announced; the undo line is a polite status, as on the words.
    assert re.search(r'id="snipError" role="alert" hidden', sec)
    assert re.search(r'id="snipUndo" role="status" aria-live="polite" hidden', sec)
    assert 'id="snipUndoBtn"' in sec and "link-btn" in sec
    # Nothing in it needs a pointer: no hover-only controls, no titles as the only name.
    assert "onmouseover" not in sec and "tabindex=\"-1\"" not in sec


def test_the_section_text_is_plain_sentence_case_with_no_em_dash():
    sec = _section()
    text = re.sub(r"<[^>]+>", " ", sec)
    assert "—" not in sec and "–" not in sec
    assert "Say a short phrase and Waffler pastes longer text in its place." in text
    for word in ("API", "regex", "trigger", "expansion"):
        assert word not in text, word
    # S10: "JSON" is a file format in the Export choice (CSV or JSON), and is
    # said nowhere else in the section.
    assert text.count("JSON") == 1 and '<option value="json">JSON</option>' in sec
    assert "Say only the phrase and you get exactly that text." in text


def test_the_empty_state_teaches_and_keeps_the_form():
    app = code_only(read("app.js"))
    render = app[app.index("function renderSnippets()"):app.index("async function _snipSave(")]
    assert "Say a short phrase, get longer text" in render
    assert "SNIPPET_EXAMPLES" in render and "${_vocabRows(SNIPPET_EXAMPLES)}" in render
    # The examples are made-up and said to be examples.
    assert "Examples." in render
    assert "—" not in render and "–" not in render


def test_the_rows_are_a_list_with_named_buttons_and_the_keyboard_does_the_rest():
    app = code_only(read("app.js"))
    render = app[app.index("function renderSnippets()"):app.index("async function _snipSave(")]
    assert 'role="list"' in render and "setAttribute('role', 'listitem')" in render
    assert 'aria-label="Change ${t}"' in render and 'aria-label="Remove ${t}"' in render
    assert "WI.icon('pencil')" in render and "WI.icon('x')" in render and "WI.icon('arrow')" in render
    # Esc cancels a change, Ctrl or Cmd with Enter saves it; Enter in the
    # phrase box submits the form.
    assert "e.key === 'Escape'" in render and "e.ctrlKey || e.metaKey" in render
    assert "edit.addEventListener('submit'" in render
    # The text box of the add form takes Ctrl or Cmd with Enter too.
    tail = app[app.index("async function deleteSnippet("):app.index("async function openPracticeEditor(")]
    assert "getElementById('snipText')" in tail and "e.ctrlKey || e.metaKey" in tail
    assert "addSnippet();" in tail


def test_each_change_saves_the_whole_list_and_a_removal_or_change_can_be_undone():
    app = code_only(read("app.js"))
    assert "pywebview.api.get_snippets()" in app and "pywebview.api.set_snippets(list)" in app
    save = app[app.index("async function _snipSave("):app.index("async function addSnippet(")]
    assert "res.snippets" in save and "(res && res.error)" in save
    # The page shows what the app kept, not what it sent.
    assert "_snips = Array.isArray(res.snippets) ? res.snippets : list;" in save
    for fn in ("deleteSnippet", "saveSnippetEdit"):
        body = app[app.index(f"async function {fn}("):]
        body = body[:body.index("\n}\n")]
        assert "_snipShowUndo(" in body and "_snipSave(before)" in body, fn
    assert "loadSnippets();" in app[app.index("async function loadVocabPage()"):app.index("function renderVocab()")]


def test_removing_a_snippet_keeps_focus_in_the_list():
    app = code_only(read("app.js"))
    body = app[app.index("async function deleteSnippet("):]
    body = body[:body.index("\n}\n")]
    assert "WL.focusAfterRemove(i, left.length)" in body
    assert "document.getElementById('snipTrigger')" in body       # the box when none are left


def test_an_edit_returns_focus_to_its_button_and_a_refused_one_to_the_box():
    app = code_only(read("app.js"))
    close = app[app.index("function closeSnippetEdit()"):app.index("async function saveSnippetEdit(")]
    assert ".sn-edit-btn" in close and ".focus()" in close
    save = app[app.index("async function saveSnippetEdit("):]
    save = save[:save.index("\n}\n")]
    assert "box.focus()" in save and "check.field === 'text'" in save


def test_it_never_asks_with_a_native_box_and_never_shows_an_exception():
    app = code_only(read("app.js"))
    block = app[app.index("let _snips = [];"):app.index("async function openPracticeEditor(")]
    assert "confirm(" not in block and "alert(" not in block
    assert "String(e)" not in block and "e.message" not in block and "'Error" not in block
    # A bridge failure says one sentence; the detail goes to the console.
    assert block.count("Couldn't save your snippets. Try again.") >= 2


def test_every_icon_it_uses_exists_and_it_loads_nothing_from_the_network():
    icons = read("icons.js")
    for name in ("pencil", "x", "arrow", "plus"):
        assert re.search(rf"\b{name}:", icons), name
    block = read("style.css")
    start = block.index("/* Snippets: under the words.")
    block = block[start:block.index("/* Narrower windows", start)]
    assert "url(" not in block and "@import" not in block and "http" not in block


def test_the_section_reflows_and_the_controls_are_not_hover_only():
    css = read("style.css")
    start = css.index("/* Snippets: under the words.")
    snips = css[start:css.index("/* Narrower windows", start)]
    # No opacity: 0 on the buttons (the words table hides its own until hover).
    assert "opacity: 0" not in snips
    assert "animation:" in snips and "infinite" not in snips
    narrow = css[css.index("@media (max-width: 640px) {"):]
    narrow = narrow[:narrow.index("\n}\n")]
    assert ".vadd, .snip-add { grid-template-columns: 1fr; }" in narrow
    assert ".sn-r { grid-template-columns: minmax(0, 1fr) auto;" in narrow


def test_the_privacy_page_says_snippets_stay_when_the_data_is_deleted():
    html = read("index.html")
    assert "Your keys, words, snippets and settings stay." in html
