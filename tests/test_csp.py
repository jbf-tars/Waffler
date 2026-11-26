"""The window's Content-Security-Policy, and the handlers it allows.

ui/index.html and the Meetings frame (ui/meetings-app/index.html) carry a
policy that runs only the app's own files: no remote script, font, image or
connection, no inline script, no inline event handlers. The markup's
handlers are data-on* attributes bound by ui/handlers.js, which accepts only
"name(simple args)" statements. These tests read the files; the real web view
is checked by scripts/check_csp_webview.py.
"""
import base64
import hashlib
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
EVENTS = ("click", "change", "input", "keydown", "submit", "blur")
NODE = shutil.which("node")


def _policy(html: str) -> dict:
    m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"', html)
    assert m, "no Content-Security-Policy meta"
    out = {}
    for part in m.group(1).split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


@pytest.mark.parametrize("page", ["index.html", "meetings-app/index.html"])
def test_policy_allows_only_the_apps_own_files(page):
    pol = _policy((UI / page).read_text(encoding="utf-8"))
    assert pol["default-src"] == ["'self'"]
    assert pol["object-src"] == ["'none'"] and pol["base-uri"] == ["'none'"]
    assert pol["form-action"] == ["'none'"]
    for directive, sources in pol.items():
        for s in sources:
            assert not re.match(r"(https?:|wss?:|\*|//)", s), f"{page}: {directive} allows {s}"
    script = pol["script-src"]
    assert "'unsafe-inline'" not in script and "'self'" in script
    assert pol["connect-src"] == ["'self'"]


def test_main_page_policy_keeps_unsafe_eval_only_for_pywebview():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert _policy(html)["script-src"] == ["'self'", "'unsafe-eval'"]
    assert "pywebview" in html[html.index("Content-Security-Policy: only"):html.index('<meta http-equiv="Content-Security-Policy"')]


def test_meetings_frame_inline_script_is_allowed_by_its_hash_and_nothing_else():
    html = (UI / "meetings-app" / "index.html").read_text(encoding="utf-8")
    inline = re.findall(r"<script>(.*?)</script>", html, re.S)
    hashes = ["'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'"
              for s in inline]
    script = _policy(html)["script-src"]
    assert sorted(script) == sorted(["'self'"] + hashes), (
        "The Meetings frame was rebuilt: its inline script changed. Put this policy back in "
        "ui/meetings-app/index.html with the new hash (see ui/meetings-app/README.md).")
    assert "'unsafe-eval'" not in script


# -- no inline handlers anywhere -------------------------------------------

# An on...= attribute in markup, or in markup a script writes (lower case, as
# HTML attributes are written here; `el.onclick = fn` in script is allowed).
INLINE_ATTR = re.compile(r"(?<![\w.-])on[a-z]+=[\"'\\]")


def _ui_texts():
    for f in sorted(UI.glob("*.js")) + [UI / "index.html", UI / "meetings-app" / "index.html"]:
        if f.name == "handlers.js":
            continue
        yield f, f.read_text(encoding="utf-8")


def test_no_inline_event_handler_in_markup_or_in_markup_built_by_script():
    for f, text in _ui_texts():
        hits = [m.group(0) for m in INLINE_ATTR.finditer(text)]
        assert not hits, f"{f.name}: inline handlers {hits[:5]} (write data-on... instead)"


def test_no_inline_script_or_javascript_urls_in_the_window():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    for f, text in _ui_texts():
        assert "javascript:" not in text.lower(), f.name


def test_handlers_js_is_the_first_script():
    html = (UI / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="([^"]+)"', html)
    assert scripts[0] == "handlers.js"


# -- every data-on* handler is one the binder accepts, calling a real function

class _Attrs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k.startswith("data-on") and k[7:] in EVENTS:
                self.found.append((tag, k, v))


def _handlers():
    p = _Attrs()
    p.feed((UI / "index.html").read_text(encoding="utf-8"))
    found = [v for _, _, v in p.found]
    # Markup built by app.js: data-onclick="name(...)" inside template strings.
    js = (UI / "app.js").read_text(encoding="utf-8")
    for m in re.finditer(r'data-on(?:%s)="([^"]+)"' % "|".join(EVENTS), js):
        found.append(re.sub(r"\$\{[^}]+\}", "0", m.group(1)))
    return found


ARG = r"(?:'[^']*'|\"[^\"]*\"|-?\d+(?:\.\d+)?|true|false|null|this|this\.value|this\.checked|event)"
CALL = re.compile(r"^([A-Za-z_$][\w$]*)\(\s*(?:%s(?:\s*,\s*%s)*)?\s*\)$" % (ARG, ARG))


def _statements(text):
    return [s.strip() for s in re.split(r";(?=(?:[^']*'[^']*')*[^']*$)", text) if s.strip()]


def _global_functions():
    names = set()
    for f in UI.glob("*.js"):
        s = f.read_text(encoding="utf-8")
        names |= set(re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", s, re.M))
        names |= set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", s))
    return names


def test_every_handler_is_simple_and_calls_a_function_of_the_page():
    handlers = _handlers()
    assert len(handlers) >= 180
    funcs = _global_functions()
    for text in handlers:
        for st in _statements(text):
            if st in ("return false", "event.stopPropagation()"):
                continue
            m = CALL.match(st)
            assert m, f"handlers.js would refuse: {st!r} (in {text!r})"
            assert m.group(1) in funcs, f"no global function {m.group(1)} for {text!r}"


@pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/handlers.js")
def test_the_binder_itself_parses_every_handler(tmp_path):
    """ui/handlers.js's own parser, in Node with a stand-in document."""
    handlers = sorted(set(_handlers()))
    script = tmp_path / "run.js"
    script.write_text("""
const fs = require('fs');
global.window = global;
global.document = { addEventListener() {}, documentElement: { nodeType: 1, getAttribute() { return null; },
  querySelectorAll() { return []; } } };
global.MutationObserver = class { observe() {} };
eval(fs.readFileSync(process.argv[2], 'utf8'));
const out = {};
for (const h of JSON.parse(fs.readFileSync(process.argv[3], 'utf8'))) {
  try { out[h] = window.WaffleHandlers.parse(h); } catch (e) { out[h] = 'REFUSED ' + e.message; }
}
for (const bad of ["alert(document.cookie)", "a(b)", "a(1)+b()", "x.y()", "a('x'), b()"]) {
  try { window.WaffleHandlers.parse(bad); out[bad] = 'ACCEPTED'; } catch (e) { out[bad] = 'REFUSED'; }
}
console.log(JSON.stringify(out));
""", encoding="utf-8")
    data = tmp_path / "h.json"
    data.write_text(json.dumps(handlers), encoding="utf-8")
    res = subprocess.run([NODE, str(script), str(UI / "handlers.js"), str(data)],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    refused = {h: v for h, v in out.items() if h in handlers and isinstance(v, str)}
    assert not refused, refused
    assert out["a(b)"] == "REFUSED" and out["a(1)+b()"] == "REFUSED" and out["x.y()"] == "REFUSED"
    # alert( document.cookie ) is not a literal argument either.
    assert out["alert(document.cookie)"] == "REFUSED"
    steps = out["askFactoryResetKeys(true); return false;"]
    assert steps == [{"call": "askFactoryResetKeys", "args": [{"lit": True}]}, {"prevent": True}]
