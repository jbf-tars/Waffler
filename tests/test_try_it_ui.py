"""Settings, Transforms and Command mode: the top of each section, and "Try it
here".

The top of each section is the switch, one sentence, the shortcut as keycaps
with Change, and a box that runs the real feature on the text in it
(ui/tryit.js; Api.try_transform, Api.try_command). What is sent where is not
lost: it is under one collapsed "How it works" at the bottom. The page's small
decisions run in Node; the rest is read from the source, as the other window
tests do.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


def section(ident):
    html = read("index.html")
    start = html.index(f'id="{ident}"')
    return html[start:html.index("</section>", start)]


def words(text):
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    return [w for w in re.sub(r"<[^>]+>", " ", text).split() if re.search(r"[A-Za-z0-9]", w)]


SECTIONS = (("secTransforms", "transforms", "Polish it"), ("secCommand", "command", "Do it"))


# ── the top of the section ───────────────────────────────────────────────────

@pytest.mark.parametrize("ident,prefix,button", SECTIONS)
def test_the_top_is_the_switch_one_sentence_the_shortcut_and_the_box(ident, prefix, button):
    sec = section(ident)
    order = [sec.index(mark) for mark in (
        'class="ptitle"', f'class="pdesc" id="{prefix}Desc"', f'id="{prefix}Toggle"',
        f'id="{prefix}Caps"', f'id="{prefix}Change"', f'id="{prefix}Try"', 'class="how"')]
    assert order == sorted(order), "title, sentence, switch, shortcut, Try it here, then the rest"
    sentence = re.search(rf'<p class="pdesc" id="{prefix}Desc">([^<]+)</p>', sec).group(1)
    assert len(words(sentence)) <= 15 and sentence.count(".") == 1 and sentence.endswith(".")
    # The switch's row and the shortcut's row have no line of explanation.
    top = sec[sec.index('class="sheet top-sheet"'):sec.index(f'id="{prefix}Error"')]
    assert re.findall(r'<div class="row-d"[^>]*>([^<]+)</div>', top) == []
    assert f'<div class="row-d" id="{prefix}ShortcutDesc" hidden></div>' in top
    assert ">Change…</button>" in top and f'<span class="keys tx-caps" id="{prefix}Caps"' in top


@pytest.mark.parametrize("ident,prefix,button", SECTIONS)
def test_the_main_view_does_not_explain_how_it_works(ident, prefix, button):
    sec = section(ident)
    main = sec[sec.index("<h1"):sec.rindex("<div", 0, sec.index('class="how"'))]
    for said in ("It is off until you turn it on", "at any other time", "sent to the provider",
                 "does not reach the app", "Accessibility", "your speech provider"):
        assert said not in main, said
    # The words written into the page for the whole section, the hidden ones
    # included (it was 279 shown for Transforms and 255 for Command mode).
    assert len(words(main)) <= 110


@pytest.mark.parametrize("ident,prefix,button", SECTIONS)
def test_one_primary_action_and_it_is_the_try_button(ident, prefix, button):
    sec = section(ident)
    assert sec.count("btn-pri") == 1
    assert re.search(rf'<button type="button" class="btn btn-pri btn-sm" id="{prefix}TryBtn"[^>]*>{button}</button>', sec)


# ── How it works ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ident,prefix,button", SECTIONS)
def test_how_it_works_is_one_collapsed_disclosure_at_the_bottom(ident, prefix, button):
    sec = section(ident)
    assert sec.count('class="how"') == 1 and sec.count(">How it works<") == 1
    assert (f'<button type="button" class="link-btn how-btn" id="{prefix}HowBtn" aria-expanded="false" '
            f'aria-controls="{prefix}How" onclick="toggleHow(this)">') in sec
    assert f'<div class="sheet how-body" id="{prefix}How" hidden>' in sec
    after = sec[sec.index(f'id="{prefix}How"'):]
    assert "<h2" not in after and 'class="row"' not in after, "nothing comes after it"
    app = code_only(read("app.js"))
    fn = app[app.index("function toggleHow(btn)"):app.index("function toggleShortcutRules")]
    assert "btn.getAttribute('aria-controls')" in fn and "box.hidden = !open;" in fn
    assert "btn.setAttribute('aria-expanded', String(open));" in fn


def test_nothing_true_about_what_is_sent_was_lost():
    how = " ".join(re.sub(r"<[^>]+>", " ", section("secTransforms")[section("secTransforms").index('class="how"'):]).split())
    for said in (
        "It is off until you turn it on.",
        "the text you selected is read from the app in front, with Accessibility or by copying it, and sent to the "
        "provider you chose in Keys and providers",
        "Nothing is read or sent at any other time.",
        "Your app's own Undo takes it back, and the text before and after is kept in your Journal.",
        "The key of the shortcut does not reach the app.",
        'use "Transform selected text" in the menu bar',
        "never invents details",
        "You can keep up to 5.",
        "Try it here sends the text in the box to that same provider, only when you press Polish it.",
        "Nothing from it is kept in your Journal.",
    ):
        assert said in how, said
    how = " ".join(re.sub(r"<[^>]+>", " ", section("secCommand")[section("secCommand").index('class="how"'):]).split())
    for said in (
        "It is off until you turn it on.",
        "your recording is turned into text by your speech provider",
        "the text you selected (or, with nothing selected, up to 500 characters before your cursor)",
        "Nothing is recorded, read or sent at any other time.",
        "Your app's own Undo takes an edit back, and the Journal keeps what you said and the text before and after.",
        "Your cancel key stops a command.",
        "The key of the shortcut does not reach the app.",
        "Only your spoken question is sent to the engine, never the text you selected.",
        "Try it here sends the text in the box and what you typed to that same provider, only when you press Do it.",
        "a question only shows the search it would open",
    ):
        assert said in how, said


def test_the_off_notes_are_shown_only_while_the_feature_is_off():
    assert '<li id="transformsOffNote" hidden>' in section("secTransforms")
    assert '<li id="commandOffNote" hidden>' in section("secCommand")
    app = code_only(read("app.js"))
    assert "const off = byId('transformsOffNote');\n  if (off) off.hidden = v.enabled;" in app
    assert "const off = byId('commandOffNote');\n  if (off) off.hidden = v.enabled;" in app


# ── Try it here: the box ─────────────────────────────────────────────────────

def test_each_box_has_a_named_text_field_a_status_line_and_undo():
    tx, cm = section("secTransforms"), section("secCommand")
    for sec, prefix in ((tx, "transforms"), (cm, "command")):
        assert f'<h2 class="sh2"><label for="{prefix}TryText">Try it here</label></h2>' in sec
        assert f'<textarea id="{prefix}TryText" class="input try-text"' in sec
        assert f'<span class="try-said" id="{prefix}TrySaid" role="status" aria-live="polite"></span>' in sec
        assert f'<button type="button" class="link-btn" id="{prefix}TryUndo" hidden>Undo</button>' in sec
        # With no key: one short line and a link to Keys and providers.
        needs = sec[sec.index(f'id="{prefix}TryNeeds"'):]
        needs = needs[:needs.index("</p>")]
        assert "hidden" in needs[:40] and "showSettingsSection('keys')" in needs and ">Keys and providers</button>" in needs
    assert '<label for="commandTryAsk">What should it do?</label>' in cm
    assert '<input type="text" id="commandTryAsk" class="input"' in cm
    assert "commandTryAsk" not in tx


@needs_node
def test_the_box_starts_with_a_short_ramble_and_the_instruction():
    sample, ask = js("L.TRY_SAMPLE"), js("L.TRY_ASK")
    assert ask == "make this shorter"
    assert 15 <= len(sample.split()) <= 40 and sample == sample.lower() and "um" in sample.split()
    assert "." not in sample and "," not in sample, "a ramble: no punctuation yet"
    tryit = code_only(read("tryit.js"))
    assert "text.value = WL.TRY_SAMPLE;" in tryit and "ask.value = WL.TRY_ASK;" in tryit


@needs_node
def test_with_no_key_the_button_is_off_and_one_short_line_says_what_is_needed():
    assert js("L.tryItNeeds({ok: true, ready: true, needs: ''})") == {"ready": True, "line": ""}
    for status in ("{ok: true, ready: false, needs: 'key'}", "null", "undefined", "{}", "{ok: false, ready: true}",
                   "{ready: 'yes'}"):
        view = js(f"L.tryItNeeds({status})")
        assert view == {"ready": False, "line": "Add a key to try this."}, status
    tryit = code_only(read("tryit.js"))
    assert "btn.disabled = busy || !ready;" in tryit
    assert "if (needs) needs.hidden = view.ready;" in tryit
    assert "await p.try_it_status()" in tryit


@needs_node
def test_a_rewrite_goes_into_the_box_with_undo():
    r = js("L.tryItResult('transform', {ok: true, changed: true, text: 'Tidy.'})")
    assert r == {"text": "Tidy.", "said": "Polished.", "error": False, "undo": True, "link": None}
    r = js("L.tryItResult('command', {ok: true, action: 'edit', changed: true, text: 'Short.'})")
    assert r == {"text": "Short.", "said": "Done.", "error": False, "undo": True, "link": None}
    r = js("L.tryItResult('command', {ok: true, action: 'draft', changed: true, text: 'New.'})")
    assert r["text"] == "New." and r["said"] == "Written." and r["undo"] is True


@needs_node
def test_text_that_needs_nothing_leaves_the_box_alone_and_says_the_apps_words():
    r = js("L.tryItResult('transform', {ok: true, changed: false, text: 'Same.', message: 'Your text already looks good.'})")
    assert r == {"text": None, "said": "Your text already looks good.", "error": False, "undo": False, "link": None}
    assert js("L.tryItResult('command', {ok: true, action: 'edit', changed: false})")["said"] == "Nothing to change."


@needs_node
def test_a_question_shows_the_search_as_a_link_and_changes_nothing():
    r = js("L.tryItResult('command', {ok: true, action: 'ask', query: 'capital of France', engine: 'Google', "
           "url: 'https://www.google.com/search?q=capital+of+France'})")
    assert r == {"text": None, "said": "It would search Google for", "error": False, "undo": False,
                 "link": {"label": "capital of France", "query": "capital of France"}}
    # The address the app sent is never used by the page: only the words go back.
    assert "url" not in json.dumps(r)
    assert js("L.tryItResult('command', {ok: true, action: 'ask', query: ''})")["error"] is True


@needs_node
def test_a_failure_is_the_apps_sentence_or_a_plain_one():
    r = js("L.tryItResult('transform', {ok: false, error: 'Add a key first, in Keys and providers.'})")
    assert r == {"text": None, "said": "Add a key first, in Keys and providers.", "error": True, "undo": False, "link": None}
    for odd in ("null", "undefined", "{}", "{ok: 'yes', text: 'x'}", "{ok: false, error: 5}"):
        assert js(f"L.tryItResult('transform', {odd})")["said"] == "Couldn't polish that. Try again.", odd
        assert js(f"L.tryItResult('command', {odd})")["said"] == "Couldn't run that. Try again.", odd
    assert js("L.tryItResult('transform', {ok: true, changed: true, text: 5})")["text"] is None


# ── Try it here: the wiring ──────────────────────────────────────────────────

def test_the_page_loads_the_script_and_asks_when_a_section_opens():
    html = read("index.html")
    assert '<script src="tryit.js"></script>' in html
    assert html.index('<script src="logic.js"></script>') < html.index('<script src="tryit.js"></script>')
    app = code_only(read("app.js"))
    show = app[app.index("function showSettingsSection(sec)"):app.index("function _syncMicMeter")]
    assert "if (window.tryItRefresh) window.tryItRefresh();" in show


def test_the_buttons_call_the_apps_real_paths():
    tryit = code_only(read("tryit.js"))
    assert "await p.try_command(before, $(box.ask).value) : await p.try_transform(before)" in tryit
    assert "window.tryItRefresh = refresh;" in tryit
    # The result is the app's, read by the logic file.
    assert "const view = WL.tryItResult(kind, r);" in tryit
    # One run at a time, and never without a key.
    assert "if (!text || st.busy || !ready || !p) return;" in tryit
    # A call that throws is a failure the page words itself.
    assert "} catch (e) {\n      r = null;\n    }" in tryit


def test_undo_puts_back_what_was_in_the_box():
    tryit = code_only(read("tryit.js"))
    assert "st.before = before;\n      text.value = view.text;" in tryit
    fn = tryit[tryit.index("function undo(kind)"):tryit.index("function wire()")]
    assert "text.value = st.before;" in fn and "st.before = null;" in fn
    # Typing after a result leaves nothing for Undo to put back.
    assert "text.addEventListener('input', () => { state[kind].before = null; if (back) back.hidden = true; });" in tryit


def test_a_search_opens_only_when_its_link_is_clicked_and_the_page_sends_only_the_words():
    tryit = code_only(read("tryit.js"))
    assert tryit.count("open_command_search") == 2          # asked for, then called
    click = tryit[tryit.index("a.addEventListener('click'"):tryit.index("said.append(' ', a);")]
    assert "e.preventDefault();" in click and "p.open_command_search(view.link.query)" in click
    assert "a.href = '#';" in tryit and "a.textContent = view.link.label;" in tryit
    assert "window.open" not in tryit and "location" not in tryit and ".url" not in tryit
    # Nothing the app sends reaches the page as markup.
    assert "innerHTML" not in tryit


def test_the_try_css_uses_tokens_only():
    css = read("style.css")
    block = css[css.index('/* A small "Beta" tag'):css.index("/* Settings, Command mode: the phrases to try.")]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block), "colours come from tokens"
    assert "animation" not in block and "transition" not in block
    for rule in (".beta-tag {", ".try-text {", ".try-foot {", ".try-said.is-err {", ".how-btn {", ".how-body[hidden]"):
        assert rule in block, rule
