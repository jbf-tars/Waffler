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
