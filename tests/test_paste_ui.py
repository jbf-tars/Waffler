"""The window's side of safe paste: one Settings row, and what Privacy says.

Settings, General has "Keep what I dictated on the clipboard" under "Paste
when I let go" (Mac only; Windows cannot put the clipboard back). The Privacy
and data page says, in plain words, what Waffler reads while it pastes: the
clipboard it puts back and the text field it checks. The pure parts live in
ui/logic.js and run in Node; the rest is checked by source, as the other
window tests do.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


# ── the pure part ────────────────────────────────────────────────────────

@needs_node
def test_the_row_is_for_a_mac_only():
    v = js("L.clipboardAfterPasteView(true, 'restore', false)")
    assert v["visible"] is False and v["desc"] == ""
    v = js("L.clipboardAfterPasteView(true, 'restore', true)")
    assert v["visible"] is True and v["disabled"] is False


@needs_node
def test_the_switch_is_on_only_for_keep_and_any_other_value_is_the_default():
    assert js("L.clipboardAfterPasteView(true, 'keep', true)")["checked"] is True
    for mode in ("restore", "", "bogus", None):
        assert js(f"L.clipboardAfterPasteView(true, {mode!r}, true)".replace("None", "null"))["checked"] is False
    assert js("L.clipboardAfterPasteView(true, undefined, true)")["checked"] is False


@needs_node
def test_with_paste_off_the_switch_is_fixed_on_and_says_why():
    v = js("L.clipboardAfterPasteView(false, 'restore', true)")
    assert v["visible"] and v["checked"] is True and v["disabled"] is True
    assert "Paste is off" in v["desc"] and "clipboard" in v["desc"]
    # Nothing was said about paste: it is on.
    assert js("L.clipboardAfterPasteView(undefined, 'restore', true)")["disabled"] is False


@needs_node
def test_the_description_is_a_plain_sentence_about_what_waffler_does():
    on = js("L.clipboardAfterPasteView(true, 'restore', true)")["desc"]
    assert on == "Off, Waffler puts back what you had copied once it has pasted."
    assert js("L.clipboardAfterPasteSaved('keep')") == "Waffler leaves your text on the clipboard."
    assert js("L.clipboardAfterPasteSaved('restore')") == "Waffler puts back what you had copied."


def test_the_words_have_no_em_dash_and_no_jargon():
    src = read("logic.js")
    block = src[src.index("function clipboardAfterPasteView"):src.index("// General: the microphone meter")]
    assert "—" not in block and "–" not in block
    for jargon in ("pasteboard", "NSPasteboard", "snapshot", "changeCount", "Accessibility"):
        assert jargon not in block


# ── Settings, General ────────────────────────────────────────────────────

def test_the_row_sits_under_paste_when_i_let_go_and_starts_hidden():
    html = read("index.html")
    general = html[html.index('id="secGeneral"'):html.index('id="secKeys"')]
    auto = general.index('id="autoPasteToggle"')
    row = general.index('id="clipboardAfterPasteRow"')
    assert auto < row < general.index('id="micSelect"')
    tag = general[general.rindex("<div", 0, row):general.index(">", row)]
    assert 'class="row"' in tag and "hidden" in tag, "hidden until a Mac says otherwise"
    assert '<label for="keepClipboardToggle">Keep what I dictated on the clipboard</label>' in general
    assert re.search(r'<input type="checkbox" class="tog" role="switch" id="keepClipboardToggle" '
                     r'onchange="setClipboardAfterPaste\(this.checked\)">', general)
    assert 'id="clipboardAfterPasteDesc"' in general
    assert "Off, Waffler puts back what you had copied once it has pasted." in general


def test_the_window_reads_and_saves_the_choice():
    app = code_only(read("app.js"))
    assert "function renderClipboardRow()" in app
    load = app[app.index("async function loadSettings()"):app.index("function renderClipboardRow()")]
    assert "renderClipboardRow();" in load
    assert "reads.hidden = !isMacPlatform;" in load
    assert "WL.clipboardAfterPasteView(" in app and "isMacPlatform);" in app
    setter = app[app.index("async function setClipboardAfterPaste(keep)"):]
    setter = setter[:setter.index("\n}\n")]
    assert "const mode = keep ? 'keep' : 'restore';" in setter
    assert "saveSetting('clipboard_after_paste', mode)" in setter
    # A refused save puts the switch back where it was.
    assert "tog.checked = !keep;" in setter
    save = app[app.index("async function saveSetting(key, value)"):]
    save = save[:save.index("\n}\n")]
    assert "key === 'clipboard_after_paste'" in save and "WL.clipboardAfterPasteSaved(value)" in save
    # Turning paste on or off changes what the row means.
    assert "_lastSettings.auto_paste = !!value;" in save and "renderClipboardRow();" in save


def test_the_row_is_never_shown_off_a_mac():
    app = code_only(read("app.js"))
    fn = app[app.index("function renderClipboardRow()"):app.index("async function setClipboardAfterPaste")]
    assert "isMacPlatform" in fn and "row.hidden = !v.visible;" in fn


# ── Privacy and data ─────────────────────────────────────────────────────

def _privacy():
    html = read("index.html")
    sec = html[html.index('id="secPrivacy"'):]
    return sec[:sec.index("</section>")]


def test_privacy_says_what_is_read_while_it_pastes():
    sec = _privacy()
    block = sec[sec.index('id="privPasteReads"'):sec.index('<h2 class="sh2">Help</h2>')]
    assert '<h2 class="sh2">While it pastes</h2>' in block
    assert ("To confirm a paste landed, Waffler reads whether that field changed. "
            "It never stores or logs what is in it.") in block
    assert "The text field you paste into" in block
    assert ("Unless you keep your text there, Waffler holds what you had copied in memory for about "
            "a second, then puts it back. It never stores or logs it.") in block
    assert "hidden" in sec[sec.rindex("<div", 0, sec.index('id="privPasteReads"')):sec.index('id="privPasteReads"') + 40]


def test_privacy_copy_has_no_em_dash():
    block = _privacy()
    block = block[block.index('id="privPasteReads"'):block.index('<h2 class="sh2">Help</h2>')]
    assert "—" not in block and "–" not in block


def test_the_policy_and_readme_say_the_same_thing():
    policy = (ROOT / "docs" / "CODE_SIGNING_POLICY.md").read_text(encoding="utf-8")
    assert "**Text field (macOS).**" in policy
    assert "never stores, logs or sends that text" in policy
    assert "It never\n  reads a password field" in policy
    assert "put back; it is\n  never stored or logged" in policy
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "reads whether the text field you paste into changed" in readme
    assert "It never stores or logs what is in it" in readme
