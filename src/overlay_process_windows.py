#!/usr/bin/env python3
"""
Waffler Overlay Process — Windows (tkinter)

Runs as a subprocess — owns its own tkinter mainloop.
Receives JSON commands from stdin; emits JSON events on stdout.

Commands:  {"type": "show"}
           {"type": "hide"}
           {"type": "level", "value": 0.0-1.0}
           {"type": "working", "elapsed_seconds": 3.0}
                 after release: the pill stays, cells ripple, the elapsed
                 time shows and one X cancels
           {"type": "working_end", "result": "done"|"quiet"}
                 "done" shows a short tick, then hides; "quiet" just hides.
                 A Working pill that is up becomes "Done" in place, or goes;
                 any other message is left alone.
           {"type": "show_toast", "style": "cancel"|"error"|"warn"|"info",
            "pill": {"kind", "text", "button", "close", "click", "seconds",
                     "description"},
            "reposition": true}                              (optional)
                 every message is one small pill (src/pill.py): the waffle
                 mark, a tick, three dots or a red dot, one line, at most one
                 button and a cross. The app sends the pill ready made; the
                 style still travels so hide_toast can withdraw by it.
                 reposition is accepted and ignored: the dictation pill and
                 its messages always sit at the bottom centre of the main
                 screen here. A command with the old card's fields instead
                 ("heading", "body",
                 "buttons": [{"label", "action", "kind"}, ...], "timeout")
                 becomes its pill here, by the same table.
           {"type": "hide_toast", "style": "info"}   (style optional: only
                 a pill raised with that style is taken down)
           {"type": "quit"}

Events:    {"event": "cancel_request"}
           {"event": "stop"}
           {"event": "ready"}
           {"event": "toast_action", "action": "confirm"|"dismiss"|"select_mic"|...}

All imports are Python stdlib or tkinter (built into CPython on Windows),
and src/pill.py, which is stdlib only.
"""

import sys
import json
import threading
import math
import time
import random
import queue
import tkinter as tk
import tkinter.font as tkfont

try:
    import pill as _pl
except ImportError:  # imported as src.overlay_process_windows
    from src import pill as _pl

# ── Constants ──────────────────────────────────────────────────────────
TRANSPARENT  = '#010101'   # Magic colour used for window-level transparency

# Waffle palette — warm, realistic waffle tones
WAFFLE_BODY  = '#D4A843'   # Golden body / ridges (catches light)
WAFFLE_RIM   = '#B08530'   # Outer crust border
CELL_BG      = '#C49838'   # Cell pocket base (slightly darker than ridges)
CELL_HILITE  = '#E8C86C'   # Top/left edge highlight inside pocket (light catching rim)
CELL_SHADOW  = '#9A7825'   # Bottom/right edge shadow (depth)
SYRUP_COLOR  = '#5C2E0E'   # Dark amber maple syrup
SYRUP_LIGHT  = '#7A3F14'   # Lighter syrup for partial fill
SYRUP_SHEEN  = '#8B4518'   # Subtle syrup highlight

# Button colours
BTN_RING     = '#9A7825'   # Button outline ring (darker for contrast)
BTN_FILL     = '#2A1F0E'   # Dark filled background so icons pop
CANCEL_X_CLR = '#D94040'   # Cancel X colour
STOP_ICON    = '#ffffff'   # Stop square colour

# Waffle grid
GRID_ROWS   = 4
GRID_COLS   = 4
NUM_CELLS   = GRID_ROWS * GRID_COLS   # 16
CELL_SIZE   = 11           # Each pocket is 11x11 px
GRID_GAP    = 3            # Ridge width between cells
OUTER_RIM   = 5            # Outer crust rim
INNER_PAD   = 3            # Padding between rim and first cell
CORNER_R    = 10           # More rounded = more waffle-like

# Waffle dimensions (square grid area)
WAFFLE_W    = OUTER_RIM * 2 + INNER_PAD * 2 + GRID_COLS * CELL_SIZE + (GRID_COLS - 1) * GRID_GAP  # 69
WAFFLE_H    = WAFFLE_W

# Window dimensions (waffle + bigger buttons below)
BTN_R       = 10           # Bigger button radius
BTN_GAP     = 6            # Gap between waffle and buttons
WIN_W       = WAFFLE_W
WIN_H       = WAFFLE_H + BTN_GAP + BTN_R * 2 + 2  # 69 + 6 + 20 + 2 = 97

# Grid origin (top-left of first cell)
GRID_OX     = OUTER_RIM + INNER_PAD
GRID_OY     = OUTER_RIM + INNER_PAD

# Click zones — centred below waffle, spaced apart
BTN_ROW_Y     = WAFFLE_H + BTN_GAP + BTN_R    # Centre of button row
BTN_CANCEL_CX = WIN_W // 2 - 16               # Left button
BTN_CANCEL_CY = BTN_ROW_Y
BTN_STOP_CX   = WIN_W // 2 + 16               # Right button
BTN_STOP_CY   = BTN_ROW_Y
BTN_HIT_R2    = (BTN_R + 4) ** 2              # Squared hit radius

# While working (after release) the stop button has nothing to stop, so the
# X sits alone in the middle of the button row.
BTN_WORK_CX   = WIN_W // 2

