"""The pill stays up after release, shows it is working, and ends clearly.

On release the pill used to be hidden at once, and the "Transcribing" label
was then drawn on the hidden pill, so nothing showed that a dictation was
being processed. Now the pill stays in a working look (a slow ripple, the
elapsed time and one X that cancels) and ends with a short tick, or quietly
when a message is showing. The same commands on Windows (Tk) and Mac
(PyObjC); the Mac file cannot be imported here, so its pieces are compared
by source. No overlay process is launched.
"""
import ast
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from overlay import RecordingOverlay  # noqa: E402


# ── the controller (src/overlay.py) ──────────────────────────────────────────

class _Stdin:
    def __init__(self):
        self.lines = []

    def write(self, data):
        self.lines.extend(json.loads(x) for x in data.splitlines() if x.strip())

    def flush(self):
        pass


class _Proc:
    def __init__(self):
        self.stdin = _Stdin()

    def poll(self):
        return None


def _overlay():
    o = object.__new__(RecordingOverlay)
    o._process = _Proc()
    o._send_lock = threading.Lock()
    o._visible = False
    o._log = lambda *a, **k: None
    return o


def test_working_keeps_the_pill_visible_and_carries_the_elapsed_time():
    o = _overlay()
    assert o.show_working(3.7, reliable=True)
    assert o._process.stdin.lines == [{"type": "working", "elapsed_seconds": 3.7}]
    assert o._visible is True


def test_a_busy_overlay_drops_a_working_frame_rather_than_wait():
    o = _overlay()
    o._send_lock.acquire()
    try:
        assert o.show_working(4.0) is False
    finally:
        o._send_lock.release()


def test_ending_work_says_done_or_quiet_and_nothing_else():
    o = _overlay()
    o._visible = True
    o.end_working("done")
    o.end_working("quiet")
    o.end_working("anything else")
    assert [c["result"] for c in o._process.stdin.lines] == ["done", "quiet", "quiet"]
    assert o._visible is False


def test_toasts_can_carry_up_to_three_buttons_and_be_withdrawn_by_style():
    o = _overlay()
    o.show_toast("info", "Still working on it", "Slow.", buttons=[
        {"label": "Keep waiting", "action": "keep_waiting", "kind": "primary"},
        {"label": "Send later", "action": "send_later"},
        {"label": "Cancel", "action": "cancel_processing", "kind": "danger"},
        {"label": "Extra", "action": "x"},
    ])
    o.hide_toast(style="info")
    o.hide_toast()
    toast, hide_info, hide_any = o._process.stdin.lines
    assert [b["label"] for b in toast["buttons"]] == ["Keep waiting", "Send later", "Cancel"]
    assert toast["buttons"][1]["kind"] == "secondary"
    assert hide_info == {"type": "hide_toast", "style": "info"}
    assert hide_any == {"type": "hide_toast"}


def test_a_toast_without_buttons_is_sent_as_before():
    o = _overlay()
    o.show_toast("warn", "Heading", "Body")
    assert o._process.stdin.lines == [
        {"type": "show_toast", "style": "warn", "heading": "Heading", "body": "Body"}]


# ── both overlay processes speak the same commands ──────────────────────────

WIN = ROOT / "src" / "overlay_process_windows.py"
MAC = ROOT / "src" / "overlay_process.py"


def _handled_types(path, func):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
    out = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "ctype"):
            for c in node.comparators:
                if isinstance(c, ast.Constant):
                    out.add(c.value)
    return out


def test_both_platforms_handle_the_working_commands():
    win = _handled_types(WIN, "_handle_cmd")
    mac = _handled_types(MAC, "_dispatch_cmd")
    for cmd in ("show", "hide", "level", "working", "working_end", "show_toast", "hide_toast"):
        assert cmd in win, f"Windows overlay does not handle {cmd}"
        assert cmd in mac, f"Mac overlay does not handle {cmd}"


def _lift(path, *names):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in names)
             or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in
                 ("GRID_ROWS", "GRID_COLS"))]
    import math
    ns = {"math": math}
    exec(compile(ast.Module(nodes, []), str(path), "exec"), ns)
    return ns


def test_the_ripple_and_the_elapsed_time_match_on_both_platforms():
    win = _lift(WIN, "working_targets", "elapsed_text")
    mac = _lift(MAC, "working_targets", "elapsed_text")
    for t in (0.0, 1.3, 1000.0):
        a, b = win["working_targets"](t), mac["working_targets"](t)
        assert a == b and len(a) == 16
        assert all(0.15 <= v <= 0.85 for v in a), "the ripple should never look empty or full"
    for s in (0, 5, 59, 60, 125):
        assert win["elapsed_text"](s) == mac["elapsed_text"](s)
    import pipeline_watchdog as pw
    assert all(win["elapsed_text"](s) == pw.elapsed_label(s) for s in (0, 9, 61, 3600))


