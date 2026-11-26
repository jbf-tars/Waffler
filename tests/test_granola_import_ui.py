"""Import from Granola, in the window: the entry beside Import audio and the
one sheet it opens. What the sheet says is a pure function in ui/logic.js
(run in Node); the rest is read from the source, as the other window tests do.
The Python side is in tests/test_granola_import_run.py."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


def view(status):
    return js(f"L.granolaView({status})")


def sheet():
    html = read("index.html")
    start = html.index('<div class="gr" id="granolaSheet"')
    return html[start:html.index("<!-- Toast -->", start)]


def words(text):
    return re.findall(r"[\w’']+", text)


# ── what the sheet says ─────────────────────────────────────────────────────

@needs_node
def test_it_offers_in_one_line_and_one_button():
    for status in ("null", "undefined", "{}", "{ok: true, connected: false, state: 'idle'}"):
        v = view(status)
        assert v["title"] == "Bring your Granola meetings into Waffler."
        assert (v["primary"], v["primaryText"]) == ("import", "Connect Granola")
        assert v["quiet"] == "" and v["count"] == "" and v["note"] == ""


@needs_node
def test_connected_it_offers_to_import_and_to_disconnect():
    v = view("{ok: true, connected: true, state: 'idle'}")
    assert v["title"] == "Bring your Granola meetings into Waffler."
    assert (v["primary"], v["primaryText"]) == ("import", "Import")
    assert (v["quiet"], v["quietText"]) == ("disconnect", "Disconnect Granola")


@needs_node
def test_while_the_browser_is_open_it_waits_with_a_quiet_cancel():
    v = view("{ok: true, connected: false, state: 'connecting'}")
    assert v["title"] == "Approve in your browser." and v["primary"] == ""
    assert (v["quiet"], v["quietText"]) == ("cancel", "Cancel")


@needs_node
def test_importing_says_how_far_with_a_quiet_cancel():
    v = view("{ok: true, connected: true, state: 'importing', done: 37, total: 185}")
    assert (v["title"], v["count"], v["percent"]) == ("Importing.", "37 of 185", 20)
    assert v["primary"] == "" and (v["quiet"], v["quietText"]) == ("cancel", "Cancel")
    early = view("{ok: true, connected: true, state: 'importing', done: 0, total: 0}")
    assert early["title"] == "Importing." and early["count"] == "", "no count until Granola has said how many"
    assert view("{state: 'importing', done: 500, total: 185}")["percent"] == 100


@needs_node
def test_done_says_how_many_and_only_then_how_many_could_not_be_read():
    v = view("{ok: true, connected: true, state: 'done', done: 185, total: 185, failed: 0}")
    assert v["title"] == "185 meetings imported." and v["note"] == ""
    assert (v["primary"], v["primaryText"]) == ("close", "Done")
    assert (v["quiet"], v["quietText"]) == ("disconnect", "Disconnect Granola")
    some = view("{ok: true, connected: true, state: 'done', done: 182, total: 185, failed: 3}")
    assert some["title"] == "182 meetings imported." and some["note"] == "3 could not be read."
    assert view("{state: 'done', done: 1, total: 1}")["title"] == "1 meeting imported."
    assert view("{state: 'done', done: 0, total: 0}")["title"] == "Granola has no meetings yet."
    assert view("{state: 'done', done: 0, total: 2, failed: 2}")["title"] == "No meetings imported."


@needs_node
def test_a_failure_is_one_plain_sentence_and_a_way_to_try_again():
    said = "Waffler couldn't reach Granola. Check your internet connection and try again."
    v = view("{ok: true, connected: false, state: 'error', error: " + repr(said) + "}")
    assert v["title"] == said and (v["primary"], v["primaryText"]) == ("import", "Try again")
    assert v["note"] == "" and v["count"] == ""
    assert view("{ok: false}")["title"] == "That didn’t work. Try again."
    assert view("{state: 'error', error: 7}")["title"] == "That didn’t work. Try again."


@needs_node
def test_every_state_is_few_words():
    states = ["null", "{connected: true}", "{state: 'connecting'}", "{state: 'importing', done: 37, total: 185}",
              "{state: 'done', done: 185, total: 185}", "{state: 'done', done: 182, total: 185, failed: 3}",
              "{connected: true, state: 'done', done: 0, total: 0}"]
    for status in states:
        v = view(status)
        said = " ".join([v["title"], v["count"], v["note"], v["primaryText"], v["quietText"]])
        assert len(words(said)) <= 11, said
        assert len(words(v["primaryText"])) <= 2 and len(words(v["quietText"])) <= 2


# ── the sheet and the entry, in the source ──────────────────────────────────

def test_the_sheet_is_a_dialog_with_one_line_one_button_and_one_quiet_action():
    html = sheet()
    assert 'role="dialog"' in html and 'aria-modal="true"' in html and 'aria-labelledby="grTitle"' in html
    assert ' hidden>' in html.split("\n")[0], "closed until it is asked for"
    assert ">Bring your Granola meetings into Waffler.</h2>" in html
    assert html.count("<button") == 3, "the button, the quiet action and Close"
    assert 'id="grGo">Connect Granola</button>' in html and 'aria-label="Close"' in html
    assert 'role="status"' in html, "a screen reader hears each state"


def test_it_is_dark_whatever_the_theme():
    css = code_only(read("meetings.css"))
    rule = css[css.index(".gr {"):css.index("}", css.index(".gr {"))]
    assert "--gr-bg: #12100E" in rule and "background: var(--gr-bg)" in rule and "position: fixed" in rule
    assert "body[data-theme" not in css[css.index(".gr {"):], "no lighter version for the light theme"


def test_the_entry_sits_beside_import_audio_and_opens_the_sheet():
    page = code_only(read("granola.js"))
    assert "'Import from Granola'" in page and "/Import audio/" in page
    assert "insertAdjacentElement('afterend'" in page and "addEventListener('click', open)" in page
    assert "MutationObserver(plant)" in page, "put back when the meetings interface redraws"
    assert "getElementById('wafflerGranola')" in page, "one button, never two"
    assert '<script src="granola.js"></script>' in read("index.html")
    # The build of the meetings interface still has the button it goes beside.
    build = next((ROOT / "ui" / "meetings-app" / "assets").glob("index-*.js")).read_text(encoding="utf-8")
    assert '"Import audio"' in build and 'className:"foot"' in build


def test_the_page_uses_the_four_calls_and_is_told_as_the_import_goes():
    page = code_only(read("granola.js"))
    for name in ("granola_status", "granola_import", "granola_cancel", "granola_disconnect"):
        assert name in page, name
    assert "window.waffler_granola_changed" in page and "L.granolaView(" in page
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    for name in ("def granola_status", "def granola_import", "def granola_cancel", "def granola_disconnect"):
        assert name in app, name
    assert "window.waffler_granola_changed && window.waffler_granola_changed(" in app
    assert "Escape" in page, "Esc closes the sheet; an import that is running carries on"


def test_no_token_can_reach_the_page():
    source = (ROOT / "src" / "meetings" / "granola_import.py").read_text(encoding="utf-8")
    status = source[source.index("    def status(self)"):source.index("    def _set(self")]
    assert "token" not in status.split('"""')[2], "status() is built from the view and `connected` only"
    assert "granola.json" not in read("granola.js") and "token" not in code_only(read("granola.js")).lower()


