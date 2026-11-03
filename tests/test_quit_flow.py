"""Every way of quitting Waffler ends the process, with one shutdown.

On 2026-10-29 ``osascript -e 'tell application "Waffler" to quit'`` left the
app running until it was sent SIGTERM. Two causes:

  * The overlay subprocess is the same executable (``Waffler --overlay``), so
    LaunchServices lists two "Waffler" processes with one bundle id and a quit
    Apple Event can land on the overlay. It obeyed and exited; the main app
    never heard of it.
  * When the event reaches the main app, AppKit asks
    ``applicationShouldTerminate:``, pywebview turns that into the window's
    ``closing`` event, and Waffler's handler only recognised a Cmd-Q key-down
    as a quit. Anything else (Apple Event, Dock > Quit, log out) was treated
    as the red close button: window hidden, quit cancelled.

These tests drive src/quit_flow.py with fakes, and read app.py and
src/overlay_process.py for the wiring, as the other app.py tests do.
"""
import ast
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import quit_flow  # noqa: E402

APP_PY = (ROOT / "app.py").read_text(encoding="utf-8")
OVERLAY_PROCESS_PY = (ROOT / "src" / "overlay_process.py").read_text(encoding="utf-8")
OVERLAY_PY = (ROOT / "src" / "overlay.py").read_text(encoding="utf-8")


def _lift(name, src=APP_PY):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} not found")


# ── the shutdown itself ──────────────────────────────────────────────────────

class FakeTimer:
    made = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.daemon, self.started = delay, fn, False, False
        FakeTimer.made.append(self)

    def start(self):
        self.started = True


def _controller(steps, exits=None, logs=None, grace_s=2.0):
    FakeTimer.made = []
    exits = [] if exits is None else exits
    logs = [] if logs is None else logs
    return quit_flow.QuitController(
        steps, hard_exit=exits.append, log=logs.append, grace_s=grace_s,
        timer=FakeTimer,
    ), exits, logs


def test_quit_runs_every_step_once_in_order():
    ran = []
    qc, _, _ = _controller([("a", lambda: ran.append("a")), ("b", lambda: ran.append("b"))])
    assert qc.quit("menu") is True
    assert ran == ["a", "b"]
    assert qc.quitting
    # A second request (Cmd-Q then the terminate it causes) does nothing more.
    assert qc.quit("apple-event") is False
    assert ran == ["a", "b"]


def test_a_failing_step_does_not_stop_the_others():
    ran = []

    def boom():
        raise RuntimeError("tray already gone")

    qc, _, logs = _controller([("tray", boom), ("window", lambda: ran.append("window"))])
    qc.quit("menu")
    assert ran == ["window"]
    assert any("tray" in line and "RuntimeError" in line for line in logs)


def test_the_process_is_ended_if_it_is_still_running_after_the_grace():
    qc, exits, logs = _controller([], grace_s=2.0)
    qc.quit("apple-event")
    (timer,) = FakeTimer.made
    assert timer.delay == 2.0
    # Daemon, so the backstop itself never keeps the interpreter alive.
    assert timer.daemon is True and timer.started
    assert exits == []
    timer.fn()
    assert exits == [0]
    assert any("still running" in line for line in logs)


def test_the_backstop_is_armed_before_the_steps_run():
    """A step that hangs (a wedged CoreAudio teardown) must not stop the exit."""
    seen = []
    qc, _, _ = _controller([("hang", lambda: seen.append(len(FakeTimer.made)))])
    qc.quit("menu")
    assert seen == [1]


def test_the_reason_is_logged():
    qc, _, logs = _controller([])
    qc.quit("Dock or AppleScript")
    assert any("[quit]" in line and "Dock or AppleScript" in line for line in logs)


def test_the_real_timer_thread_is_a_daemon():
    exits = []
    qc = quit_flow.QuitController([], hard_exit=exits.append, log=lambda m: None,
                                  grace_s=60)
    qc.quit("test")
    t = qc._watchdog
    try:
        assert isinstance(t, threading.Thread) and t.daemon
    finally:
        t.cancel()