def test_both_platforms_use_the_same_toast_button_colours():
    def kinds(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.Assign)
                    and getattr(n.targets[0], "id", "") == "TOAST_BTN_KINDS")
        return ast.literal_eval(node.value)
    assert kinds(WIN) == kinds(MAC)


# ── the Windows overlay, for real (Tk) ───────────────────────────────────────

@pytest.fixture(scope="module")
def _tk_root():
    """One Tk root for the module: creating many in one process makes Tcl
    fail to read its own library files now and then on Windows."""
    if sys.platform != "win32":
        pytest.skip("overlay_process_windows is the Windows overlay")
    import overlay_process_windows as ow
    try:
        root = ow.tk.Tk()
    except Exception as e:           # no display, or Tcl not usable
        pytest.skip(f"Tk unavailable: {e}")
    root.overrideredirect(True)
    root.geometry(f"{ow.WIN_W}x{ow.WIN_H}+-3000+-3000")
    canvas = ow.tk.Canvas(root, width=ow.WIN_W, height=ow.WIN_H, highlightthickness=0)
    canvas.pack()
    yield ow, root, canvas
    root.destroy()


@pytest.fixture
def tk_overlay(_tk_root):
    ow, root, canvas = _tk_root
    saved = (ow._root, ow._canvas, ow._visible, ow._mode, ow._toast_win)
    ow._root, ow._canvas = root, canvas
    ow._visible, ow._mode, ow._toast_win = False, "recording", None
    emitted = []
    real_emit = ow.emit
    ow.emit = lambda event, **k: emitted.append(event)
    try:
        yield ow, root, canvas, emitted
    finally:
        ow.emit = real_emit
        ow._hide_toast()
        ow._cancel_done_timer()
        ow._root, ow._canvas, ow._visible, ow._mode, ow._toast_win = saved


class _Click:
    def __init__(self, x, y):
        self.x, self.y = x, y


def test_windows_pill_after_release_ripples_shows_the_time_and_one_x(tk_overlay):
    ow, root, canvas, emitted = tk_overlay
    ow._handle_cmd({"type": "show"})
    ow._handle_cmd({"type": "working", "elapsed_seconds": 7.4})
    assert ow._mode == "working" and ow._visible
    texts = [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]
    assert texts == ["7s"]
    # Late VU frames after release are ignored.
    before = list(ow._targets)
    ow._handle_cmd({"type": "level", "value": 0.9})
    assert ow._targets == before
    # The only button is the X in the middle, and it cancels.
    ow._on_click(_Click(ow.BTN_WORK_CX, ow.BTN_CANCEL_CY))
    ow._on_click(_Click(ow.BTN_STOP_CX, ow.BTN_STOP_CY))
    assert emitted == ["cancel_request"]


def test_windows_pill_ends_with_a_tick_then_hides(tk_overlay):
    ow, root, canvas, emitted = tk_overlay
    ow._handle_cmd({"type": "show"})
    ow._handle_cmd({"type": "working", "elapsed_seconds": 2})
    ow._handle_cmd({"type": "working_end", "result": "done"})
    assert ow._mode == "done" and ow._visible
    assert all(v == 1.0 for v in ow._bars)
    ow._finish_done()
    assert ow._mode == "recording" and not ow._visible


def test_windows_pill_skips_the_tick_when_a_message_is_up_and_leaves_the_message(tk_overlay):
    ow, root, canvas, emitted = tk_overlay
    ow._handle_cmd({"type": "show"})
    ow._handle_cmd({"type": "working", "elapsed_seconds": 2})
    ow._handle_cmd({"type": "show_toast", "style": "warn", "heading": "Not pasted", "body": "b"})
    ow._handle_cmd({"type": "working_end", "result": "done"})
    assert ow._mode == "recording" and not ow._visible
    assert ow._toast_win is not None, "ending the work must not close the message"
    # Withdrawing an offer ("info") leaves a different message alone.
    ow._handle_cmd({"type": "hide_toast", "style": "info"})
    assert ow._toast_win is not None


def test_windows_offer_toast_shows_its_own_buttons(tk_overlay):
    ow, root, canvas, emitted = tk_overlay
    ow._handle_cmd({"type": "working", "elapsed_seconds": 9})
    ow._show_toast("info", "Still working on it", "Slow.", buttons=[
        {"label": "Keep waiting", "action": "keep_waiting", "kind": "primary"},
        {"label": "Send later", "action": "send_later", "kind": "secondary"},
        {"label": "Cancel", "action": "cancel_processing", "kind": "danger"},
    ])
    c = ow._toast_win.winfo_children()[0]
    labels = [c.itemcget(i, "text") for i in c.find_all() if c.type(i) == "text"]
    assert labels[-3:] == ["Keep waiting", "Send later", "Cancel"]
    assert "Select mic" not in labels