# Badges drawn on the waffle: the elapsed time while working, a tick when done
BADGE_FILL    = '#1A1208'
BADGE_RING    = '#C8A256'
BADGE_TEXT    = '#F0E0C0'
DONE_TICK_MS  = 900        # how long the tick shows before the pill goes

# The small pill every message is now (src/pill.py has its sizes, colours and
# rules; this file only draws it). It sits a small gap above the dictation
# pill's window and is as wide as its words. Same numbers as the Mac's.
TOAST_H     = _pl.HEIGHT   # 40
TOAST_PAD   = _pl.GAP      # between it and the dictation pill

# ── Global state ───────────────────────────────────────────────────────
_cmd_queue: queue.Queue = queue.Queue()
_bars:    list = [0.0] * NUM_CELLS
_targets: list = [0.0] * NUM_CELLS
_visible: bool = False
_root           = None
_canvas         = None
_toast_win      = None
_toast_canvas   = None
_toast_style    = None
_toast_pill     = None     # the src/pill.py Pill that is up
_toast_lay      = None     # and where its parts are
_toast_gen      = 0        # goes up with every pill shown or hidden
_toast_phase    = 0        # which of a Working pill's dots is the bright one

# Progress text shown over the pill during the slow post-recording stages
# (transcribing, styling). Cleared when recording starts again or pill hides.
_progress_text_item = None   # Canvas text item id, or None
_progress_bg_item   = None   # Pill-shaped background item id, or None

# What the pill is showing: "recording" (VU cells, X and stop), "working"
# (after release: ripple, elapsed time, X) or "done" (the short tick).
_mode: str = "recording"
_work_elapsed: int = 0
_done_after_id = None        # Tk after() id of the tick's hide, or None

# Screen position (set during init, reused for toast positioning)
_waffle_x = 0
_waffle_y = 0


def elapsed_text(seconds) -> str:
    """The pill's elapsed time: 7s under a minute, then 1:05. Same as
    pipeline_watchdog.elapsed_label, which this process does not import."""
    s = max(0, int(seconds))
    return f"{s}s" if s < 60 else f"{s // 60}:{s % 60:02d}"


def working_targets(t: float) -> list:
    """Cell fill levels for the working look at time ``t``: a slow ripple
    running diagonally across the waffle, so the pill reads as busy, not
    listening."""
    out = []
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            wave = 0.5 + 0.5 * math.sin(t * 3.2 - (row + col) * 0.9)
            out.append(0.18 + 0.62 * wave)
    return out


# ── IPC helpers ────────────────────────────────────────────────────────

def emit(event: str, **kwargs):
    """Send a JSON event to the parent process via stdout."""
    data = {"event": event, **kwargs}
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


# ── Drawing ────────────────────────────────────────────────────────────

