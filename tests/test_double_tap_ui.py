"""The window's side of double-tap to go hands-free (S11b): one switch under
the Hands-free row on Settings, Shortcuts.

The pure parts live in ui/logic.js and run in Node; the rest is checked by
source, as the other window tests do. The row is drawn by app.js from the
get_shortcuts() answer, so it is not in index.html.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


# ── the pure part ────────────────────────────────────────────────────────────

@needs_node
def test_it_is_off_until_the_answer_says_true():
    assert js("L.doubleTapView(undefined).on") is False
    assert js("L.doubleTapView({}).on") is False
    assert js("L.doubleTapView({double_tap_handsfree: true}).on") is True
    assert js("L.doubleTapView({double_tap_handsfree: false}).on") is False


@needs_node
def test_a_stray_value_is_off_as_the_engine_reads_it():
    for stray in ("'true'", "1", "'yes'", "null", "[true]", "{}"):
        assert js(f"L.doubleTapView({{double_tap_handsfree: {stray}}}).on") is False, stray


@needs_node
def test_the_label_and_the_sentence_say_what_the_tap_does():
    v = js("L.doubleTapView({})")
    assert v["label"] == "Double-tap to go hands-free"
    assert v["desc"] == ("Tap your dictation hotkey twice, quickly, to keep recording without holding it. "
                         "Tap it once to finish. Holding the hotkey works as it always did.")


@needs_node
def test_the_toast_after_changing_it_is_a_plain_sentence():
    assert js("L.doubleTapSaved(true)") == "Waffler will keep recording when you double-tap."
    assert js("L.doubleTapSaved(false)") == "Double-tap is off."


def test_the_words_have_no_em_dash_and_no_jargon():
    src = read("logic.js")
    block = src[src.index("const DOUBLE_TAP_TEXT"):src.index("function doubleTapSaved")]
    assert "—" not in block and "–" not in block
    for jargon in ("listener", "debounce", "event tap", "state machine", "double_tap"):
        assert jargon not in block.split("function doubleTapView")[0]


# ── the row ──────────────────────────────────────────────────────────────────

def _app():
    return code_only(read("app.js"))


def test_the_row_is_drawn_right_under_hands_free_and_nowhere_else():
    app = read("app.js")
    assert "r.id === 'hands_free' ? _doubleTapRowHtml() : ''" in app
    assert "double_tap" not in read("index.html").replace("Double-tap", "")      # no static copy to drift


def test_the_row_is_a_labelled_switch_with_a_description():
    app = read("app.js")
    row = app[app.index("function _doubleTapRowHtml()"):app.index("// Save the switch; a refused save")]
    assert '<label for="doubleTapToggle">' in row
    assert re.search(r'<input type="checkbox" class="tog" role="switch" id="doubleTapToggle" '
                     r'aria-describedby="doubleTapDesc"', row)
    assert 'id="doubleTapDesc"' in row and 'role="listitem"' in row
    assert "escHtml(v.label)" in row and "escHtml(v.desc)" in row       # the words come from logic.js


def test_a_change_is_saved_through_save_settings_and_a_refusal_puts_the_switch_back():
    app = read("app.js")
    assert "list.addEventListener('change'" in app
    assert "e.target.id === 'doubleTapToggle'" in app
    fn = app[app.index("async function setDoubleTap(on)"):app.index("function renderShortcuts()")]
    assert "saveSetting('double_tap_handsfree', !!on)" in fn
    assert "tog.checked = !on" in fn
    assert "_shortcuts.double_tap_handsfree = !!on" in fn
    assert "WL.doubleTapSaved(value)" in app


@needs_node
def test_the_logic_exports_what_the_window_calls():
    assert js("[typeof L.doubleTapView, typeof L.doubleTapSaved, typeof L.DOUBLE_TAP_TEXT]") == [
        "function", "function", "object"]
    src = read("app.js")
    for call in ("WL.doubleTapView(_shortcuts)", "WL.doubleTapSaved("):
        assert call in src, call


def test_the_row_adds_no_remote_asset_and_no_style_of_its_own():
    css = read("style.css")
    assert "sc-dt" not in css          # the row is the page's own .row with the .tog switch
    row = read("app.js")
    row = row[row.index("function _doubleTapRowHtml()"):row.index("// Save the switch; a refused save")]
    assert "http" not in row and "style=" not in row
