"""Fixes from the review of the Atelier build (3.15).

The top bar as a running head, the empty Journal, setup's later steps, the
Journal margin, What you said, the dialogs, and keyboard and screen reader
fixes. These read ui/ files (and run ui/logic.js in Node where it is
installed); the look itself was checked in the capture harness.
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


def rule(css, selector):
    body = css[css.index(selector + " {"):]
    return body[:body.index("}")]


# ── Top bar ──────────────────────────────────────────────────────────────────

def test_the_pages_are_plain_words_with_a_rule_under_the_open_one():
    html = read("index.html")
    assert '<nav class="topbar-nav" aria-label="Pages">' in html
    assert 'class="seg topbar-nav"' not in html
    css = read("style.css")
    on = rule(css, '.topbar-nav > button[aria-current="page"]::after')
    assert "height: 2px;" in on and "background: var(--text);" in on
    assert "background: none;" in rule(css, ".topbar-nav > button")
    assert "--bar-h: 52px;" in read("tokens.css")


def test_ready_is_green_and_sits_loose_with_a_thin_rule_before_the_keys():
    html = read("index.html")
    status = html[html.index('id="statusIndicator"'):]
    status = status[:status.index("</div>")]
    assert status.index('class="status-sep"') < status.index('id="hotkeyHint"')
    comp = read("components.css")
    box = rule(comp, ".status")
    assert "border-radius" not in box and "box-shadow" not in box and "width:" not in box
    assert re.search(r"\.status\.idle \.status-dot,\s*\.status\.done \.status-dot \{ background: var\(--ok\);", comp)
    # The seconds are in the UI face, not mono, like the Journal's strip.
    assert "var(--f-mono)" not in rule(comp, ".status-t")


def test_the_bar_does_not_repeat_the_seconds_the_journal_strip_shows():
    comp = read("components.css")
    assert 'body[data-page="home"] .status.listening .status-t' in comp
    assert "document.body.dataset.page = page;" in func(read("app.js"), "showPage")
    assert '<body data-page="home">' in read("index.html")


# ── The empty Journal ────────────────────────────────────────────────────────

def test_the_empty_journal_is_the_first_entry_of_today_not_a_picture():
    html = read("index.html")
    first = html[html.index('id="emptyState"'):html.index('<!-- A search with no matches')]
    assert 'class="j-date-divider"' in first and 'id="emptyDay"' in first
    assert 'class="j-first-ent"' in first and 'id="emptyKeys"' in first
    assert "j-first-art" not in first
    css = read("style.css")
    assert "stage-lattice" not in css and "field-syrup-dark" not in css
    assert "var(--f-serif)" in rule(css, ".j-first-title")
    draw = func(read("app.js"), "drawFeed")
    # The serif title and the margin stay; only the search goes.
    assert "$rail" not in draw and "$strip" not in draw
    assert "search.hidden = view.kind === 'empty';" in draw
    assert "WL.dayHeading(key, now).date" in draw


# ── What you said (the old View original) ───────────────────────────────────

def test_what_you_said_is_a_labelled_button_with_one_name():
    app = read("app.js")
    card = func(app, "makeCard")
    assert 'class="btn btn-quiet btn-sm text-toggle"' in card
    assert "<span>What you said</span>" in card
    toggle = func(app, "toggleRawHandler")
    # Only aria-pressed changes; the name stays "What you said".
    assert "aria-label" not in toggle and ".title" not in toggle
    assert "toggleEl.setAttribute('aria-pressed', 'true');" in toggle
    assert "toggleEl.setAttribute('aria-pressed', 'false');" in toggle
    assert "'Show clean'" not in app


def test_the_changelog_no_longer_claims_what_was_said_always_differed():
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "differed every time" not in log
    assert "31 entries with the button and 29 without" in log


# ── Setup: every step on squared paper ───────────────────────────────────────

def test_every_setup_picture_is_on_squared_paper_you_can_see():
    css = read("setup.css")
    art = rule(css, ".ob-art")
    assert "var(--stage-grid)" in art and "background-color: var(--stage);" in art
    assert ".webp" not in css, "no photographs behind setup"
    tokens = read("tokens.css")
    m = re.search(r"--stage-grid:\s+rgba\(122, 87, 23, ([0-9.]+)\);", tokens)
    # --stage-line (0.065) could not be seen at 1x or 2x on the cream stage.
    assert m and float(m.group(1)) >= 0.1
    assert tokens.count("--stage-grid:") == 3, "light, dark, and dark for System"


def test_setup_labels_are_sentence_case_not_mono_capitals():
    css = read("setup.css")
    for sel in (".ob-pl", ".ob .eyebrow"):
        r = rule(css, sel)
        assert "text-transform: none;" in r and "var(--f-ui)" in r, sel
    for sel in (".ob-chip", ".ob-footnote", ".ob-holdcap", ".ob-ghostcap", ".ob-flab, .ob-flab2"):
        assert "var(--f-mono)" not in rule(css, sel), sel


def test_the_microphone_select_is_drawn_like_the_other_fields():
    sel = rule(read("setup.css"), ".ob-micselect")
    assert "appearance: none;" in sel and "var(--ring-ctl)" in sel


# ── The Journal margin ───────────────────────────────────────────────────────

def test_the_margin_is_never_taller_than_the_window():
    css = read("style.css")
    rail = rule(css, ".j-rail")
    assert "position: sticky;" in rail
    assert "max-height: calc(100vh - var(--bar-h));" in rail and "overflow-y: auto;" in rail
    narrow = css[css.index("@media (max-width: 900px) {"):]
    narrow = narrow[:narrow.index("\n}\n")]
    assert "max-height: none;" in narrow


def test_a_margin_notice_uses_the_full_width_under_its_icon():
    css = read("style.css")
    assert "position: absolute;" in rule(css, ".j-rail .notice .itile")
    assert "text-indent: 23px;" in rule(css, ".j-rail .notice-title")
    assert "padding-right: 34px" not in rule(css, ".j-rail .notice")


# ── Dialogs and buttons ──────────────────────────────────────────────────────

def test_buttons_have_the_atelier_corners_not_pills():
    comp = read("components.css")
    assert "border-radius: 8px;" in rule(comp, ".btn")
    assert "border-radius: 7px;" in rule(comp, ".btn-sm")
    assert "border-radius: 10px;" in rule(comp, ".btn-lg")


def test_dialogs_have_a_light_veil_and_a_serif_title():
    css = read("style.css")
    overlay = rule(css, ".modal-overlay")
    assert "backdrop-filter" not in overlay
    assert "var(--f-serif)" in rule(css, ".modal-title")
    assert "border-radius: 14px;" in rule(css, ".modal")


@needs_node
def test_the_update_dialog_speaks_plainly():
    v = js("L.updateCheckView({update_available: true, latest_version: '3.15.2', current_version: '3.15.0', download_url: 'https://x/y.exe'})")
    assert v["title"] == "Waffler 3.15.2 is ready"
    assert v["subtitle"] == "You have 3.15.0. Download it and install it now?"
    assert v["primary"]["label"] == "Download and install"
    v = js("L.updateCheckView({update_available: false, latest_version: '3.15.0', current_version: '3.15.0'})")
    assert v["subtitle"] == "You have Waffler 3.15.0."
    v = js("L.updateCheckView({update_available: false, latest_version: '3.15.0', current_version: '3.15.1'})")
    assert v["subtitle"] == "You have Waffler 3.15.1. The newest release is 3.15.0."
    assert " & " not in read("logic.js").split("function updateCheckView(")[1].split("\n  }\n")[0]


# ── Keyboard and screen readers ──────────────────────────────────────────────

def test_the_hotkey_presets_are_one_tab_stop_with_arrow_keys():
    app = read("app.js")
    cards = func(app, "renderHotkeyPresetCards")
    assert "b.tabIndex = on ? 0 : -1;" in cards
    assert "host.children[had].focus()" in cards
    keys = app[app.index("document.getElementById('hotkeyPresetCards');\n  if (!host) return;\n  host.addEventListener('keydown'"):]
    keys = keys[:keys.index("\n});\n")]
    assert "WL.radioMove(i, radios.length, e.key)" in keys
    # Landing on Custom only moves the focus: it opens a dialog.
    assert "!radios[to].dataset.custom" in keys


@needs_node
def test_radio_move_wraps_and_has_home_and_end():
    assert js("[L.radioMove(0, 3, 'ArrowRight'), L.radioMove(2, 3, 'ArrowDown'), L.radioMove(0, 3, 'ArrowUp'), "
              "L.radioMove(1, 3, 'Home'), L.radioMove(0, 3, 'End'), L.radioMove(0, 3, 'a')]") == [1, 0, 2, 0, 2, -1]


def test_each_key_button_names_its_provider_and_says_if_its_box_is_open():
    app = read("app.js")
    row = func(app, "renderProviderOrder")
    assert 'aria-label="${escHtml(_keyButtonName(r))}"' in row
    assert 'aria-expanded="${_openKeyEditor === r.id}"' in row and "aria-controls=" in row
    name = func(app, "_keyButtonName")
    assert "Replace the ${r.name} key" in name and "Add ${" in name
    assert "_syncKeyButtons();" in func(app, "openKeyEditor")
    close = func(app, "closeKeyEditor")
    assert "_syncKeyButtons();" in close and "b.focus()" in close
    # Esc in the box closes it and goes back to the button.
    assert "if (e.key !== 'Escape') return;" in app and "closeKeyEditor(true);" in app
    html = read("index.html")
    assert html.count('onclick="closeKeyEditor(true)">Cancel</button>') == 3


def test_privacy_buttons_are_tied_to_their_rows():
    html = read("index.html")
    assert 'id="logsBtn" aria-describedby="logsTitle"' in html and 'id="logsTitle"' in html
    assert 'id="resetAsk" aria-describedby="resetTitle"' in html and 'id="resetTitle"' in html
    assert 'id="unsentDelete" aria-describedby="unsentTitle"' in html


def test_try_a_sentence_has_a_control_edge_you_can_see():
    # --ring was about 1.2:1; --ring-ctl is the 3:1 edge every other field has.
    assert "0 0 0 1px var(--ring-ctl)" in rule(read("style.css"), ".vtry")
