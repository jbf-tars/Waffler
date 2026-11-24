"""A file or link dropped on the window where nothing takes it is refused.

Before: dropping a link, an .html or an .svg on Home or Settings made the
window open it in place of Waffler, and pywebview gives every page it loads
the bridge to Python. Now the main page and the Meetings frame refuse a drop
that no drop zone has taken (L.blockStrayDrops), while Meetings' own import
drop zone, which handles its drops first, keeps working. Run in Node.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOGIC = ROOT / "ui" / "logic.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")


def js(body):
    code = (f"const L = require({json.dumps(str(LOGIC))});\n"
            "function drag(type, effect) {\n"
            "  const e = new Event(type, {cancelable: true});\n"
            "  Object.defineProperty(e, 'dataTransfer', {value: {dropEffect: effect}});\n"
            "  return e;\n"
            "}\n"
            f"const out = (() => {{ {body} }})();\n"
            "process.stdout.write(JSON.stringify(out));")
    done = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60,
                          encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_a_drop_nothing_takes_is_refused():
    out = js("const t = new EventTarget(); L.blockStrayDrops(t);"
             "const over = drag('dragover', 'copy'); t.dispatchEvent(over);"
             "const drop = drag('drop', 'copy'); t.dispatchEvent(drop);"
             "return [over.defaultPrevented, over.dataTransfer.dropEffect, drop.defaultPrevented];")
    assert out == [True, "none", True]


@needs_node
def test_a_drop_zone_that_takes_the_drop_keeps_it():
    out = js("const t = new EventTarget();"
             "t.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });"
             "L.blockStrayDrops(t);"
             "const over = drag('dragover', 'move'); t.dispatchEvent(over);"
             "return [over.defaultPrevented, over.dataTransfer.dropEffect];")
    assert out == [True, "copy"]


def _code(name):
    src = (ROOT / "ui" / name).read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def test_the_window_and_the_meetings_frame_both_refuse_stray_drops():
    app = _code("app.js")
    assert re.search(r"WL\.blockStrayDrops\(window\)", app)
    assert re.search(r"mtFrame[\s\S]{0,400}addEventListener\('load'[\s\S]{0,300}"
                     r"WL\.blockStrayDrops\([^)]*contentWindow\)", app)
