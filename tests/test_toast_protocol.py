"""Messages on the wire: the small pill, and where it appears.

Every message is one small pill now (src/pill.py). The app still calls
``show_toast(style, heading, body, buttons, timeout, reposition)`` with the
old card's words; the controller works the pill out and sends it ready made:

    {"type": "show_toast", "style": ..., "pill": {kind, text, button, close,
     click, seconds, description}}

``reposition`` (a message with no recording behind it appears on the screen
the cursor is on, not where the dictation pill last was) is still sent only
when set. The old card's long sentence travels as the pill's description.

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

def test_a_message_is_sent_as_its_pill():
    o = _overlay()
    o.show_toast("error", "We couldn't hear you", "Check your mic.")
    assert _sent(o) == [{"type": "show_toast", "style": "error", "pill": {
        "kind": "problem", "text": "Didn\u2019t hear you",
        "button": {"label": "Mic", "action": "select_mic"}, "close": "dismiss", "click": None,
        "seconds": None, "description": "We couldn't hear you. Check your mic."}}]


def test_the_old_cards_words_and_buttons_do_not_travel_only_the_pill_does():
    o = _overlay()
    o.show_toast("error", "Microphone is off", "Turn it on.", buttons=[
        {"label": "Open Settings", "action": "open_mic_settings", "kind": "primary"},
        {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}],
        timeout=12, reposition=True)
    [msg] = _sent(o)
    assert set(msg) == {"type", "style", "pill", "reposition"}
    assert msg["pill"]["button"] == {"label": "Allow", "action": "open_mic_settings"}, \
        "one button: the one that fixes it, with the old button's action"
    assert msg["pill"]["close"] == "dismiss", "the old Dismiss is the cross"
    assert msg["pill"]["seconds"] is None, "it has a button, so it stays until that or the cross"
    assert msg["pill"]["description"] == "Microphone is off. Turn it on."
    assert msg["reposition"] is True


def test_reposition_is_sent_only_when_set():
    o = _overlay()
    o.show_toast("warn", "h", "b")
    o.show_toast("warn", "h", "b", timeout=None, reposition=False)
    o.show_toast("warn", "h", "b", reposition=True)
    assert all("reposition" not in m for m in _sent(o)[:2])
    assert _sent(o)[2]["reposition"] is True


def test_the_callers_time_is_kept_only_where_the_kind_keeps_it():
    o = _overlay()
    o.show_toast("warn", "Connection failed", "b", timeout=30)        # a Problem: six seconds
    o.show_toast("info", "Polish done", "b", timeout=8)               # Done: two
    o.show_toast("info", "Don't tap, hold", "Hold fn while you speak, then let go.", timeout=10)
    assert [m["pill"]["seconds"] for m in _sent(o)] == [6.0, 2.0, 10.0]


def test_three_buttons_become_one_and_a_cross():
    o = _overlay()
    o.show_toast("info", "Still working on it", "b", buttons=[
        {"label": "Keep waiting", "action": "keep_waiting", "kind": "primary"},
        {"label": "Send later", "action": "send_later", "kind": "secondary"},
        {"label": "Cancel", "action": "cancel_processing", "kind": "danger"}])
    [msg] = _sent(o)
    assert msg["pill"]["button"] == {"label": "Send later", "action": "send_later"}
    assert msg["pill"]["close"] == "cancel_processing"
    assert "keep_waiting" not in json.dumps(msg), "keeping waiting is what doing nothing does"


def test_a_message_that_is_no_longer_a_pop_up_is_not_sent():
    o = _overlay()
    o.show_toast("info", "Used OpenAI instead", "Groq can't be reached from this network.", timeout=8)
    assert _sent(o) == []


def test_the_long_sentence_is_logged_for_warnings_and_never_for_what_the_person_named():
    o = _overlay()
    lines = []
    o._log = lines.append
    o.show_toast("warn", "Connection failed", "Check your internet or VPN.")
    o.show_toast("info", "Siobhan's AirPods detected", "Use it for dictation?",
                 buttons=[{"label": "Use it", "action": "mic_use:1", "kind": "primary"}])
    text = "\n".join(lines)
    assert "Connection failed. Check your internet or VPN." in text
    assert "pill=problem 'No connection'" in text
    assert "Siobhan" not in text and "pill=ask" in text


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


def _pill_module():
    import pill
    return pill


def test_mac_a_pill_stays_its_own_time_and_a_working_one_is_capped():
    pl = _pill_module()
    ns = _lift(MAC, "_toast_seconds", _pl=pl)
    secs = ns["_toast_seconds"]
    assert secs(pl.pill_for("warn", "Connection failed", "b")) == 6.0
    assert secs(pl.pill_for("info", "Polish done", "b")) == 2.0
    assert secs(pl.pill_for("info", "Still working on it", "b")) is None, "it stays until answered"
    assert secs(pl.pill_for("error", "We couldn't hear you", "b")) is None, "it has a button"
    assert secs(pl.pill_for("info", "Waffler is busy", "b")) == pl.WORKING_CAP_S


def test_mac_a_toast_with_no_recording_follows_the_cursors_screen_without_moving_the_pill():
    pl = _pill_module()
    calls = []
    ns = _lift(MAC, "_toast_anchor", "_toast_origin",
               _waffle_x=100.0, _waffle_y=200.0, WIN_W=69, WIN_H=97, TOAST_H=40, TOAST_PAD=8, _pl=pl,
               _saved_positions={},     # nothing dragged (src/idle_pill.py): toasts as before
               _compute_overlay_position=lambda verbose=False: calls.append(1) or (1500, 40))
    anchor, origin = ns["_toast_anchor"], ns["_toast_origin"]
    assert anchor(False) == (100.0, 200.0) and calls == [], "an ordinary toast stays by the pill"
    assert anchor(True) == (1500.0, 40.0), "a toast with nothing recorded uses the pill's screen choice"
    assert (ns["_waffle_x"], ns["_waffle_y"]) == (100.0, 200.0), \
        "the pill's own position is left alone: the next show compares against it"
    # Centred over the dictation pill, a small gap above the whole of its
    # window (waffle and buttons), so neither covers the other.
    assert origin((1500.0, 40.0), 160) == (1500.0 + (69 - 160) / 2.0, 40.0 + 97 + 8)
    assert origin(None, 160) == origin((100.0, 200.0), 160)


def test_mac_an_anchor_that_cannot_be_worked_out_falls_back_to_the_pill():
    def boom(verbose=False):
        raise RuntimeError("no screen")
    ns = _lift(MAC, "_toast_anchor", "_waffle_x", "_waffle_y",
               _compute_overlay_position=boom)
    ns.update(_waffle_x=5.0, _waffle_y=6.0)
    assert ns["_toast_anchor"](True) == (5.0, 6.0)


def test_mac_the_show_toast_command_passes_the_pill_on():
    tree = ast.parse(MAC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_dispatch_cmd")
    src = ast.unparse(fn)
    branch = src[src.index("ctype == 'show_toast'"):src.index("ctype == 'hide_toast'")]
    assert "pill=cmd.get('pill')" in branch
    assert "anchor=_toast_anchor(True) if cmd.get('reposition') else None" in branch
    # The old card's fields are still read, for a command that carries no
    # pill: it becomes one by the same table, never the old card.
    assert "timeout=cmd.get('timeout')" in branch and "buttons=cmd.get('buttons')" in branch
    show = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_show_toast")
    assert [a.arg for a in show.args.args] == ["style", "heading", "body", "buttons", "timeout",
                                               "anchor", "pill"]
    body = ast.unparse(show)
    assert "_pl.from_dict(pill) or _pl.pill_for(style, heading, body, buttons, timeout)" in body
    put = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_put_pill"))
    assert "_toast_origin(anchor, w)" in put and "_toast_seconds(pill)" in put


def test_mac_one_pill_at_a_time():
    """There is one window for it. A second pill takes the first one's
    window; nothing ever makes another while one is up."""
    tree = ast.parse(MAC.read_text(encoding="utf-8"))
    put = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_put_pill"))
    assert put.count("_new_overlay_window(") == 1
    assert "replacing = _toast_win is not None" in put
    assert put.index("if not replacing:") < put.index("_new_overlay_window(")
    makers = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)
              and "MessagePillView.alloc()" in ast.unparse(n)]
    assert makers == ["_put_pill"]


def test_mac_the_pill_never_takes_the_keyboard_and_never_hides_the_dictation_pill():
    src = MAC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in ("_put_pill", "_hide_toast", "_show_toast"):
        assert "makeKeyAndOrderFront" not in fns[name], name
        assert "_g_window" not in fns[name], f"{name} must leave the dictation pill alone"
    assert "orderFrontRegardless()" in fns["_put_pill"]


def test_mac_reduce_motion_and_voiceover():
    tree = ast.parse(MAC.read_text(encoding="utf-8"))
    fns = {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "accessibilityDisplayShouldReduceMotion" in fns["_reduce_motion"]
    put = fns["_put_pill"]
    assert "still = _reduce_motion()" in put and "if replacing or still:" in put, \
        "no rise and no fade: it is simply there"
    assert "if _reduce_motion():\n        win.orderOut_(None)\n        return" in fns["_hide_toast"]
    assert "_announce(pill)" in put
    announce = fns["_announce"]
    assert "pill.kind not in (_pl.PROBLEM, _pl.ASK)" in announce
    assert "NSAccessibilityAnnouncementRequestedNotification" in announce


# ── the Windows overlay process ──────────────────────────────────────────

def test_windows_a_pill_stays_its_own_time_and_a_working_one_is_capped():
    pl = _pill_module()
    ns = _lift(WIN, "_toast_ms", _pl=pl)
    ms = ns["_toast_ms"]
    assert ms(pl.pill_for("warn", "Connection failed", "b")) == 6000
    assert ms(pl.pill_for("info", "Polish done", "b")) == 2000
    assert ms(pl.pill_for("info", "Still working on it", "b")) is None
    assert ms(pl.pill_for("error", "We couldn't hear you", "b")) is None
    assert ms(pl.pill_for("info", "Waffler is busy", "b")) == int(pl.WORKING_CAP_S * 1000)


def test_windows_the_show_toast_command_passes_the_pill_and_ignores_reposition():
    tree = ast.parse(WIN.read_text(encoding="utf-8"))
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_handle_cmd")
    src = ast.unparse(handler)
    branch = src[src.index("ctype == 'show_toast'"):src.index("ctype == 'hide_toast'")]
    assert "pill=cmd.get('pill')" in branch and "reposition" not in branch
    assert "timeout=cmd.get('timeout')" in branch and "buttons=cmd.get('buttons')" in branch
    assert "withdraw" not in branch, "the dictation pill is no longer hidden for a message"
    show = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_show_toast")
    assert [a.arg for a in show.args.args] == ["style", "heading", "body", "buttons", "timeout", "pill"]
    assert "_pl.from_dict(pill) or _pl.pill_for(style, heading, body, buttons, timeout)" in ast.unparse(show)
    put = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_put_pill"))
    assert "_toast_ms(pill)" in put and "_toast_origin(tw)" in put


def test_windows_one_pill_at_a_time_and_it_never_hides_the_dictation_pill():
    tree = ast.parse(WIN.read_text(encoding="utf-8"))
    fns = {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    put = fns["_put_pill"]
    assert put.count("tk.Toplevel(") == 1
    assert put.index("if not replacing:") < put.index("tk.Toplevel(")
    assert [name for name, src in fns.items() if "tk.Toplevel(" in src] == ["_put_pill"]
    for name in ("_put_pill", "_hide_toast", "_show_toast"):
        assert "_root.withdraw" not in fns[name] and "_root.deiconify" not in fns[name], name
    assert "_no_activate(_toast_win)" in put, "a click on it must leave the user's app in front"


def test_windows_the_pill_sits_above_the_dictation_pill_with_the_same_gap_as_the_mac():
    pl = _pill_module()
    ns = _lift(WIN, "_toast_origin", _waffle_x=600, _waffle_y=900, WAFFLE_W=69, TOAST_H=40,
               TOAST_PAD=pl.GAP)
    x, y = ns["_toast_origin"](160)
    assert (x, y) == (600 + (69 - 160) // 2, 900 - 40 - 8)
    assert y + 40 + pl.GAP == 900, "its bottom edge is one gap above the dictation pill's top"


class _Canvas:
    """A canvas that only writes down what was drawn on it."""

    def __init__(self):
        self.items = []

    def delete(self, what):
        self.items = []

    def _add(self, kind, coords, kw):
        self.items.append((kind, tuple(coords), kw))
        return len(self.items)

    def create_polygon(self, pts, **kw):
        return self._add("polygon", pts, kw)

    def create_rectangle(self, *c, **kw):
        return self._add("rectangle", c, kw)

    def create_line(self, *c, **kw):
        return self._add("line", c, kw)

    def create_oval(self, *c, **kw):
        return self._add("oval", c, kw)

    def create_text(self, *c, **kw):
        return self._add("text", c, kw)


def _windows_drawing():
    pl = _pill_module()
    return pl, _lift(WIN, "_draw_pill", "_draw_waffle_mark", "_rounded_rect", "_blend", "_pill_font",
                     "PILL_FONT", "PILL_FONT_SEMIBOLD", "WAFFLE_BODY", "WAFFLE_RIM", "CELL_BG",
                     "GRID_ROWS", "GRID_COLS", _pl=pl)


def _drawn(old, phase=0):
    pl, ns = _windows_drawing()
    pill = pl.pill_for(*old)
    lay = pl.layout(pill, lambda text, pts, weight: 7 * len(text))
    c = _Canvas()
    ns["_draw_pill"](c, pill, lay, phase)
    return pl, pill, lay, c.items


def _texts(items):
    return [(kw["text"], kw["fill"], kw["font"]) for kind, _c, kw in items if kind == "text"]


def _ovals(items):
    return [(c, kw["fill"]) for kind, c, kw in items if kind == "oval"]


def test_windows_draws_the_same_capsule_colours_and_sizes():
    pl, pill, lay, items = _drawn(("info", "Polish done", "b", None, None))
    kind, pts, kw = items[0]
    assert kind == "polygon" and kw["fill"] == "#14110F"
    assert kw["outline"] == "#353231", "white at 14% on the fill, as one colour"
    assert max(pts[1::2]) == 39 and max(pts[0::2]) == int(lay.width) - 1, "40 high, as wide as its words"
    assert _texts(items) == [("Polished", "#F4F0EA", ("Segoe UI", -14))]
    # The waffle mark: the body and sixteen pockets, in 22 pixels.
    assert items[1][2]["fill"] == "#D4A843" and len([i for i in items if i[0] == "rectangle"]) == 16


def test_windows_the_tick_is_a_cream_badge_on_the_marks_corner():
    pl, pill, lay, items = _drawn(("info", "Polish done", "b", None, None))
    bx, by, bd = lay.badge
    ring, badge = _ovals(items)
    assert ring == ((bx - 2, by - 2, bx + bd + 2, by + bd + 2), "#14110F"), "the ring, in the pill's colour"
    assert badge == ((bx, by, bx + bd, by + bd), "#F4F0EA") and bd == 14
    [tick] = [i for i in items if i[0] == "line"]
    assert tick[2]["fill"] == "#14110F"
    assert tick[1] == (bx + 3 + 1.5, by + 3 + 4.2, bx + 3 + 3.3, by + 3 + 6.0, bx + 3 + 6.5, by + 3 + 2.2)
    # And the words come after it, with the same air as on the Mac.
    [(coords, _fill, _font)] = [(c, kw["fill"], kw["font"]) for kind, c, kw in items if kind == "text"]
    assert coords[0] == lay.text_x == bx + bd + 14


def test_windows_draws_three_dots_that_trail_the_words_and_take_turns():
    pl, pill, lay, items = _drawn(("info", "Waffler is busy", "b", None, None))
    dots = _ovals(items)
    assert [fill for _c, fill in dots] == ["#E8C267", "#8A7437", "#4D4226"]
    assert dots[0][0][0] == lay.dots_x > lay.text_x + lay.text_w, "after the words, not on the left"
    _pl2, _p, _l, later = _drawn(("info", "Waffler is busy", "b", None, None), phase=1)
    assert [fill for _c, fill in _ovals(later)] == ["#4D4226", "#E8C267", "#8A7437"]
    assert _texts(items) == [("One moment", "#F4F0EA", ("Segoe UI", -14))]


def test_windows_draws_a_problem_with_its_red_badge_one_button_and_a_cross():
    pl, pill, lay, items = _drawn(("error", "We couldn't hear you", "b", None, None))
    bx, by, bd = lay.badge
    ring, badge = _ovals(items)
    assert ring[1] == "#14110F" and badge == ((bx, by, bx + bd, by + bd), "#F0624F") and bd == 12
    assert _texts(items) == [("Didn\u2019t hear you", "#F4F0EA", ("Segoe UI", -14)),
                             ("Mic", "#12100E", ("Segoe UI Semibold", -12))]
    button = [i for i in items if i[0] == "polygon" and i[2]["fill"] == "#F4F0EA"]
    assert len(button) == 1
    ys = button[0][1][1::2]
    assert max(ys) - min(ys) == 28
    cross = [i for i in items if i[0] == "line" and i[2]["fill"] == "#B3AA9E"]
    assert len(cross) == 2


def test_windows_a_tip_is_quieter_and_has_the_plain_mark():
    pl, pill, lay, items = _drawn(("info", "Don't tap, hold", "Hold fn while you speak, then let go.",
                                   [{"label": "Got it", "action": "tip_dismiss:hold_hint"}], 10))
    assert _texts(items) == [("Hold fn to talk", "#CEC6BB", ("Segoe UI", -14))]
    assert not _ovals(items)


def test_mac_draws_the_badge_and_the_trailing_dots_from_the_same_layout():
    src = MAC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    draw = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_draw_pill"))
    assert "if lay.badge is not None:" in draw and "if lay.dots_x is not None:" in draw
    assert "_pl.BADGE_FILL if pill.kind == _pl.DONE else _pl.RED" in draw
    assert "_pl.BADGE_RING" in draw and "_pl.BADGE_TICK_PT" in draw and "_pl.BADGE_INK" in draw
    assert "glyph" not in src, "nothing sits between the mark and the words any more"


def test_windows_a_click_is_decided_by_the_same_rules_as_the_mac():
    tree = ast.parse(WIN.read_text(encoding="utf-8"))
    click = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                             and n.name == "_on_pill_click"))
    assert "_toast_clicked(_pl.hit(lay, event.x, event.y))" in click
    mac = ast.unparse(ast.parse(MAC.read_text(encoding="utf-8")))
    assert "_toast_clicked(_pl.hit(lay, pt.x, pt.y))" in mac
    for path in (MAC, WIN):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        clicked = ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                                   and n.name == "_toast_clicked"))
        assert "action = _pl.action_for(pill, where)" in clicked, path.name
        assert "if action:" in clicked, "a click that sends nothing leaves the pill up"


def test_mac_a_click_sends_the_answer_and_takes_the_pill_down():
    pl = _pill_module()
    sent, hidden = [], []
    ns = _lift(MAC, "_toast_clicked", _pl=pl, _dbg=lambda *a, **k: None,
               emit=lambda event, **k: sent.append((event, k)), _hide_toast=lambda: hidden.append(1),
               _toast_pill=pl.pill_for("info", "Still working on it", "b", [
                   {"label": "Send later", "action": "send_later"},
                   {"label": "Cancel", "action": "cancel_processing"}]))
    import io
    ns["sys"] = type("S", (), {"stderr": io.StringIO()})()
    ns["_toast_clicked"]("body")
    assert sent == [] and hidden == [], "a click on the words of a Your call does nothing"
    ns["_toast_clicked"]("button")
    assert sent == [("toast_action", {"action": "send_later"})] and hidden == [1]
    ns["_toast_clicked"]("close")
    assert sent[-1] == ("toast_action", {"action": "cancel_processing"})
    ns["_toast_pill"] = None
    ns["_toast_clicked"]("button")
    assert len(sent) == 2, "no pill, no answer"


def test_windows_blend():
    ns = _lift(WIN, "_blend")
    assert ns["_blend"]("#000000", "#FFFFFF", 0.5) == "#808080"
    assert ns["_blend"]("#14110F", "#FFFFFF", 0.0) == "#14110F"


def test_both_overlay_processes_document_the_same_command():
    for path in (MAC, WIN):
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
        assert '"pill": {"kind", "text", "button", "close", "click", "seconds",' in doc, path.name
        assert '"reposition": true}' in doc, path.name
        assert '"buttons": [{"label", "action", "kind"}, ...], "timeout")' in doc, path.name


def test_both_overlay_processes_take_their_sizes_and_colours_from_the_one_place():
    for path in (MAC, WIN):
        src = path.read_text(encoding="utf-8")
        assert "TOAST_H     = _pl.HEIGHT" in src and "TOAST_PAD   = _pl.GAP" in src, path.name
        for old in ("TOAST_W", "TOAST_BTN_KINDS", "_draw_sad_waffle", "'Select mic'", "'Keep going'"):
            assert old not in src, f"{path.name} still has the old card's {old}"
