"""The front-end polish pass (after 3.16.7).

What an audit of every page found, in a harness with a pretend bridge and
invented data: a Journal that slowed as it grew, a dialog whose Enter did the
wrong button's job, Mac words on Windows, text in right-to-left languages
laid out left to right, a Copy that said Copied when it had not, and smaller
things. These tests read ui/ files and run ui/logic.js in Node where it is
installed. No network, no keys, no private data.
"""
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


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def js(expr):
    code = (f"const L = require({json.dumps(str(UI / 'logic.js'))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def func(src, name):
    body = src[src.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


def css_rule(css, selector):
    """The declarations of the first rule whose selector is exactly this."""
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def visible_text(html):
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


# ── The Journal stays quick as it grows ──────────────────────────────────────

def test_the_feed_is_a_block_so_one_entry_does_not_measure_them_all():
    """As a flex column every entry was measured again whenever one was added
    or changed height: with 5,000 loaded, adding a page of older entries and
    opening "What you said" each took about 0.5 s; as a block, about 60 ms
    and 50 ms (measured in the audit harness, where the change of display
    alone left the feed's height the same to the pixel)."""
    css = read("style.css")
    assert "display: block" in css_rule(css, ".j-feed")
    assert "flex" not in css_rule(css, ".j-feed")


# ── Search shows where it matched ────────────────────────────────────────────

@needs_node
def test_search_marks_every_match_in_any_case():
    segs = js("L.searchMarks([{text: 'Send it to Sendhil, then send more', mark: false}], 'send')")
    assert [(s["text"], s["hit"]) for s in segs] == [
        ("Send", True), (" it to ", False), ("Send", True), ("hil, then ", False), ("send", True), (" more", False)]
    assert all(s["mark"] is False for s in segs)


@needs_node
def test_search_marks_keep_the_vocabulary_marks():
    segs = js("L.searchMarks([{text: 'Ask ', mark: false}, {text: 'Siobhán', mark: true}, {text: ' today', mark: false}], 'SIOB')")
    assert [(s["text"], s["mark"], s["hit"]) for s in segs] == [
        ("Ask ", False, False), ("Siob", True, True), ("hán", True, False), (" today", False, False)]


@needs_node
def test_no_search_marks_nothing_and_odd_input_is_safe():
    one = [{"text": "a.b (c)", "mark": False}]
    assert js(f"L.searchMarks({json.dumps(one)}, '')") == [{"text": "a.b (c)", "mark": False, "hit": False}]
    assert js(f"L.searchMarks({json.dumps(one)}, '   ')") == [{"text": "a.b (c)", "mark": False, "hit": False}]
    # The search is plain text, never a pattern.
    assert [s["text"] for s in js(f"L.searchMarks({json.dumps(one)}, '.b (')") if s["hit"]] == [".b ("]
    assert js("L.searchMarks(null, 'x')") == []


def test_the_journal_draws_the_marks_and_drops_them_when_the_search_clears():
    app = read("app.js")
    marked = func(app, "_markedHtml")
    assert "WL.searchMarks(" in marked and "_searchText" in marked
    assert '<mark class="search-hl">' in marked
    # drawn from the app's text through escHtml, never as HTML
    assert "escHtml(sg.text)" in marked
    assert ".search-hl" in read("style.css")


@needs_node
def test_find_focuses_the_search_with_the_platforms_own_key():
    assert js("L.isFindKey({key: 'f', metaKey: true}, true)") is True
    assert js("L.isFindKey({key: 'F', ctrlKey: true}, false)") is True
    assert js("L.isFindKey({key: 'f', ctrlKey: true}, true)") is False      # Ctrl+F on a Mac moves the cursor
    assert js("L.isFindKey({key: 'f', metaKey: true}, false)") is False
    assert js("L.isFindKey({key: 'f', metaKey: true, shiftKey: true}, true)") is False
    assert js("L.isFindKey({key: 'f', metaKey: true, altKey: true}, true)") is False
    assert js("L.isFindKey({key: 'g', metaKey: true}, true)") is False
    assert js("L.isFindKey(null, true)") is False


def test_find_goes_to_the_search_of_the_page_that_is_open():
    app = read("app.js")
    body = func(app, "_onFindKey")
    assert "WL.isFindKey(e, isMacPlatform)" in body
    assert "searchInput" in body and "vocabSearch" in body
    # Not while a dialog or setup has the window.
    assert "_openModalOverlay()" in body and "_wizardVisible()" in body


# ── Dialogs ──────────────────────────────────────────────────────────────────

@needs_node
def test_enter_in_a_dialog_belongs_to_the_button_that_has_focus():
    """Restart Waffler: Enter on Later restarted the app."""
    assert js("L.enterRunsDefault('BUTTON')") is False
    assert js("L.enterRunsDefault('button')") is False
    assert js("L.enterRunsDefault('A')") is False
    assert js("L.enterRunsDefault('TEXTAREA')") is False
    assert js("L.enterRunsDefault('SELECT')") is False
    assert js("L.enterRunsDefault('DIV')") is True
    assert js("L.enterRunsDefault('INPUT')") is True
    assert js("L.enterRunsDefault('')") is True and js("L.enterRunsDefault(null)") is True


def test_the_restart_dialog_asks_before_enter_restarts():
    banner = func(read("app.js"), "showRestartBanner")
    assert "WL.enterRunsDefault(e.target && e.target.tagName)" in banner
    # One restart, however many times the button or Enter is pressed.
    assert "if (_restarting) return;" in banner


def test_checking_for_updates_can_be_tried_again_from_the_dialog():
    app = read("app.js")
    body = func(app, "checkForUpdatesManual")
    assert "primaryLabel: 'Try again'" in body and "primaryHandler: checkForUpdatesManual" in body


# ── Feedback that is true ────────────────────────────────────────────────────

@needs_node
def test_copy_only_says_copied_when_it_was():
    assert js("L.copyWorked(true)") is True
    assert js("L.copyWorked(null)") is True and js("L.copyWorked(undefined)") is True   # older bridge: no answer
    assert js("L.copyWorked({ok: true})") is True
    assert js("L.copyWorked(false)") is False
    assert js("L.copyWorked({ok: false})") is False


def test_a_copy_that_failed_says_so():
    body = func(read("app.js"), "copyItem")
    assert "WL.copyWorked(" in body
    assert body.index("WL.copyWorked(") < body.index("'copied'")
    assert "Couldn't copy that. Try again." in body


def test_saving_a_key_cannot_be_sent_twice_and_shows_it_is_working():
    app = read("app.js")
    for name in ("saveGroqKey", "saveCerebrasKey", "saveApiKey"):
        body = func(app, name)
        assert "_keySaveBusy(" in body, name
    busy = func(app, "_keySaveBusy")
    assert "disabled" in busy and "aria-busy" in busy
    for call in ("saveGroqKey(this)", "saveCerebrasKey(this)", "saveApiKey(this)"):
        assert f'onclick="{call}"' in read("index.html"), call


def test_deleting_everything_cannot_be_sent_twice():
    app = read("app.js")
    body = func(app, "factoryReset")
    assert "btn.disabled = true" in body and "btn.disabled = false" in body
    assert 'onclick="factoryReset(this)"' in read("index.html")


# ── Words for the computer you are on ────────────────────────────────────────

@needs_node
def test_windows_has_a_tray_not_a_menu_bar():
    mac, win = js("L.platformText(true)"), js("L.platformText(false)")
    assert mac["menu"] == "menu bar" and win["menu"] == "tray"
    for key in ("transformsRun", "transformsOff"):
        assert "menu bar item" in mac[key] and "tray item" in win[key], key
        assert "menu bar" not in win[key]
    assert "in the menu bar" in js("L.transformShortcutLine({hotkey: []}, true)")
    assert "in the tray" in js("L.transformShortcutLine({hotkey: []}, false)")
    assert "menu bar" not in js("L.transformShortcutLine({hotkey: []}, false)")
    news_mac, news_win = js("L.whatsNew('3.16.0', true)"), js("L.whatsNew('3.16.0', false)")
    assert news_mac["items"][3].endswith("from Settings or the menu bar.")
    assert news_win["items"][3].endswith("from Settings or the tray.")
    assert news_mac["items"][1:3] == news_win["items"][1:3]
    # Without a platform it reads as the Mac's.
    assert js("L.whatsNew('3.16.0')") == news_mac


@needs_node
def test_whats_new_says_what_is_true_on_each_computer():
    """It said "Paste last (Mac)". Paste last is on Windows too (the tray and
    a shortcut); keeping your clipboard is the Mac's."""
    mac, win = js("L.whatsNew('3.16.0', true)")["items"][0], js("L.whatsNew('3.16.0', false)")["items"][0]
    assert mac == "Paste that keeps your clipboard, and Paste last."
    assert win == "Paste last, from the tray or a shortcut of your own."
    assert "(Mac)" not in mac + win and "clipboard" not in win
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    tray = app[app.index("menu = pystray.Menu("):]
    assert "_paste_last_async()" in tray[:1500]          # the Windows tray pastes last


@needs_node
def test_windows_does_not_speak_of_accessibility():
    """Accessibility is a Mac permission; on Windows the selection is read by
    copying it (src/selection_copy.py)."""
    mac, win = js("L.platformText(true)"), js("L.platformText(false)")
    for key in ("transformsDesc", "privacyTransforms", "privacySelected"):
        assert "Accessibility" in mac[key], key
        assert "Accessibility" not in win[key] and "by copying it" in win[key], key
        assert "—" not in win[key] and "  " not in win[key]


def test_the_page_holds_the_mac_sentences_and_windows_swaps_them():
    """index.html carries the Mac wording (what its own tests read); on
    Windows the same elements are filled from logic.js platformText."""
    html = visible_text(read("index.html"))
    if NODE:
        mac = js("L.platformText(true)")
        for key in ("transformsDesc", "transformsRun", "transformsOff", "privacyTransforms", "privacySelected"):
            assert mac[key] in html, key
    app = read("app.js")
    body = func(app, "_applyPlatformText")
    for el in ("transformsDesc", "transformsRunDesc", "transformsOffNote", "privTransformsDesc", "privSelectedDesc"):
        assert f"'{el}'" in body and f'id="{el}"' in read("index.html"), el
    assert "if (isMacPlatform) return;" in body
    assert "WL.transformShortcutLine(v, isMacPlatform)" in app
    assert "WL.whatsNew(ver, isMacPlatform)" in app


# ── One name for the Journal ─────────────────────────────────────────────────

def test_the_window_calls_it_the_journal_not_history():
    html = visible_text(read("index.html"))
    for old in ("Export history", "Delete your history", "from your own history", "keys and history"):
        assert old not in html, old
    for new in ("Export the Journal", "Delete your Journal, usage, recordings and logs?",
                "Counted on this computer, from your own Journal.", "Your keys and Journal stay as they are."):
        assert new in html, new
    row = read("index.html")
    row = row[row.index('id="historyRow"'):]
    assert re.search(r'<label for="historyKeep">Journal</label>', row[:600])
    assert "Deleted your Journal, usage, recordings and logs." in read("app.js")
    assert "Deleted your history" not in read("app.js")


# ── Right-to-left languages ──────────────────────────────────────────────────

def test_dictated_text_finds_its_own_direction():
    """Arabic and Hebrew were laid out left to right, with the full stop at
    the wrong end. dir="auto" lets each text (and each paragraph) choose."""
    app = read("app.js")
    assert '<div class="card-text styled" id="${textId}" dir="auto">' in app
    marked = func(app, "_markedHtml")
    assert '<p dir="auto">' in marked
    assert 'class="input j-edit-ta" dir="auto"' in app
    # the before and after of a rewrite or a command, too
    assert app.count('class="card-text styled tx-text') == app.count('class="card-text styled tx-text tx-before" dir="auto"') \
        + app.count('class="card-text styled tx-text tx-after" dir="auto"')


# ── Small windows ────────────────────────────────────────────────────────────

def test_the_stats_row_is_short_in_a_small_window():
    css = read("style.css")
    block = css[css.index("/* Narrower windows: the margin becomes a row above the entries. */"):]
    block = block[:block.index("/* ── First run")]
    assert ".j-weekline:not([hidden]) { display: none; }" in block
    assert re.search(r"\.j-rail \{ align-items: center; padding: 12px 24px; \}", block)
    assert re.search(r"\.j-spark \{ height: 30px; \}", block)


def test_the_settings_colophon_keeps_to_two_lines_in_the_narrow_menu():
    css = read("style.css")
    block = css[css.index("@media (max-width: 960px) {"):]
    block = block[:block.index("\n}\n")]
    assert ".snav-colophon" in block and "white-space: nowrap" in block


def test_a_text_button_is_tall_enough_to_aim_at():
    css = read("style.css")
    assert ".link-btn::after" in css
    assert "inset: -4px -2px" in css_rule(css, ".link-btn::after")
    assert "position: relative" in css_rule(css, ".link-btn")


def test_the_vocabulary_mark_stays_beside_its_sentence():
    """At the default window size "Vocabulary changed ..." was too long for
    the line, so it wrapped whole and left its honey mark alone above it."""
    css = read("style.css")
    rule = css_rule(css, ".j-meta.card-vocab, .j-meta.card-edited")
    assert "flex-wrap: nowrap" in rule and "align-items: flex-start" in rule
    assert "min-width: 0" in css_rule(css, ".j-meta.card-vocab > span:last-child, .j-meta.card-edited > span")


# ── The second round ─────────────────────────────────────────────────────────

def test_setup_uses_an_invented_name():
    for name in ("index.html", "app.js", "logic.js"):
        assert "James" not in read(name), name
    html = visible_text(read("index.html"))
    assert "Send it to John, sorry, Alex, by Wednesday at three." in html
    assert html.count("Send it to Alex by Wednesday at three.") == 2
    assert "Cheers, Alex" in html


def test_a_list_that_did_not_load_can_be_asked_for_again():
    html, app = read("index.html"), read("app.js")
    assert 'id="micRetry" hidden onclick="retryMicrophones(this)">Try again</button>' in html
    assert 'id="shortcutsRetry" hidden onclick="retryShortcuts(this)">Try again</button>' in html
    assert "retry.hidden = !status.failed;" in func(app, "renderMicrophones")
    assert "await loadMicrophones(true)" in func(app, "retryMicrophones")
    assert "await loadShortcuts()" in func(app, "retryShortcuts")
    load = func(app, "loadShortcuts")
    assert "retry.hidden = !!(r && r.ok) || !!_shortcuts;" in load
    # hidden really hides them (a .btn is inline-flex)
    assert ".s-retry[hidden], #micRetry[hidden] { display: none; }" in read("style.css")


@needs_node
def test_only_a_failed_microphone_list_offers_try_again():
    assert js("L.micStatus(null).failed") is True
    assert js("L.micStatus({ok: false}).failed") is True
    assert js("!!L.micStatus({ok: true, ranking: []}).failed") is False


@needs_node
def test_the_search_count_is_short_and_honest():
    assert js("L.searchCount('kestrel', 12, true)") == "12 entries"
    assert js("L.searchCount('kestrel', 1, true)") == "1 entry"
    assert js("L.searchCount('the', 50, false)") == "50+ entries"      # more pages to come
    assert js("L.searchCount('the', 1250, true)") == "1,250 entries"
    assert js("L.searchCount('zzz', 0, true)") == ""                   # the page says No entries match
    assert js("L.searchCount('', 50, false)") == "" and js("L.searchCount('  ', 5, true)") == ""


def test_the_count_and_our_own_clear_button_are_on_the_page():
    html, app, css = read("index.html"), read("app.js"), read("style.css")
    assert '<span class="j-search-n" id="searchCount" aria-hidden="true"></span>' in html
    for bid, call in (("searchClear", "clearSearch()"), ("vocabSearchClear", "clearVocabSearch()")):
        assert f'class="rbtn j-search-clear" id="{bid}" aria-label="Clear the search" hidden onclick="{call}"' in html
    # the browser's own is put away
    assert "::-webkit-search-cancel-button" in css and ".j-search-clear[hidden] { display: none; }" in css
    assert "_syncSearchClear('searchClear', q);" in func(app, "onSearchInput")
    assert "_syncSearchClear('searchClear', '');" in func(app, "clearSearch")
    assert "_syncSearchClear('vocabSearchClear', value);" in func(app, "onVocabSearch")
    assert "_syncSearchClear('vocabSearchClear', '');" in func(app, "clearVocabSearch")
    assert "WL.searchCount(_searchText, history.length, _histDone)" in func(app, "_updateSearchCount")
    assert "_updateSearchCount();" in func(app, "renderFeed") and "_updateSearchCount();" in func(app, "loadMoreEntries")


def test_the_word_has_the_widest_column_in_the_vocabulary():
    css = read("style.css")
    cols = "grid-template-columns: minmax(0, 1.9fr) minmax(0, 1.25fr) 96px 88px 60px;"
    assert cols in css_rule(css, ".vt-h, .vt-r")
    assert cols in css_rule(css, ".vt-r.vt-edit")        # a row keeps its columns while it is edited


def test_right_to_left_entries_start_at_the_same_left_edge():
    css = read("style.css")
    assert "text-align: left" in css_rule(css, ".card-text")
    assert "text-align: left" in css_rule(css, ".card-text p")


def test_the_window_says_dictation_where_it_said_take():
    for name in ("index.html", "app.js"):
        text = code_free(read(name))
        assert not re.search(r"\btakes? (can|from)\b|this take|a take\b", text), name
    app = read("app.js")
    assert "<span>Redo this dictation</span>" in app and "Undo redoing this dictation" in app
    assert "so a dictation can't be redone or recovered" in app
    msgs = (ROOT / "src" / "user_messages.py").read_text(encoding="utf-8")
    assert "the longest one dictation can be" in msgs


def code_free(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)
