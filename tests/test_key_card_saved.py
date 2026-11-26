"""A saved key looks saved: the key card's own words (ui/logic.js).

After Save the box was emptied and, until the settings came back, drawn
from the settings of before ("Paste your key", no state), and a Save with
nothing in the box said "Paste your Groq key first." even with a key saved.
James read both as the key not saving. Now the card is drawn at once from
the settings with the key just saved (withSavedKey, maskKey as app.py
_mask writes it) and says "Saved"; an empty box says it is empty, and
that a saved key is unchanged. Run in Node.
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


def js(expr):
    code = (f"const L = require({json.dumps(str(LOGIC))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_a_key_is_masked_as_the_app_masks_it():
    assert js("L.maskKey('gsk_acceptedacceptedaccepted')") == "gsk_acce…pted"
    assert js("L.maskKey('short-key-12')") == "*" * 12
    assert js("L.maskKey('')") == ""


@needs_node
def test_the_settings_with_a_key_just_saved_and_nothing_else_changed():
    before = {"groq_key_set": False, "theme": "dark"}
    out = js(f"(() => {{ const s = {json.dumps(before)}; const n = L.withSavedKey(s, 'groq', 'gsk_acceptedacceptedaccepted');"
             " return {n, s}; })()")
    assert out["n"] == {"groq_key_set": True, "groq_key_masked": "gsk_acce…pted", "theme": "dark"}
    assert out["s"] == before, "a copy: what the window had is not changed under it"
    assert js("L.withSavedKey(null, 'cerebras', 'csk-acceptedacceptedaccepted')")["cerebras_key_set"] is True


@needs_node
def test_a_key_just_saved_says_saved_beside_it():
    v = js("L.keyCardView('groq', null, L.withSavedKey({}, 'groq', 'gsk_acceptedacceptedaccepted'), {ok: true, saved: true}, false)")
    assert (v["state"], v["cls"], v["placeholder"], v["hasKey"]) == ("Saved", "ok", "gsk_acce…pted", True)
    assert js("L.keyCardView('groq', null, {groq_key_set: true}, {ok: true}, false)")["state"] == "Working"


@needs_node
def test_save_with_nothing_in_the_box_says_so():
    with_key, without = js("L.keyEmptyLine('Groq', true)"), js("L.keyEmptyLine('Groq', false)")
    assert with_key == "The box is empty. Your saved Groq key is unchanged."
    assert without == "The box is empty. Paste your Groq key, then click Save."
    for line in (with_key, without):
        first = re.split(r"(?<=\.)\s", line)[0]
        assert len(first.split()) <= 9, "the card's line is the first sentence"
