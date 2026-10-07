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