def _draw_waffle():
    """Redraw the entire overlay canvas as a realistic waffle with syrup."""
    if _canvas is None:
        return
    try:
        _canvas.delete("all")
    except Exception:
        return  # Canvas destroyed, bail out

    # 1. Waffle body — rounded rectangle (the raised ridges / crust)
    _rounded_rect(_canvas, 0, 0, WAFFLE_W, WAFFLE_H, CORNER_R,
                  fill=WAFFLE_BODY, outline=WAFFLE_RIM, width=2)

    # 2. Outer rim highlight — subtle light line along top edge for 3D
    _canvas.create_arc(
        2, 2, CORNER_R * 2, CORNER_R * 2,
        start=90, extent=90, style=tk.ARC, outline=CELL_HILITE, width=1
    )
    _canvas.create_line(
        CORNER_R, 2, WAFFLE_W - CORNER_R, 2,
        fill=CELL_HILITE, width=1
    )

    # 3. Draw cells (pockets) with 3D depth effect
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            i = row * GRID_COLS + col
            cx = GRID_OX + col * (CELL_SIZE + GRID_GAP)
            cy = GRID_OY + row * (CELL_SIZE + GRID_GAP)

            # Cell pocket background
            _canvas.create_rectangle(
                cx, cy, cx + CELL_SIZE, cy + CELL_SIZE,
                fill=CELL_BG, outline=CELL_BG
            )

            # 3D depth: highlight on top & left edges (pocket rim catching light)
            _canvas.create_line(cx, cy, cx + CELL_SIZE, cy,
                                fill=CELL_HILITE, width=1)
            _canvas.create_line(cx, cy, cx, cy + CELL_SIZE,
                                fill=CELL_HILITE, width=1)

            # 3D depth: shadow on bottom & right edges (pocket depth)
            _canvas.create_line(cx, cy + CELL_SIZE, cx + CELL_SIZE, cy + CELL_SIZE,
                                fill=CELL_SHADOW, width=1)
            _canvas.create_line(cx + CELL_SIZE, cy, cx + CELL_SIZE, cy + CELL_SIZE,
                                fill=CELL_SHADOW, width=1)

            # Syrup fill from bottom up
            lvl = max(0.0, min(1.0, _bars[i]))
            fill_h = int(lvl * CELL_SIZE)
            if fill_h > 0:
                # Syrup color darkens as it fills more
                color = SYRUP_COLOR if lvl > 0.6 else SYRUP_LIGHT
                sy_top = cy + CELL_SIZE - fill_h
                _canvas.create_rectangle(
                    cx + 1, sy_top, cx + CELL_SIZE - 1, cy + CELL_SIZE - 1,
                    fill=color, outline=color
                )
                # Tiny sheen highlight on syrup surface (1px line at top of syrup)
                if fill_h > 3:
                    _canvas.create_line(
                        cx + 2, sy_top + 1, cx + CELL_SIZE - 2, sy_top + 1,
                        fill=SYRUP_SHEEN, width=1
                    )

    if _mode == "done":
        # The tick replaces the buttons: there is nothing left to stop.
        _draw_tick_badge()
        return

    # 4. Cancel button (X): dark filled circle with red X. While working it
    # sits alone in the middle: cancel is the only thing left to do.
    x_cx = BTN_WORK_CX if _mode == "working" else BTN_CANCEL_CX
    _canvas.create_oval(
        x_cx - BTN_R, BTN_CANCEL_CY - BTN_R,
        x_cx + BTN_R, BTN_CANCEL_CY + BTN_R,
        fill=BTN_FILL, outline=BTN_RING, width=2
    )
    off = 4
    _canvas.create_line(
        x_cx - off, BTN_CANCEL_CY - off,
        x_cx + off, BTN_CANCEL_CY + off,
        fill=CANCEL_X_CLR, width=2, capstyle=tk.ROUND
    )
    _canvas.create_line(
        x_cx + off, BTN_CANCEL_CY - off,
        x_cx - off, BTN_CANCEL_CY + off,
        fill=CANCEL_X_CLR, width=2, capstyle=tk.ROUND
    )

    if _mode == "working":
        _draw_elapsed_badge()
        return

    # 5. Stop button (■) — dark filled circle with white square
    _canvas.create_oval(
        BTN_STOP_CX - BTN_R, BTN_STOP_CY - BTN_R,
        BTN_STOP_CX + BTN_R, BTN_STOP_CY + BTN_R,
        fill=BTN_FILL, outline=BTN_RING, width=2
    )
    sq = 4
    _canvas.create_rectangle(
        BTN_STOP_CX - sq, BTN_STOP_CY - sq,
        BTN_STOP_CX + sq, BTN_STOP_CY + sq,
        fill=STOP_ICON, outline=STOP_ICON
    )


def _draw_elapsed_badge():
    """The elapsed time on a small dark badge in the middle of the waffle."""
    if _work_elapsed < 1:
        return
    cx, cy = WAFFLE_W // 2, WAFFLE_H // 2
    text = _canvas.create_text(cx, cy, text=elapsed_text(_work_elapsed),
                               fill=BADGE_TEXT, font=('Segoe UI', 9, 'bold'))
    bbox = _canvas.bbox(text)
    if bbox:
        x0, y0, x1, y1 = bbox
        bg = _rounded_rect(_canvas, x0 - 6, y0 - 1, x1 + 6, y1 + 1, 7,
                           fill=BADGE_FILL, outline=BADGE_RING, width=1)
        _canvas.tag_lower(bg, text)


def _draw_tick_badge():
    """A tick on a round dark badge: the dictation went through."""
    cx, cy, r = WAFFLE_W // 2, WAFFLE_H // 2, 15
    _canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                        fill=BADGE_FILL, outline=BADGE_RING, width=2)
    _canvas.create_line(cx - 7, cy + 1, cx - 2, cy + 6, cx + 8, cy - 6,
                        fill=BADGE_TEXT, width=3, capstyle=tk.ROUND,
                        joinstyle=tk.ROUND)


# ── Toast popup ────────────────────────────────────────────────────────

def _rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    """Draw a rounded rectangle using tk smooth polygon."""
    # Clamp radius so it doesn't exceed half the width or height
    r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
    if r < 1:
        return canvas.create_rectangle(x1, y1, x2, y2, **kwargs)
    pts = [
        x1 + r, y1,       # top edge start
        x1 + r, y1,       # anchor
        x2 - r, y1,       # top edge end
        x2 - r, y1,       # anchor
        x2, y1,           # corner
        x2, y1 + r,       # right edge start
        x2, y1 + r,       # anchor
        x2, y2 - r,       # right edge end
        x2, y2 - r,       # anchor
        x2, y2,           # corner
        x2 - r, y2,       # bottom edge start
        x2 - r, y2,       # anchor
        x1 + r, y2,       # bottom edge end
        x1 + r, y2,       # anchor
        x1, y2,           # corner
        x1, y2 - r,       # left edge start
        x1, y2 - r,       # anchor
        x1, y1 + r,       # left edge end
        x1, y1 + r,       # anchor
        x1, y1,           # corner
    ]
    return canvas.create_polygon(pts, smooth=True, **kwargs)


# ── Clicks that never take the focus ───────────────────────────────────
# A click on a normal window makes it the active one. So clicking the
# pill's ■ or a toast's "Keep waiting" / "Paste as is" made this overlay
# process the foreground app, and the Ctrl+V that followed went to the
# overlay instead of the app the user was typing in. The main process's
# SetForegroundWindow could not take the focus back either: Windows only
# lets the process that received the last input (the overlay, which got the
# click) move the foreground. WS_EX_NOACTIVATE makes a window take clicks
# without becoming active, which is how on-screen keyboards work.
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
GA_ROOT = 2
_user32 = None


