"""Accessibility fixes from the 3.15 review.

Setup is a modal dialog that moves focus as its steps and states change and
announces the practice; dialogs take, trap and return focus; the hotkey
dialog announces the keys; the Journal has headings, labelled buttons and a
search count; controls have 3:1 edges; and the window reflows at 200% zoom.
These tests read ui/ files (and run ui/logic.js in Node where it is
installed). The behaviour itself was checked in the audit harness.
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


# ── Setup ────────────────────────────────────────────────────────────────────

def test_setup_is_a_modal_dialog_named_by_its_step_title():
    html = read("index.html")
    assert re.search(r'id="wizardOverlay" role="dialog" aria-modal="true" aria-labelledby="obTitleConnect"', html)
    for step_title in ("obTitleConnect", "obTitleConnected", "obTitlePermissions", "obTitleTry", "obTitleAnywhere"):
        assert re.search(rf'<h1 class="ob-title" id="{step_title}" tabindex="-1"', html), step_title
    app = read("app.js")
    assert "overlay.setAttribute('aria-labelledby', h.id)" in func(app, "_wizLabelDialog")


def test_the_top_bar_is_inert_while_setup_covers_it():
    app = read("app.js")
    assert "_setTopbarInert(true);" in func(app, "showWizard")
    hide = app[app.index("function hideWizard("):app.index("function _setTopbarInert(")]
    assert "_setTopbarInert(false);" in hide
    inert = func(app, "_setTopbarInert")
    assert "bar.inert = !!on;" in inert and "aria-hidden" in inert


def test_setup_moves_focus_to_the_new_step_and_to_what_a_state_shows():
    app = read("app.js")
    show = func(app, "wizShowStep")
    assert "if (step !== prev && _wizardVisible()) _wizFocus(_wizVisibleTitle(sec));" in show
    state = func(app, "wizSetState")
    assert "_wizRehomeFocus(sec, state);" in state
    rehome = func(app, "_wizRehomeFocus")
    # Only when focus was lost, so typing a key is never interrupted.
    assert "!_wizFocusLost()" in rehome
    lost = func(app, "_wizFocusLost")
    assert "a.disabled" in lost and "closest('[hidden]')" in lost
    # Finishing setup lands on the Journal, not on <body>.
    assert '<h1 class="sr-only" id="journalTitle" tabindex="-1">Journal</h1>' in read("index.html")
    hide = app[app.index("function hideWizard("):app.index("function _setTopbarInert(")]
    assert "getElementById('journalTitle')" in hide


def test_focusables_ignore_svg_icons():
    """<use href> inside every icon is not a control."""
    app = read("app.js")
    sel = func(app, "_focusables")
    assert "'button, a[href], input, select, textarea" in sel
    assert "'button, [href]" not in sel


def test_the_try_step_announces_progress_and_problems():
    html = read("index.html")
    assert '<p class="sr-only" id="obTryLive" role="status" aria-live="polite"></p>' in html
    assert '<p class="sr-only" id="obTryAlert" role="alert"></p>' in html
    app = read("app.js")
    assert "const _WIZ_TRY_PROBLEMS = ['silent', 'error', 'nomic'];" in app
    say = func(app, "_wizAnnounceTry")
    assert "_wizSay('obTryAlert'" in say and "_wizSay('obTryLive'" in say
    # The words are the callouts on screen, so there is one copy.
    assert '.ob-callout[data-when="${state}"]' in say
    assert "_wizAnnounceTry(sec, state)" in func(app, "wizSetState")


def test_the_mac_allow_buttons_say_what_they_allow():
    html = read("index.html")
    for label, perm in (("Allow microphone", "microphone"), ("Allow keyboard monitoring", "input_monitoring"),
                        ("Allow typing for you", "accessibility")):
        assert f'aria-label="{label}" onclick="wizAllow(\'{perm}\')"' in html
    app = read("app.js")
    render = func(app, "wizRenderPermissions")
    assert 'aria-label="Allow ${_WIZ_PERM_NAMES[p]}"' in render
    assert "_WIZ_PERM_NAMES = { microphone: 'microphone', input_monitoring: 'keyboard monitoring', accessibility: 'typing for you' }" in app
    assert '<p class="sr-only" id="obPermLive" role="status" aria-live="polite"></p>' in html


def test_the_key_check_region_speaks_once_per_message_and_describes_the_field():
    html = read("index.html")
    assert re.search(r'id="wizGroqKeyInput"[^>]*aria-describedby="obKeyState"', html)
    app = read("app.js")
    once = func(app, "_wizKeyStateHtml")
    assert "box.dataset.msg === html" in once
    assert "_wizKeyStateHtml(" in func(app, "wizKeyMessage")
    connect = func(app, "wizInitConnect")
    assert "const complain = WL.debounce(complainNow, 600);" in connect


# ── Dialogs ──────────────────────────────────────────────────────────────────

def test_dialogs_take_focus_trap_tab_and_give_focus_back():
    html = read("index.html")
    assert 'aria-labelledby="updateModalTitle" tabindex="-1"' in html
    assert 'aria-labelledby="hotkeyModalTitle" tabindex="-1"' in html
    app = read("app.js")
    assert "modalOpened(m);" in func(app, "showUpdateModal")
    assert "modalClosed(m);" in func(app, "closeUpdateModal")
    assert "modalOpened(modal);" in func(app, "openHotkeyCapture")
    assert "modalClosed(modal);" in func(app, "closeHotkeyCapture")
    trap = app[app.index("document.addEventListener('keydown', (e) => {\n  // Setup fills"):]
    trap = trap[:trap.index("}, true);")]
    assert "e.key === 'Escape' && overlay.id === 'updateModal'" in trap
    assert "last.focus()" in trap and "first.focus()" in trap


def test_the_hotkey_dialog_announces_the_keys_and_its_error():
    html = read("index.html")
    assert '<p class="sr-only" id="hotkeyCaptureLive" role="status" aria-live="polite"></p>' in html
    assert '<p class="modal-error" id="hotkeyError" role="alert"' in html
    app = read("app.js")
    up = func(app, "_onCaptureKeyUp")
    assert "_announceCaptured(WL.hotkeyName(_lastCapturedKeys, isMacPlatform))" in up


def test_a_dialog_taller_than_the_window_scrolls():
    css = read("style.css")
    overlay = css[css.index(".modal-overlay {"):]
    overlay = overlay[:overlay.index("}")]
    assert "overflow-y: auto;" in overlay and "align-items: flex-start;" in overlay
    card = css[css.index(".modal {"):]
    assert "margin: auto 0;" in card[:card.index("}")]


# ── Main window ──────────────────────────────────────────────────────────────

def test_the_pages_are_buttons_marked_current_not_half_a_tab_pattern():
    html = read("index.html")
    top = html[html.index('<nav class="seg topbar-nav"'):html.index("</nav>")]
    assert 'role="tab' not in top and 'aria-selected' not in top
    assert 'id="navHome" class="active" aria-current="page"' in top
    app = read("app.js")
    assert "tab.setAttribute('aria-current', 'page');" in func(app, "showPage")


def test_the_theme_radios_have_one_tab_stop_and_arrow_keys():
    app = read("app.js")
    assert "el.tabIndex = on ? 0 : -1;" in func(app, "refreshThemePicker")
    assert "WL.radioMove(i, radios.length, e.key)" in app


def test_the_journal_has_day_headings_and_labelled_buttons():
    app = read("app.js")
    day = func(app, "_dayRow")
    assert "row.setAttribute('role', 'heading');" in day and "row.setAttribute('aria-level', '2');" in day
    card = func(app, "makeCard")
    assert 'aria-label="${escHtml(when ? `Copy the dictation from ${when}` : \'Copy\')}"' in card
    assert 'aria-controls="${textId}"' in card


def test_search_results_are_announced():
    html = read("index.html")
    assert 'aria-describedby="searchStatus"' in html
    assert '<span class="sr-only" id="searchStatus" role="status" aria-live="polite"></span>' in html
    assert "WL.searchAnnouncement(query, page.length, _histDone)" in func(read("app.js"), "renderFeed")


def test_removing_a_word_keeps_focus_in_the_list():
    app = read("app.js")
    rm = app[app.index("async function deleteVocabWord("):]
    rm = rm[:rm.index("\n}\n")]
    assert "WL.focusAfterRemove(at, left.length)" in rm
    assert "document.getElementById('vocabInput')" in rm


# ── Contrast and reflow ──────────────────────────────────────────────────────

def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    f = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]


def _ratio(a, b):
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def _tokens(block):
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", block))


def test_control_edges_reach_three_to_one_in_both_themes():
    css = read("tokens.css")
    light = _tokens(css[css.index("body {"):css.index('body[data-theme="dark"] {')])
    dark_block = css[css.index('body[data-theme="dark"] {'):]
    dark = _tokens(dark_block[:dark_block.index("}")])
    auto_block = css[css.index('body[data-theme="auto"] {'):]
    auto = _tokens(auto_block[:auto_block.index("}")])
    assert auto["ring-ctl"] == dark["ring-ctl"] and auto["tog-on-ring"] == dark["tog-on-ring"]
    for t in (light, dark):
        for bg in ("card", "surface", "bg"):
            assert _ratio(t["ring-ctl"], t[bg]) >= 3.0, (t["ring-ctl"], bg)
        assert _ratio(t["tog-on-ring"], t["card"]) >= 3.0
    assert _ratio(light["ring-ctl"], light["surface-2"]) >= 3.0   # the off knob's edge on its track
    comp = read("components.css")
    assert "box-shadow: inset 0 0 0 1px var(--ring-ctl);\n  font: var(--t-body);" in comp   # .input, .select
    assert ".seg > button[aria-current=\"page\"]," in comp
    assert "box-shadow: inset 0 0 0 1px var(--ring-ctl), var(--shadow-btn);" in comp
    style = read("style.css")
    snav = style[style.index('.snav-item[aria-current="true"] {'):]
    assert "var(--ring-ctl)" in snav[:snav.index("}")]


def test_the_window_reflows_below_560_px():
    css = read("style.css")
    narrow = css[css.index("@media (max-width: 560px) {"):]
    narrow = narrow[:narrow.index("\n}\n")]
    for rule in (".topbar { flex-wrap: wrap;", ".topbar-nav { position: static;", ".snav { flex-direction: row; flex-wrap: wrap;",
                 ".row { flex-wrap: wrap; }", ".j-search-input { width: 100%; }"):
        assert rule in narrow, rule
    assert "@media (max-width: 560px)" in read("setup.css")


# ── logic.js ─────────────────────────────────────────────────────────────────

@needs_node
def test_search_announcement():
    assert js("L.searchAnnouncement('', 3, true)") == ""
    assert js("L.searchAnnouncement('invoice', 0, true)") == "No entries match."
    assert js("L.searchAnnouncement('invoice', 1, true)") == "1 entry matches."
    assert js("L.searchAnnouncement('invoice', 12, true)") == "12 entries match."
    # Only the first page is loaded: a full page says "at least".
    assert js("L.searchAnnouncement('invoice', 50, false)") == "At least 50 entries match."


@needs_node
def test_radio_move_and_focus_after_remove():
    assert js("[L.radioMove(0,3,'ArrowRight'), L.radioMove(2,3,'ArrowRight'), L.radioMove(0,3,'ArrowLeft'),"
              " L.radioMove(1,3,'Home'), L.radioMove(0,3,'End'), L.radioMove(1,3,'a'), L.radioMove(-1,3,'ArrowRight')]") \
        == [1, 0, 2, 0, 2, -1, -1]
    # Removed the 2nd of 5: focus the new 2nd; removed the last: the one before; none left: -1.
    assert js("[L.focusAfterRemove(1, 4), L.focusAfterRemove(4, 4), L.focusAfterRemove(0, 0)]") == [1, 3, -1]