def test_a_build_is_checked_for_the_new_parts():
    check = (ROOT / "src" / "self_check.py").read_text(encoding="utf-8")
    assert '"meetings.granola_import"' in check and '"ui/granola.js"' in check
    for spec in ("Waffler_mac.spec", "Waffler_windows.spec"):
        assert "'http.server'" in (ROOT / spec).read_text(encoding="utf-8"), spec


# ── the entry does not vanish if the frame's markup changes ─────────────────

@needs_node
def test_the_entry_falls_back_to_the_window_when_it_cannot_be_planted():
    place = lambda s: js(f"L.granolaEntryPlace({s})")  # noqa: E731
    assert place("{planted: true, waitedMs: 0}") == "frame"
    assert place("{planted: true, waitedMs: 99999}") == "frame"
    assert place("{planted: false, reachable: true, waitedMs: 100}") == "wait"
    wait = js("L.GRANOLA_WAIT_MS")
    assert 1000 <= wait <= 8000
    assert place(f"{{planted: false, reachable: true, waitedMs: {wait}}}") == "window"
    assert place("{planted: false, reachable: false, waitedMs: 0}") == "window"
    assert place("null") == "wait"


def test_the_window_has_the_fallback_entry_wired_to_the_same_sheet():
    html = read("index.html")
    panel = html[html.index('id="meetingsPanel"'):]
    panel = panel[:panel.index("</div>")]
    assert '<button type="button" class="btn btn-sec btn-sm mt-granola" id="granolaFallback" hidden>' \
           'Import from Granola</button>' in panel
    src = code_only(read("granola.js"))
    assert "$('granolaFallback').addEventListener('click', open)" in src
    assert "L.granolaEntryPlace({ planted, reachable" in src
    # Not found: shown in the window; planted later: hidden again.
    assert "fb.hidden = where !== 'window'" in src
    assert "if (!audio) { planted = false; place(true); return; }" in src
    assert "catch (e) { place(false); return; }" in src
    css = read("meetings.css")
    assert ".mt-granola[hidden] { display: none; }" in css


@needs_node
def test_with_the_frames_markup_changed_the_fallback_shows(tmp_path):
    """granola.js itself, in Node with a stand-in window and a frame whose
    "Import audio" button is gone: after the wait, the window's entry shows."""
    import json as _json
    import subprocess
    script = tmp_path / "run.js"
    script.write_text("""
const fs = require('fs');
const L = require(process.argv[3]);
const els = {};
function el(id) { return els[id] || (els[id] = { id, hidden: true, textContent: '', dataset: {}, style: {},
  classList: { toggle() {} }, addEventListener() {}, focus() {}, getAttribute(n) { return n === 'src' ? 'meetings-app/index.html' : null; } }); }
const frameDoc = { body: {}, getElementById() { return null; }, querySelectorAll() { return []; } };
els.mtFrame = Object.assign(el('mtFrame'), { contentDocument: frameDoc });
let ready = null, timers = [];
global.window = { WafflerLogic: L, setTimeout(fn) { timers.push(fn); return timers.length; }, clearTimeout() {} };
global.document = { getElementById: el, addEventListener(t, fn) { if (t === 'DOMContentLoaded') ready = fn; } };
global.MutationObserver = class { observe() {} };
global.console = { warn() {}, log() {} };
const realNow = Date.now; let now = 1000; Date.now = () => now;
eval(fs.readFileSync(process.argv[2], 'utf8'));
el('granolaSheet'); ready();
const before = els.granolaFallback.hidden;
now += L.GRANOLA_WAIT_MS + 1; timers.splice(0).forEach((f) => f());
process.stdout.write(JSON.stringify({ before, after: els.granolaFallback.hidden }));
""", encoding="utf-8")
    from test_ui_logic import NODE, LOGIC
    out = subprocess.run([NODE, str(script), str(ROOT / "ui" / "granola.js"), str(LOGIC)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    r = _json.loads(out.stdout)
    assert r == {"before": True, "after": False}
