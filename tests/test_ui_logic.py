"""The window's small decisions, checked without opening it.

ui/logic.js holds the pure parts of the UI (labels, hotkey presets, what the
Journal shows for a search, the update messages). These tests run it in Node,
which the CI runners have; without Node those checks are skipped and the
source checks below still run. No network, no keys, no private data.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
LOGIC = UI / "logic.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")


def js(expr, setup=""):
    """Evaluate a JavaScript expression with ui/logic.js loaded as L."""
    code = (f"const L = require({json.dumps(str(LOGIC))});\n{setup}\n"
            f"Promise.resolve({expr}).then((v) => process.stdout.write(JSON.stringify(v === undefined ? null : v)));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60,
                         encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def code_only(src):
    """JavaScript or CSS with comments removed, so a comment explaining an
    old bug does not count as the bug."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ── the status pill (QW1) ────────────────────────────────────────────────────

@needs_node
def test_each_status_has_one_plain_label():
    views = js("['idle','listening','paused','processing','done'].map((s) => L.statusView(s))")
    assert [v["label"] for v in views] == ["Ready", "Recording", "Paused", "Cleaning up", "Done"]
    assert [v["cls"] for v in views] == ["idle", "listening", "paused", "processing", "done"]


@needs_node
def test_an_unknown_status_shows_ready_not_its_raw_name():
    assert js("L.statusView('bogus')") == {"cls": "idle", "label": "Ready"}
    assert js("L.statusView('__proto__')") == {"cls": "idle", "label": "Ready"}


@needs_node
def test_done_goes_back_to_ready_after_three_seconds():
    assert js("L.DONE_RESET_MS") == 3000


@needs_node
def test_every_status_python_sends_has_a_view():
    sent = set(re.findall(r'notify_js_status\("(\w+)"\)', (ROOT / "app.py").read_text(encoding="utf-8")))
    assert sent, "app.py should send statuses to the window"
    known = set(js("Object.keys(L.STATUS_VIEWS)"))
    assert sent <= known, f"statuses with no label: {sorted(sent - known)}"


def test_status_update_keeps_the_pill_class_and_needs_no_missing_element():
    app = code_only(read("app.js"))
    body = app[app.index("window.waffler_status"):]
    body = body[:body.index("\n};") + 3]
    # Assigning className wiped j-listen, the class that styles the pill.
    assert "className" not in body
    assert "classList.remove(...WL.STATUS_CLASSES)" in app
    # #recordingOverlay is not in index.html; touching it threw on every update.
    assert "recordingOverlay" not in app
    assert 'id="recordingOverlay"' not in read("index.html")


def test_the_idle_pill_says_ready():
    html = read("index.html")
    assert "Waiting for activation" not in html
    assert re.search(r'id="statusText"[^>]*>Ready<', html)


def test_logic_loads_before_app():
    html = read("index.html")
    assert html.index('<script src="logic.js"></script>') < html.index('<script src="app.js"></script>')
