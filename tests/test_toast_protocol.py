"""Toasts: buttons, how long they stay, and where they appear.

A toast carries up to three buttons ("Open Settings", "Copy again", "Dismiss")
as ``buttons=[{label, action, kind}]``. Two optional keys are new with safe
paste and the permission check: ``timeout`` (a toast with a button to click
needs longer than the usual six seconds) and ``reposition`` (a toast with no
recording behind it appears on the screen the cursor is on, not where the pill
last was). They are sent only when set, so every other toast is the message it
always was (tests/test_working_pill.py pins that).

The controller is tested directly. The two overlay processes cannot be started
here, so their pieces are lifted out of the source and run with plain values.
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

MAC = ROOT / "src" / "overlay_process.py"
WIN = ROOT / "src" / "overlay_process_windows.py"


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


def _sent(o):
    return o._process.stdin.lines


# ── the controller ───────────────────────────────────────────────────────

def test_a_plain_toast_is_the_message_it_always_was():
    o = _overlay()
    o.show_toast("error", "We couldn't hear you", "Check your mic.")
    assert _sent(o) == [{"type": "show_toast", "style": "error", "heading": "We couldn't hear you",
                         "body": "Check your mic."}]


def test_a_toast_with_buttons_a_timeout_and_a_screen_to_follow():
    o = _overlay()
    o.show_toast("error", "Microphone is off", "Turn it on.", buttons=[
        {"label": "Open Settings", "action": "open_mic_settings", "kind": "primary"},
        {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}],
        timeout=12, reposition=True)
    [msg] = _sent(o)
    assert msg["buttons"] == [
        {"label": "Open Settings", "action": "open_mic_settings", "kind": "primary"},
        {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}]
    assert msg["timeout"] == 12.0 and msg["reposition"] is True
    assert "primary" not in msg, "the old single-button field is gone"


def test_timeout_and_reposition_are_sent_only_when_set():
    o = _overlay()
    o.show_toast("warn", "h", "b")
    o.show_toast("warn", "h", "b", timeout=None, reposition=False)
    o.show_toast("warn", "h", "b", timeout=0)
    o.show_toast("warn", "h", "b", timeout=8)
    o.show_toast("warn", "h", "b", reposition=True)
    plain = [{"type": "show_toast", "style": "warn", "heading": "h", "body": "b"}] * 3
    assert _sent(o)[:3] == plain
    assert _sent(o)[3] == {**plain[0], "timeout": 8.0}
    assert _sent(o)[4] == {**plain[0], "reposition": True}


def test_buttons_are_cut_to_three_and_get_a_kind():
    o = _overlay()
    o.show_toast("warn", "h", "b", buttons=[{"label": "A", "action": "a"}, {"label": "B", "action": "b"},
                                            {"label": "C", "action": "c"}, {"label": "D", "action": "d"}])
    [msg] = _sent(o)
    assert [b["label"] for b in msg["buttons"]] == ["A", "B", "C"]
    assert all(b["kind"] == "secondary" for b in msg["buttons"])


def test_the_old_single_button_argument_is_not_accepted():
    with pytest.raises(TypeError):
        _overlay().show_toast("warn", "h", "b", primary={"label": "x", "action": "y"})


# ── the Mac overlay process ──────────────────────────────────────────────

def _lift(path, *names, **ns):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in names)
             or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in names for t in n.targets))]
    assert {getattr(n, "name", None) or n.targets[0].id for n in nodes} == set(names)
    ns.setdefault("sys", sys)
    exec(compile(ast.Module(nodes, []), str(path), "exec"), ns)
    return ns


def test_mac_a_toast_stays_its_styles_usual_time_unless_told_otherwise():
    ns = _lift(MAC, "_toast_seconds", "_TOAST_AUTO_HIDE_SECS")
    secs = ns["_toast_seconds"]
    assert secs("warn") == 6.0 and secs("error") == 6.0
    assert secs("cancel") is None and secs("info") is None, "these stay until answered"
    assert secs("error", 12) == 12.0
    for bad in (None, 0, -3, "", "soon", [], {}):
        assert secs("warn", bad) == 6.0, bad
    assert secs("cancel", 5) == 5.0


def test_mac_a_toast_with_no_recording_follows_the_cursors_screen_without_moving_the_pill():
    calls = []
    ns = _lift(MAC, "_toast_anchor", "_toast_origin",
               _waffle_x=100.0, _waffle_y=200.0, WAFFLE_W=96, WAFFLE_H=40, TOAST_W=320, TOAST_PAD=8,
               _compute_overlay_position=lambda verbose=False: calls.append(1) or (1500, 40))
    anchor, origin = ns["_toast_anchor"], ns["_toast_origin"]
    assert anchor(False) == (100.0, 200.0) and calls == [], "an ordinary toast stays by the pill"
    assert anchor(True) == (1500.0, 40.0), "a toast with nothing recorded uses the pill's screen choice"
    assert (ns["_waffle_x"], ns["_waffle_y"]) == (100.0, 200.0), \
        "the pill's own position is left alone: the next show compares against it"
    assert origin((1500.0, 40.0)) == (1500.0 + (96 - 320) // 2, 40.0 + 40 + 8)
    assert origin(None) == origin((100.0, 200.0))


def test_mac_an_anchor_that_cannot_be_worked_out_falls_back_to_the_pill():
    def boom(verbose=False):
        raise RuntimeError("no screen")
    ns = _lift(MAC, "_toast_anchor", "_waffle_x", "_waffle_y",
               _compute_overlay_position=boom)
    ns.update(_waffle_x=5.0, _waffle_y=6.0)
    assert ns["_toast_anchor"](True) == (5.0, 6.0)


def test_mac_the_show_toast_command_passes_the_new_keys_on():
    tree = ast.parse(MAC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_dispatch_cmd")
    src = ast.unparse(fn)
    branch = src[src.index("ctype == 'show_toast'"):src.index("ctype == 'hide_toast'")]
    assert "timeout=cmd.get('timeout')" in branch
    assert "anchor=_toast_anchor(True) if cmd.get('reposition') else None" in branch
    assert "buttons=cmd.get('buttons')" in branch
    show = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_show_toast")
    assert [a.arg for a in show.args.args] == ["style", "heading", "body", "buttons", "timeout", "anchor"]
    assert "_toast_origin(anchor)" in ast.unparse(show)
    assert "_toast_seconds(style, timeout)" in ast.unparse(show)


# ── the Windows overlay process ──────────────────────────────────────────

def test_windows_a_toast_stays_its_styles_usual_time_unless_told_otherwise():
    ns = _lift(WIN, "_toast_ms", "_TOAST_AUTO_HIDE_MS")
    ms = ns["_toast_ms"]
    assert ms("warn") == 9000 and ms("error") == 9000
    assert ms("cancel") is None
    assert ms("error", 12) == 12000
    for bad in (None, 0, -3, "soon"):
        assert ms("warn", bad) == 9000, bad


def test_windows_the_show_toast_command_passes_the_timeout_and_ignores_reposition():
    tree = ast.parse(WIN.read_text(encoding="utf-8"))
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_handle_cmd")
    src = ast.unparse(handler)
    branch = src[src.index("ctype == 'show_toast'"):src.index("ctype == 'hide_toast'")]
    assert "timeout=cmd.get('timeout')" in branch and "reposition" not in branch
    show = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_show_toast")
    assert [a.arg for a in show.args.args] == ["style", "heading", "body", "buttons", "timeout"]
    assert "_toast_ms(style, timeout)" in ast.unparse(show)


def test_both_overlay_processes_document_the_same_command():
    for path in (MAC, WIN):
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
        assert '"timeout": seconds, "reposition": true' in doc, path.name
        assert '"buttons": [{"label", "action", "kind"}, ...]' in doc, path.name