def _win32():
    """user32 with its own function prototypes (not the shared windll ones)."""
    global _user32
    if _user32 is None:
        import ctypes
        from ctypes import wintypes
        dll = ctypes.WinDLL("user32")
        long_ptr = ctypes.c_ssize_t
        get = getattr(dll, "GetWindowLongPtrW", None) or dll.GetWindowLongW
        put = getattr(dll, "SetWindowLongPtrW", None) or dll.SetWindowLongW
        get.argtypes, get.restype = [wintypes.HWND, ctypes.c_int], long_ptr
        put.argtypes, put.restype = [wintypes.HWND, ctypes.c_int, long_ptr], long_ptr
        dll.GetAncestor.argtypes, dll.GetAncestor.restype = [wintypes.HWND, wintypes.UINT], wintypes.HWND
        _user32 = (get, put, dll.GetAncestor)
    return _user32


def _top_hwnd(win):
    """The top-level Windows handle behind a Tk toplevel (Tk wraps it)."""
    get, put, ancestor = _win32()
    return ancestor(win.winfo_id(), GA_ROOT)


def _no_activate(win) -> bool:
    """Make ``win`` take clicks without taking the focus. Idempotent, and
    called after every show: Tk only builds the real window when it is first
    shown. True once the style is set."""
    if win is None or not sys.platform.startswith("win"):
        return False
    try:
        win.update_idletasks()
        hwnd = _top_hwnd(win)
        if not hwnd:
            return False
        get, put, _ancestor = _win32()
        style = get(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_NOACTIVATE:
            put(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
        return bool(get(hwnd, GWL_EXSTYLE) & WS_EX_NOACTIVATE)
    except Exception:
        return False


# ── The small pill (every message) ────────────────────────────────────
# One dark capsule, the meeting bar's family, drawn the same as the Mac's
# (overlay_process.py _draw_pill) as far as a Tk canvas can: no soft shadow
# and no smoothed edges, because the window is cut out with one colour key.

PILL_FONT = 'Segoe UI'
PILL_FONT_SEMIBOLD = 'Segoe UI Semibold'


def _blend(under: str, over: str, alpha: float) -> str:
    """``over`` at ``alpha`` on ``under``, as one colour: Tk has no see-through
    lines, so the hairline (white at 14%) is worked out here."""
    u = [int(under[i:i + 2], 16) for i in (1, 3, 5)]
    o = [int(over[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(a + (b - a) * alpha):02X}' for a, b in zip(u, o))


def _pill_font(points, weight):
    """A Tk font spec. Negative sizes are pixels, which is what the pill is
    laid out in."""
    return (PILL_FONT_SEMIBOLD if weight == 'semibold' else PILL_FONT, -int(points))


def _pill_measure(text, points, weight):
    try:
        return tkfont.Font(font=_pill_font(points, weight)).measure(text or '')
    except Exception:
        return int(len(text or '') * points * 0.56)      # no display: a fair guess


def _draw_waffle_mark(canvas, x, y):
    """The app's own waffle, small and at rest: the dictation pill's body,
    rim and sixteen pockets in 22 pixels."""
    size = _pl.MARK
    _rounded_rect(canvas, x, y, x + size, y + size, 4,
                  fill=WAFFLE_BODY, outline=WAFFLE_RIM, width=1)
    cell, gap, pad = 3, 2, 2            # 2 + 4 * 3 + 3 * 2 + 2 = 22
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            cx = x + pad + col * (cell + gap)
            cy = y + pad + row * (cell + gap)
            canvas.create_rectangle(cx, cy, cx + cell, cy + cell, fill=CELL_BG, outline=CELL_BG)


def _draw_pill(canvas, pill, lay, phase=0):
    """Draw ``pill`` on ``canvas`` (``lay.width`` x 40). The Mac's
    _draw_pill, item for item."""
    w, h = int(lay.width), int(lay.height)
    canvas.delete('all')
    _rounded_rect(canvas, 0, 0, w - 1, h - 1, h // 2, fill=_pl.FILL,
                  outline=_blend(_pl.FILL, '#FFFFFF', _pl.HAIRLINE_ALPHA), width=1)
    _draw_waffle_mark(canvas, int(lay.mark_x), (h - _pl.MARK) // 2)

    if lay.glyph_x is not None:
        gx = lay.glyph_x
        if pill.kind == _pl.DONE:
            top = (h - _pl.TICK) / 2.0
            pts = []
            for px, py in _pl.TICK_POINTS:
                pts += [gx + px, top + py]
            canvas.create_line(*pts, fill=_pl.GOLD, width=2, capstyle='round', joinstyle='round')
        elif pill.kind == _pl.WORKING:
            top = (h - _pl.DOT) / 2.0
            for i in range(3):
                x0 = gx + i * (_pl.DOT + _pl.DOT_GAP)
                colour = _pl.DOTS[(i - phase) % 3]
                canvas.create_oval(x0, top, x0 + _pl.DOT, top + _pl.DOT, fill=colour, outline=colour,
                                   tags=(f'dot{i}',))
        elif pill.kind == _pl.PROBLEM:
            top = (h - _pl.RED_DOT) / 2.0
            canvas.create_oval(gx, top, gx + _pl.RED_DOT, top + _pl.RED_DOT, fill=_pl.RED, outline=_pl.RED)

    ink = _pl.INK_QUIET if pill.kind == _pl.TIP else _pl.INK
    canvas.create_text(lay.text_x, h // 2, text=pill.text, fill=ink, anchor='w',
                       font=_pill_font(_pl.TEXT_PT, 'medium'))

    if lay.button is not None:
        bx, by, bw, bh = (int(v) for v in lay.button)
        _rounded_rect(canvas, bx, by, bx + bw, by + bh, bh // 2,
                      fill=_pl.BUTTON_FILL, outline=_pl.BUTTON_FILL)
        canvas.create_text(bx + bw // 2, by + bh // 2, text=pill.button['label'], fill=_pl.BUTTON_INK,
                           font=_pill_font(_pl.BUTTON_PT, 'semibold'))

    if lay.close is not None:
        cx, cy, cw, ch = lay.close
        mx, my, arm = cx + cw / 2.0, cy + ch / 2.0, _pl.CROSS_ARM
        canvas.create_line(mx - arm, my - arm, mx + arm, my + arm, fill=_pl.CROSS, width=2, capstyle='round')
        canvas.create_line(mx + arm, my - arm, mx - arm, my + arm, fill=_pl.CROSS, width=2, capstyle='round')


def _toast_ms(pill):
    """How long a pill stays, in ms: its own time, or, for a Working pill, the
    cap after which it goes even if nobody said the work had ended. None:
    until it is answered, withdrawn or replaced."""
    if pill.seconds:
        return int(float(pill.seconds) * 1000)
    if pill.kind == _pl.WORKING:
        return int(_pl.WORKING_CAP_S * 1000)
    return None


def _toast_origin(width):
    """Centred over the dictation pill, a small gap above its window."""
    return int(_waffle_x + (WAFFLE_W - width) // 2), int(_waffle_y - TOAST_H - TOAST_PAD)


def _reduce_motion() -> bool:
    """Windows' "Show animations" is off: the pill just appears and
    disappears, and its dots stand still."""
    try:
        import ctypes
        on = ctypes.c_int(1)
        SPI_GETCLIENTAREAANIMATION = 0x1042
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(on), 0):
            return not bool(on.value)
    except Exception:
        pass
    return False


def _show_toast(style: str, heading: str, body: str, buttons=None, timeout=None, pill=None):
    """Show a message as the small pill.

    ``pill`` is what the app worked out (src/pill.py Pill.as_dict()). A
    command without one is the old card's (heading, body, buttons, timeout),
    and becomes its pill here by the same table, so there is only ever one
    look.
    """
    made = _pl.from_dict(pill) or _pl.pill_for(style, heading, body, buttons, timeout)
    if made is None:
        return
    _put_pill(made, style)


def _put_pill(pill, style, in_place=False):
    """Put ``pill`` up. There is one pill at a time: a second one takes the
    first one's window, without appearing all over again."""
    global _toast_win, _toast_canvas, _toast_style, _toast_pill, _toast_lay, _toast_gen, _toast_phase
    _toast_gen += 1
    gen = _toast_gen
    lay = _pl.layout(pill, _pill_measure)
    tw, th = int(math.ceil(lay.width)), TOAST_H
    tx, ty = _toast_origin(tw)
    still = _reduce_motion()

    replacing = _toast_win is not None
    if not replacing:
        _toast_win = tk.Toplevel(_root)
        _toast_win.overrideredirect(True)
        _toast_win.attributes('-topmost', True)
        _toast_win.configure(bg=TRANSPARENT)
        _toast_win.attributes('-transparentcolor', TRANSPARENT)
        _toast_canvas = tk.Canvas(_toast_win, width=tw, height=th, bg=TRANSPARENT,
                                  highlightthickness=0)
        _toast_canvas.pack()
        # One handler for the whole pill: where the click landed decides.
        _toast_canvas.bind('<Button-1>', _on_pill_click)

    _toast_style, _toast_pill, _toast_lay, _toast_phase = style, pill, lay, 0
    _toast_canvas.configure(width=tw, height=th)
    _draw_pill(_toast_canvas, pill, lay, 0)

    rise = 0 if (replacing or still) else _pl.RISE
    _toast_win.geometry(f"{tw}x{th}+{tx}+{ty + rise}")
    if rise:
        # A quick fade and a slight rise, in four steps.
        try:
            _toast_win.attributes('-alpha', 0.0)
        except Exception:
            rise = 0

    # ── Force the pill to actually paint and stay on top. ──
    # Windows likes to demote -topmost when the active app reclaims focus,
    # and Tkinter sometimes lazily defers paint. Force both: paint now,
    # lift above z-stack, and re-assert topmost a few times in the first
    # couple of seconds so the OS doesn't push us behind the user's app.
    try:
        _toast_win.update_idletasks()
        _toast_win.lift()
    except Exception:
        pass
    # Its button ("Send later", "Paste as is") comes just before a paste:
    # clicking it must leave the user's app in front.
    _no_activate(_toast_win)

    win = _toast_win

    def _step(n):
        if _toast_gen != gen or _toast_win is not win:
            return
        try:
            win.attributes('-alpha', min(1.0, n / 4.0))
            win.geometry(f"{tw}x{th}+{tx}+{ty + rise - int(rise * n / 4.0)}")
            if n < 4:
                win.after(int(_pl.FADE_S * 1000 / 4), lambda: _step(n + 1))
        except Exception:
            pass

    if rise:
        _step(1)
    else:
        try:
            win.attributes('-alpha', 1.0)
        except Exception:
            pass

    def _reassert_topmost():
        if _toast_win is not win:
            return
        try:
            win.attributes('-topmost', True)
            win.lift()
        except Exception:
            pass
        _no_activate(win)

    # Re-assert topmost at 100/400/1200ms — covers the typical window
    # in which Windows can demote the pill behind a refocusing app.
    for delay_ms in (100, 400, 1200):
        try:
            win.after(delay_ms, _reassert_topmost)
        except Exception:
            pass

    if pill.kind == _pl.WORKING and not still:
        win.after(450, lambda: _dots_tick(gen))

    # It goes by itself after its time. The generation keeps a timer that
    # fires late from taking down the pill that replaced its own.
    ms = _toast_ms(pill)
    if ms:
        win.after(ms, lambda: _toast_expire(gen))


def _dots_tick(gen):
    """The three dots of a Working pill take turns being the bright one."""
    global _toast_phase
    if gen != _toast_gen or _toast_canvas is None:
        return
    _toast_phase = (_toast_phase + 1) % 3
    try:
        for i in range(3):
            colour = _pl.DOTS[(i - _toast_phase) % 3]
            _toast_canvas.itemconfigure(f'dot{i}', fill=colour, outline=colour)
        _toast_win.after(450, lambda: _dots_tick(gen))
    except Exception:
        pass


def _toast_expire(gen):
    if gen == _toast_gen:
        _hide_toast()


def _on_pill_click(event):
    """A click on the pill: its button, its cross, or the pill itself. A
    click that sends nothing (the words of a "Your call") leaves it up."""
    pill, lay = _toast_pill, _toast_lay
    if pill is None or lay is None:
        return
    _toast_clicked(_pl.hit(lay, event.x, event.y))


def _toast_clicked(where):
    """A click landed on the pill that is up: on its "button", its "close"
    cross or its "body". The answer is sent, then the pill goes."""
    pill = _toast_pill
    if pill is None:
        return
    action = _pl.action_for(pill, where)
    if action:
        _on_toast_action(action)


def _hide_toast(style=None):
    """Take the pill down, if one is up. With ``style``, only a pill raised
    with that style goes (the pipeline withdraws its "still working" offer
    this way without touching a message that has replaced it). The dictation
    pill is not touched: neither hides the other now."""
    global _toast_win, _toast_canvas, _toast_style, _toast_pill, _toast_lay, _toast_gen
    if style and _toast_style != style:
        return
    _toast_gen += 1
    if _toast_win:
        try:
            _toast_win.destroy()
        except Exception:
            pass
    _toast_win = _toast_canvas = _toast_style = _toast_pill = _toast_lay = None


def _toast_work_ended(result: str):
    """The work ended. A Working pill that is still up becomes "Done" where
    it is, or goes; any other pill (a Problem that replaced it) stays."""
    pill = _toast_pill
    if pill is None or pill.kind != _pl.WORKING:
        return
    after = _pl.after_work(pill, result)
    if after is None:
        _hide_toast()
    else:
        _put_pill(after, _toast_style, in_place=True)


def _clear_progress():
    """Remove any progress label / background from the pill canvas."""
    global _progress_text_item, _progress_bg_item
    if not _canvas:
        return
    try:
        if _progress_text_item is not None:
            _canvas.delete(_progress_text_item)
        if _progress_bg_item is not None:
            _canvas.delete(_progress_bg_item)
    except Exception:
        pass
    _progress_text_item = None
    _progress_bg_item = None


def _show_progress(label: str, elapsed_seconds: float = 0.0):
    """Draw a status label + elapsed time on the pill so the user knows the
    app is doing work (transcribing / styling) and isn't frozen.

    Replaces any previously-drawn progress text. Pass label="" to clear.
    """
    global _progress_text_item, _progress_bg_item
    if not _canvas or not _root:
        return

    # Clear previous render first.
    _clear_progress()

    label = (label or "").strip()
    if not label:
        return

    # Format the visible text: "Styling…  3s"
    if elapsed_seconds >= 1.0:
        display = f"{label}…  {int(elapsed_seconds)}s"
    else:
        display = f"{label}…"

    # Position: centred on the pill, slightly above middle so it doesn't
    # collide with the bottom rows of waffle cells.
    cx = WIN_W // 2
    cy = WIN_H // 2

    # Background pill behind the text for legibility against the waffle.
    pad_x, pad_y = 14, 6
    # Use a temporary text item to measure the bbox before drawing the bg.
    _progress_text_item = _canvas.create_text(
        cx, cy, text=display,
        fill='#F0E0C0',  # warm cream
        font=('Segoe UI', 10, 'bold'),
    )
    bbox = _canvas.bbox(_progress_text_item)
    if bbox:
        x0, y0, x1, y1 = bbox
        _progress_bg_item = _canvas.create_rectangle(
            x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y,
            fill='#1A1208',  # dark warm
            outline='#C8A256',
            width=1,
        )
        # Put background BEHIND the text.
        _canvas.tag_lower(_progress_bg_item, _progress_text_item)


def _on_toast_action(action: str):
    """Handle toast button clicks."""
    emit("toast_action", action=action)
    _hide_toast()


# ── Command handler (runs on main/tkinter thread via _animation_loop) ──

def _cancel_done_timer():
    global _done_after_id
    if _done_after_id is not None and _root:
        try:
            _root.after_cancel(_done_after_id)
        except Exception:
            pass
    _done_after_id = None


def _reset_cells(level: float = 0.0):
    for i in range(NUM_CELLS):
        _bars[i] = level
        _targets[i] = level


def _finish_done():
    """The tick has shown long enough: hide the pill."""
    global _visible, _mode, _done_after_id
    _done_after_id = None
    if _mode != "done":
        return
    _mode = "recording"
    _visible = False
    _reset_cells()
    if _root:
        _root.withdraw()


def _enter_working(elapsed_seconds: float):
    global _visible, _mode, _work_elapsed
    first = _mode != "working"
    _work_elapsed = int(max(0.0, elapsed_seconds))
    if first:
        _cancel_done_timer()
        _mode = "working"
        _clear_progress()
    _visible = True
    if _root:
        _draw_waffle()
        # A message no longer hides the pill: the small pill sits above it.
        if first:
            _root.deiconify()
            _root.lift()
            _root.attributes('-topmost', True)
            _no_activate(_root)


def _end_working(result: str):
    """Leave the working look: a short tick, or straight back to hidden.
    Never touches a toast, so a message that is already up stays readable."""
    global _visible, _mode, _done_after_id
    _cancel_done_timer()
    # A Working pill ("Tidying", "One moment") becomes "Done" in place, or
    # goes. Then, as before, the waffle's own tick is skipped while a message
    # is up: one sign that it is done is enough.
    _toast_work_ended(result)
    if result == "done" and _toast_win is None and _visible and _root:
        _mode = "done"
        _reset_cells(1.0)       # a full waffle behind the tick
        _draw_waffle()
        _done_after_id = _root.after(DONE_TICK_MS, _finish_done)
        return
    _mode = "recording"
    _visible = False
    _reset_cells()
    if _root:
        _root.withdraw()


def _handle_cmd(cmd: dict):
    global _visible, _mode

    ctype = cmd.get("type")

    if ctype == "show":
        _visible = True
        _cancel_done_timer()
        _mode = "recording"
        _reset_cells()
        _hide_toast()
        _clear_progress()        # fresh recording — drop any leftover progress text
        if _root:
            _draw_waffle()
            _root.deiconify()
            _root.lift()
            _root.attributes('-topmost', True)
            # Its ■ is followed by a paste: a click must not take the focus.
            _no_activate(_root)

    elif ctype == "hide":
        _visible = False
        _cancel_done_timer()
        _mode = "recording"
        _hide_toast()
        _clear_progress()
        if _root:
            _root.withdraw()

    elif ctype == "working":
        _enter_working(float(cmd.get("elapsed_seconds", 0.0)))

    elif ctype == "working_end":
        _end_working(str(cmd.get("result", "quiet")))

    elif ctype == "level":
        if _mode != "recording":
            return               # a late VU frame after release: ignore it
        _clear_progress()        # active recording — VU bars resume, no status text
        raw_level = max(0.0, min(1.0, float(cmd.get("value", 0.0))))
        level = raw_level ** 0.4   # Power-curve: expand low volumes for responsiveness
        t = time.time()
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                i = row * GRID_COLS + col
                # Bottom rows fill first: row 3 (bottom) gets full level,
                # row 0 (top) only fills at high volumes
                inv_row = GRID_ROWS - 1 - row  # 3,2,1,0
                threshold = inv_row / GRID_ROWS  # 0.0, 0.25, 0.5, 0.75
                row_range = 1.0 / GRID_ROWS       # 0.25
                if level <= threshold:
                    cell_level = 0.0
                elif level >= threshold + row_range:
                    cell_level = 1.0
                else:
                    cell_level = (level - threshold) / row_range
                # Per-cell bounce: each cell wobbles at its own frequency/phase
                # giving a lively, bubbling-syrup look
                phase = i * 0.7 + col * 2.3 + row * 1.8
                bounce1 = math.sin(phase + t * 4.5) * 0.25
                bounce2 = math.sin(phase * 1.7 + t * 6.2) * 0.15
                jitter = random.uniform(-0.1, 0.1)
                wobble = 1.0 + bounce1 + bounce2 + jitter  # range ~0.5 to 1.5
                _targets[i] = max(0.0, min(1.0, cell_level * wobble))

    elif ctype == "show_toast":
        # The dictation pill stays where it is: the small pill sits above it.
        _show_toast(
            style=cmd.get("style", "error"),
            heading=cmd.get("heading", ""),
            body=cmd.get("body", ""),
            buttons=cmd.get("buttons"),
            timeout=cmd.get("timeout"),
            pill=cmd.get("pill"),
        )

    elif ctype == "hide_toast":
        _hide_toast(cmd.get("style"))

    elif ctype == "_toast_click":
        # A click, said rather than made (scripts/drive_overlay.py). The app
        # never sends it.
        _toast_clicked(cmd.get("where"))

    elif ctype == "progress":
        _show_progress(cmd.get("label", ""), float(cmd.get("elapsed_seconds", 0.0)))

    elif ctype == "quit":
        _hide_toast()
        if _root:
            try:
                _root.destroy()
            except Exception:
                pass


# ── Animation loop (tkinter after-callback, 50 ms = 20 fps) ───────────

def _animation_loop():
    try:
        # Drain the command queue (safe on main thread)
        try:
            while True:
                cmd = _cmd_queue.get_nowait()
                _handle_cmd(cmd)
        except queue.Empty:
            pass

        if _mode == "working" and _visible:
            _targets[:] = working_targets(time.time())

        # Smooth bar interpolation
        changed = False
        for i in range(NUM_CELLS):
            diff = _targets[i] - _bars[i]
            if abs(diff) > 0.005:
                _bars[i] += diff * 0.35
                changed = True
            else:
                _bars[i] = _targets[i]

        if changed and _visible:
            _draw_waffle()
    except Exception:
        pass  # Window may be destroyed, don't crash

    if _root:
        try:
            _root.after(50, _animation_loop)
        except Exception:
            pass


# ── Mouse click ────────────────────────────────────────────────────────

def _on_click(event):
    x, y = event.x, event.y
    if _mode == "done":
        return
    if _mode == "working":
        # The one button while working: cancel the dictation being processed.
        if (x - BTN_WORK_CX) ** 2 + (y - BTN_CANCEL_CY) ** 2 <= BTN_HIT_R2:
            emit("cancel_request")
        return
    # Cancel button — bottom-left circle
    if (x - BTN_CANCEL_CX) ** 2 + (y - BTN_CANCEL_CY) ** 2 <= BTN_HIT_R2:
        emit("cancel_request")
    # Stop button — bottom-right circle
    elif (x - BTN_STOP_CX) ** 2 + (y - BTN_STOP_CY) ** 2 <= BTN_HIT_R2:
        emit("stop")


# ── Stdin reader (background daemon thread) ────────────────────────────

def _stdin_reader():
    """Read JSON commands from stdin and push them onto the queue."""
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
                _cmd_queue.put(cmd)
            except json.JSONDecodeError:
                pass
    except Exception:
        pass
    # stdin closed — signal quit
    _cmd_queue.put({"type": "quit"})


# ── Taskbar / screen geometry ──────────────────────────────────────────

def _get_taskbar_height() -> int:
    """
    Ask Windows for the work-area rect to find the taskbar height.
    Falls back to 40px (standard taskbar) if ctypes is unavailable.
    """
    try:
        import ctypes
        import ctypes.wintypes

        SPI_GETWORKAREA = 0x0030

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left",   ctypes.c_long),
                ("top",    ctypes.c_long),
                ("right",  ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        )
        screen_h  = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
        taskbar_h = screen_h - rect.bottom
        return max(0, taskbar_h)
    except Exception:
        return 40   # sensible default for a standard DPI taskbar


# ── Entry point ────────────────────────────────────────────────────────

def main():
    global _root, _canvas, _waffle_x, _waffle_y

    # Kick off the stdin reader
    threading.Thread(target=_stdin_reader, daemon=True, name="StdinReader").start()

    _root = tk.Tk()

    # Window chrome
    _root.overrideredirect(True)          # borderless (no title bar)
    _root.attributes('-topmost', True)    # float above all windows
    _root.configure(bg=TRANSPARENT)
    _root.attributes('-transparentcolor', TRANSPARENT)  # punch-through transparent

    # Position: bottom-centre, 16 px above taskbar
    screen_w  = _root.winfo_screenwidth()
    screen_h  = _root.winfo_screenheight()
    taskbar_h = _get_taskbar_height()
    gap       = 16

    _waffle_x = (screen_w - WIN_W) // 2
    _waffle_y = screen_h - taskbar_h - WIN_H - gap
    _root.geometry(f"{WIN_W}x{WIN_H}+{_waffle_x}+{_waffle_y}")

    # Canvas — same size as window, transparent background
    _canvas = tk.Canvas(
        _root,
        width=WIN_W,
        height=WIN_H,
        bg=TRANSPARENT,
        highlightthickness=0,
    )
    _canvas.pack()
    _canvas.bind("<Button-1>", _on_click)

    # Start hidden; the parent will send {"type": "show"} when ready
    _root.withdraw()

    # Start animation loop
    _root.after(50, _animation_loop)

    emit("ready")
    try:
        _root.mainloop()
    except (OSError, Exception):
        pass  # Window destroyed during shutdown — exit cleanly


if __name__ == "__main__":
    main()
