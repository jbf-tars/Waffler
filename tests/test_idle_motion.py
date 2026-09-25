"""Nothing in the window animates while Waffler sits idle.

Idle, Waffler used about 11% of one core, almost all of it in the WebView2
GPU process, which was redrawing two endless CSS animations on the Journal:
the status dot pulsing even at "Ready", and the streak logo's wobble with a
drop-shadow. Headless Chrome drew 300 frames in 5 seconds on the idle
Journal; with these rules it draws none.

These checks read ui/style.css, ui/app.js and app.py; the frame count itself
is measured in the audit harness, not here.
"""
import ast
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


def _code(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


CSS = _code((UI / "style.css").read_text(encoding="utf-8"))
APP_JS = _code((UI / "app.js").read_text(encoding="utf-8"))
APP_PY = (ROOT / "app.py").read_text(encoding="utf-8")


def rules(css=CSS):
    """(selector, body) for every rule, including rules inside @media."""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel = m.group(1).strip()
        if sel.startswith("@"):
            sel = sel.split("{")[-1].strip()
        out.append((" ".join(sel.split()), m.group(2)))
    return out


def media_block(query):
    i = CSS.index(query)
    j = CSS.index("{", i)
    depth, k = 0, j
    while True:
        if CSS[k] == "{":
            depth += 1
        elif CSS[k] == "}":
            depth -= 1
            if depth == 0:
                return CSS[j + 1:k]
        k += 1


# ── the two animations that ran at idle ──────────────────────────────────────

def test_the_status_dot_only_pulses_while_recording_or_cleaning_up():
    for sel, body in rules():
        if "j-listen-dot" not in sel or "animation" not in body or "none" in body:
            continue
        for part in sel.split(","):
            if "j-listen-dot" in part:
                assert re.search(r"\.(listening|recording|processing)\b", part), \
                    f"'{part.strip()}' animates the status dot outside recording"


def test_the_streak_logo_wobbles_once_not_forever():
    bodies = [body for sel, body in rules() if sel == ".j-stack-svg"]
    assert bodies
    for body in bodies:
        anim = re.search(r"animation\s*:([^;]+);", body)
        if anim:
            assert "infinite" not in anim.group(1)


# ── pause while hidden, and reduced motion ───────────────────────────────────

def test_everything_pauses_under_waffler_hidden():
    found = [body for sel, body in rules() if sel.startswith("html.waffler-hidden *")]
    assert found and all("animation-play-state: paused !important" in b for b in found)


def test_one_reduced_motion_rule_covers_every_element():
    block = media_block("@media (prefers-reduced-motion: reduce) {\n  *, *::before")
    star = [body for sel, body in rules(block) if sel == "*, *::before, *::after"]
    assert star, "no global reduced-motion rule"
    body = star[0]
    for decl in ("animation-duration: 0.01ms !important", "animation-iteration-count: 1 !important",
                 "transition-duration: 0.01ms !important"):
        assert decl in body
    # The macOS walkthrough is drawn by its animation; without one it would
    # be blank, so the final frame is set by hand.
    assert ".vb-phase-list" in block and ".vb-cursor" in block


def test_the_page_tracks_its_own_visibility_and_listens_to_app_py():
    assert "addEventListener('visibilitychange'" in APP_JS
    assert "window.waffler_window_visible = function" in APP_JS
    assert "classList.toggle('waffler-hidden'" in APP_JS


def test_the_try_it_waffle_stops_when_it_is_off_screen():
    hide = APP_JS[APP_JS.index("function hideWizard()"):]
    assert "wizStopExplainerWaffle()" in hide[:hide.index("\n}")]
    show = APP_JS[APP_JS.index("function wizShowStep("):]
    show = show[:show.index("\nfunction ")]
    assert "else wizStopExplainerWaffle();" in show


# ── app.py tells the page about hide, minimise and restore ───────────────────

def _lift(name):
    tree = ast.parse(APP_PY)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(APP_PY, fn)


class FakeWindow:
    def __init__(self, fail=False):
        self.sent, self.threads, self.fail = [], [], fail

    def evaluate_js(self, js):
        self.threads.append(threading.current_thread().name)
        self.sent.append(js)
        if self.fail:
            raise RuntimeError("window gone")


def _notify(window):
    ns = {"threading": threading, "_window": window}
    exec(_lift("notify_js_window_visible"), ns)
    return ns["notify_js_window_visible"]


def _wait(cond):
    t = time.time() + 5
    while not cond() and time.time() < t:
        time.sleep(0.01)


def test_notify_sends_the_state_from_another_thread():
    w = FakeWindow()
    notify = _notify(w)
    notify(False)
    notify(True)
    _wait(lambda: len(w.sent) == 2)
    assert sorted(w.sent) == sorted([
        "window.waffler_window_visible && window.waffler_window_visible(false)",
        "window.waffler_window_visible && window.waffler_window_visible(true)",
    ])
    assert threading.main_thread().name not in w.threads


def test_notify_never_raises():
    _notify(None)(False)          # no window yet
    w = FakeWindow(fail=True)
    _notify(w)(True)              # window already closed
    _wait(lambda: w.sent)
    assert w.sent


def test_hide_minimise_and_restore_are_all_reported():
    closing = _lift("_on_window_closing")
    assert closing.index("_window_ref.hide()") < closing.index("notify_js_window_visible(False)")
    assert "notify_js_window_visible(True)" in _lift("_tray_show_window")
    main = _lift("main")
    assert "window.events.minimized += _on_minimized" in main
    assert "window.events.restored += _on_restored" in main
    # pywebview passes arguments to handlers that declare parameters; these
    # declare none, so they are called as they are.
    assert re.search(r"def _on_minimized\(\):\s+notify_js_window_visible\(False\)", main)
    assert re.search(r"def _on_restored\(\):\s+notify_js_window_visible\(True\)", main)
