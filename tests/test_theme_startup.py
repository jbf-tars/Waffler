"""The theme survives a restart.

The window runs in pywebview's default private mode, so WebView2 starts with
empty localStorage every launch. The page read the theme only from there
(default 'cream') and, once the bridge was ready, sent it to set_theme, which
wrote 'cream' over the choice in settings.json. Dark or System went back to
Light on every start. settings.json is now the record: the page asks for it
(get_theme) and only saves when the user picks a theme, or once to carry over
a choice that only an older page held.

Offline. The decision runs in Node (ui/logic.js) when Node is installed.
"""
import ast
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
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True,
                         timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
@pytest.mark.parametrize("saved, local, apply, save", [
    ("dark", None, "dark", None),          # a restart: private mode wiped the page
    ("auto", "cream", "auto", None),       # never writes the page's default over it
    ("dark", "dark", "dark", None),
    ("cream", "dark", "cream", None),      # settings.json wins
    ("", None, None, None),                # a new install: nothing saved, nothing sent
    ("", "cream", None, None),
    (None, "dark", "dark", "dark"),        # carried over once from an older page
    ("purple", "auto", "auto", "auto"),
    ("purple", "nonsense", None, None),
])
def test_start_up_never_saves_a_default_over_the_saved_theme(saved, local, apply, save):
    got = js(f"L.startupTheme({json.dumps(saved)}, {json.dumps(local)})")
    assert got == {"apply": apply, "save": save}


def _code(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def test_the_page_asks_python_for_the_theme_when_the_bridge_is_ready():
    app = _code((ROOT / "ui" / "app.js").read_text(encoding="utf-8"))
    assert "addEventListener('pywebviewready', _loadSavedTheme)" in app
    assert "pywebviewready', () => _syncThemeToApp" not in app
    body = app[app.index("async function _loadSavedTheme"):]
    body = body[:body.index("\n}\n")]
    assert "api.get_theme()" in body
    assert "WL.startupTheme(saved, local)" in body
    # The only save at start-up is the decision's own.
    assert body.count("_syncThemeToApp(") == 1
    assert "_syncThemeToApp(d.save)" in body


def test_get_theme_reads_settings_json_and_offers_only_known_themes(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "get_theme")
    ns = {}
    exec(compile(ast.Module([fn], []), "<app.py get_theme>", "exec"), ns)

    class Fake:
        def __init__(self, stored):
            self.stored = stored

        def _load_settings_file(self):
            return self.stored

    get = ns["get_theme"]
    assert get(Fake({"theme": "dark"})) == {"theme": "dark"}
    assert get(Fake({"theme": " Auto "})) == {"theme": "auto"}
    assert get(Fake({})) == {"theme": ""}
    assert get(Fake({"theme": "purple"})) == {"theme": ""}