# ── telling a quit from the red close button ─────────────────────────────────

def _app_should_terminate_(fn):
    """Call fn from inside a frame named like pywebview's delegate method."""
    def applicationShouldTerminate_():
        return fn()
    return applicationShouldTerminate_()


def _window_should_close_(fn):
    def windowShouldClose_():
        return fn()
    return windowShouldClose_()


def test_a_close_asked_by_applicationShouldTerminate_is_a_quit():
    assert _app_should_terminate_(quit_flow.inside_app_terminate) is True


def test_a_close_asked_by_the_window_is_not_a_quit():
    assert _window_should_close_(quit_flow.inside_app_terminate) is False
    assert quit_flow.inside_app_terminate() is False


def test_quit_reason_covers_every_route():
    r = quit_flow.quit_reason
    assert r(in_terminate=False, cmd_q=False, quit_event=False) is None   # red X / Cmd-W
    assert r(in_terminate=False, cmd_q=True, quit_event=False) == "Cmd-Q"
    assert r(in_terminate=False, cmd_q=False, quit_event=True) == "a quit Apple Event"
    # App menu Quit, Dock > Quit, log out: AppKit is terminating.
    assert r(in_terminate=True, cmd_q=False, quit_event=False) == "the app is terminating"
    assert r(in_terminate=True, cmd_q=True, quit_event=False) == "Cmd-Q"


def test_quit_apple_event_codes():
    assert quit_flow.is_quit_apple_event(quit_flow.K_CORE_EVENT_CLASS,
                                         quit_flow.K_AE_QUIT_APPLICATION)
    assert quit_flow.K_CORE_EVENT_CLASS == int.from_bytes(b"aevt", "big")
    assert quit_flow.K_AE_QUIT_APPLICATION == int.from_bytes(b"quit", "big")
    assert not quit_flow.is_quit_apple_event(quit_flow.K_CORE_EVENT_CLASS,
                                             int.from_bytes(b"oapp", "big"))
    assert not quit_flow.is_quit_apple_event(None, None)


# ── the overlay passes a quit it receives to the app ─────────────────────────

def test_overlay_forwards_an_outside_quit_to_the_app():
    d = quit_flow.overlay_terminate_decision
    # The app told it to quit (or closed its stdin): go.
    assert d(parent_asked=True, now=10.0, last_forward=None) == "terminate"
    # A quit from outside (AppleScript, Dock): ask the app, stay up.
    assert d(parent_asked=False, now=10.0, last_forward=None) == "forward"
    # Asked again soon after: still the app's job.
    assert d(parent_asked=False, now=11.0, last_forward=10.0) == "forward"
    # The app did nothing for a while (hung): the overlay goes anyway.
    late = 10.0 + quit_flow.OVERLAY_FORWARD_PATIENCE_S + 0.1
    assert d(parent_asked=False, now=late, last_forward=10.0) == "terminate"


def test_overlay_process_installs_the_forwarding_delegate():
    src = OVERLAY_PROCESS_PY
    assert "def applicationShouldTerminate_(self, _app)" in src
    assert "overlay_terminate_decision(" in src
    assert 'emit("quit_request")' in src
    assert re.search(r"app\.setDelegate_\(\s*_app_delegate\s*\)", src)
    # The app's own quit command and stdin EOF are allowed through.
    quit_branch = src[src.index('elif ctype == "quit":'):]
    quit_branch = quit_branch[:quit_branch.index("terminate_(None)")]
    assert "_parent_asked_quit = True" in quit_branch


def test_overlay_manager_hands_quit_request_to_the_app():
    from overlay import RecordingOverlay

    class FakeChild:
        stdout = ['{"event": "ready"}\n', '{"event": "quit_request"}\n']

    asked = []
    o = RecordingOverlay(on_quit_request=lambda: asked.append(True))
    o._log = lambda msg: None
    o._read_stdout(FakeChild())
    assert asked == [True]
    # Without the callback (an older caller) the event is ignored.
    o2 = RecordingOverlay()
    o2._log = lambda msg: None
    o2._read_stdout(FakeChild())


# ── app.py routes every quit through one shutdown ────────────────────────────

def test_window_closing_uses_the_shared_quit_for_every_real_quit():
    closing = _lift("_on_window_closing")
    assert "quit_reason(" in closing
    assert "inside_app_terminate()" in closing
    assert "_quit_app(" in closing
    # The hide path is still there for the red close button.
    assert "_window_ref.hide()" in closing


def test_menu_quit_and_tray_quit_use_the_shared_quit():
    assert "_quit_app(" in _lift("_tray_quit")


def test_quit_app_is_built_on_quit_controller():
    quit_app = _lift("_quit_app")
    assert "QuitController" in APP_PY
    assert "_quit_controller" in quit_app
    assert "_should_quit = True" in APP_PY[APP_PY.index("def _quit_steps"):]


def _steps_on(system):
    """app._quit_steps run against fakes, as on ``system``."""
    did = []

    class Thing:
        def __init__(self, name):
            self.name = name

        def stop(self):
            did.append(f"{self.name}.stop")

        def destroy(self):
            did.append(f"{self.name}.destroy")

    class Platform:
        @staticmethod
        def system():
            return system

    ns = {"_platform": Platform, "_tray_icon": Thing("tray"),
          "_window_ref": Thing("window"), "_should_quit": False}
    exec(_lift("_quit_steps"), ns)
    for _name, fn in ns["_quit_steps"]():
        fn()
    return did, ns


def test_windows_tray_quit_still_stops_the_tray_and_closes_the_window():
    did, ns = _steps_on("Windows")
    assert did == ["tray.stop", "window.destroy"]
    assert ns["_should_quit"] is True


def test_the_pipeline_overlay_reports_quit_requests_to_the_app():
    assert re.search(r"on_quit_request\s*=\s*_on_overlay_quit_request", APP_PY)


def test_mac_menu_quit_goes_through_appkit_terminate():
    """So the menu bar's Quit ends the process the same way Cmd-Q does,
    instead of waiting for webview.start() to return and Python to wind down."""
    body = _lift("_quit_steps")
    assert "terminate_" in body


# ── no long-lived thread keeps the interpreter alive ─────────────────────────

def _thread_calls(path):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in ("Thread", "Timer"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        daemon = kw.get("daemon")
        if isinstance(daemon, ast.Constant) and daemon.value is True:
            continue
        # Timer has no daemon argument: `t.daemon = True` must follow within
        # a few lines, before it is started.
        following = "\n".join(lines[node.lineno - 1:node.lineno + 5])
        if re.search(r"\.daemon\s*=\s*True", following):
            continue
        yield f"{path.relative_to(ROOT)}:{node.lineno}"


# Threads that are deliberately not daemons, each joined by its owner. Empty
# today; anything added here needs the join that ends it named alongside.
_JOINED_THREADS = set()


def test_every_thread_waffler_starts_is_a_daemon():
    files = [ROOT / "app.py"] + sorted((ROOT / "src").glob("*.py"))
    offenders = [hit for f in files for hit in _thread_calls(f)
                 if hit not in _JOINED_THREADS]
    assert offenders == [], (
        "A non-daemon thread keeps Python alive after the window closes, so "
        "Quit appears to do nothing. Pass daemon=True or join it on quit: "
        + ", ".join(offenders))


def test_no_thread_pool_executor():
    """ThreadPoolExecutor workers are joined at interpreter exit."""
    files = [ROOT / "app.py"] + sorted((ROOT / "src").glob("*.py"))
    for f in files:
        assert "ThreadPoolExecutor(" not in f.read_text(encoding="utf-8"), f.name
