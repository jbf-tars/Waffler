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
                 Neither touches a toast.
           {"type": "show_toast", "style": "cancel"|"error"|"warn"|"info",
            "heading": "...", "body": "...",
            "buttons": [{"label", "action", "kind"}, ...]}   (buttons optional)
           {"type": "hide_toast", "style": "info"}   (style optional: only
                 a toast of that style is hidden)
           {"type": "quit"}

Events:    {"event": "cancel_request"}
           {"event": "stop"}
           {"event": "ready"}
           {"event": "toast_action", "action": "confirm"|"dismiss"|"select_mic"|...}

All imports are Python stdlib or tkinter (built into CPython on Windows).
"""

import sys
import json
import threading
import math
import time
import random
import queue
import tkinter as tk

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

# Toast constants
TOAST_W     = 380
TOAST_H     = 210  # tall enough for 3-line body text
TOAST_PAD   = 12           # gap above waffle

# Custom toast buttons, by kind (same colours as the fixed ones below)
TOAST_BTN_KINDS = {
    'primary':   ('#C8A256', '#D4A843', '#2A1F0E'),
    'secondary': ('#3D2E14', '#5A4520', '#A89070'),
    'danger':    ('#3D1818', '#D94040', '#D94040'),
}

# ── Global state ───────────────────────────────────────────────────────
_cmd_queue: queue.Queue = queue.Queue()
_bars:    list = [0.0] * NUM_CELLS
_targets: list = [0.0] * NUM_CELLS
_visible: bool = False
_root           = None
_canvas         = None
_toast_win      = None
_toast_style    = None

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


def _draw_sad_waffle(canvas, cx, cy, style='error'):
    """Draw a larger sad waffle icon with expressive face and syrup tear."""
    s = 44
    x0 = cx - s // 2
    y0 = cy - s // 2

    body_clr = '#D4A843' if style != 'cancel' else '#C07040'
    rim_clr  = '#B08530' if style != 'cancel' else '#904030'
    cell_clr = '#C49838' if style != 'cancel' else '#A06040'
    hilite   = '#E8C86C'
    shadow   = '#9A7825' if style != 'cancel' else '#703020'

    # Waffle body
    _rounded_rect(canvas, x0, y0, x0 + s, y0 + s, 7,
                  fill=body_clr, outline=rim_clr, width=2)

    # 3x3 grid of cells
    pad = 5
    gap = 3
    cell = (s - 2 * pad - 2 * gap) // 3  # ~9px
    for r in range(3):
        for col in range(3):
            px = x0 + pad + col * (cell + gap)
            py = y0 + pad + r * (cell + gap)
            canvas.create_rectangle(px, py, px + cell, py + cell,
                                    fill=cell_clr, outline=cell_clr)
            # Highlight top-left edge
            canvas.create_line(px, py, px + cell, py, fill=hilite, width=1)
            canvas.create_line(px, py, px, py + cell, fill=hilite, width=1)
            # Shadow bottom-right edge
            canvas.create_line(px, py + cell, px + cell, py + cell, fill=shadow, width=1)
            canvas.create_line(px + cell, py, px + cell, py + cell, fill=shadow, width=1)

    if style == 'info':
        # "Still working" is not bad news: a plain waffle, no sad face.
        return

    # Sad face drawn in dark syrup colour
    face = '#5C2E0E'

    # Eyes — tilted inward (worried/sad brows)
    # Left eye
    lex, ley = cx - 8, cy - 5
    canvas.create_oval(lex - 2, ley - 2, lex + 2, ley + 2, fill=face, outline=face)
    # Left brow — angled down toward centre
    canvas.create_line(lex - 4, ley - 5, lex + 3, ley - 3,
                       fill=face, width=1.5, capstyle=tk.ROUND)

    # Right eye
    rex, rey = cx + 8, cy - 5
    canvas.create_oval(rex - 2, rey - 2, rex + 2, rey + 2, fill=face, outline=face)
    # Right brow — angled down toward centre
    canvas.create_line(rex + 4, rey - 5, rex - 3, rey - 3,
                       fill=face, width=1.5, capstyle=tk.ROUND)

    # Sad mouth — wide frown arc
    my = cy + 7
    canvas.create_arc(cx - 8, my - 4, cx + 8, my + 6,
                      start=0, extent=180, style=tk.ARC,
                      outline=face, width=2)

    # Syrup tear — dripping from right eye
    tear_clr = '#5C2E0E'
    tear_hi  = '#7A3F14'
    tx, ty = rex, rey + 4
    # Teardrop shape: oval body + pointed top
    canvas.create_polygon(
        tx, ty - 2,       # top point
        tx - 3, ty + 4,   # left bulge
        tx, ty + 7,       # bottom
        tx + 3, ty + 4,   # right bulge
        smooth=True, fill=tear_clr, outline=tear_clr)
    # Tiny glossy highlight on tear
    canvas.create_oval(tx - 1, ty + 1, tx + 1, ty + 3,
                       fill=tear_hi, outline=tear_hi)


# How long before a toast auto-dismisses (ms), per style. `cancel` stays
# until the user answers — everything else disappears on its own.
_TOAST_AUTO_HIDE_MS = {"warn": 9000, "error": 9000}

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


def _show_toast(style: str, heading: str, body: str, buttons=None):
    """Show a warm Waffler-branded toast above the waffle.

    ``buttons`` (optional) replaces the style's usual buttons: a list of up
    to three {"label", "action", "kind"} dicts, kind being "primary",
    "secondary" or "danger".
    """
    global _toast_win, _toast_style
    _hide_toast()
    _toast_style = style

    # ── Create the Toplevel BEFORE withdrawing the root pill. ──
    # Tkinter on Windows doesn't reliably render a Toplevel whose master
    # is currently withdrawn — this is the root cause of "rate-limit
    # toasts never appear on Windows" while they show fine on macOS.
    # Build the toast first, then hide the pill.
    _toast_win = tk.Toplevel(_root)
    _toast_win.overrideredirect(True)
    _toast_win.attributes('-topmost', True)
    _toast_win.configure(bg=TRANSPARENT)
    _toast_win.attributes('-transparentcolor', TRANSPARENT)

    tw, th = TOAST_W, TOAST_H
    # Centre toast above the waffle
    tx = _waffle_x + (WAFFLE_W - tw) // 2
    ty = _waffle_y - th - TOAST_PAD
    _toast_win.geometry(f"{tw}x{th}+{tx}+{ty}")

    # Hide the pill now that the toast Toplevel exists and is configured.
    try:
        _root.withdraw()
    except Exception:
        pass

    c = tk.Canvas(_toast_win, width=tw, height=th, bg=TRANSPARENT,
                  highlightthickness=0)
    c.pack()

    # Warm dark background with golden accent border
    _rounded_rect(c, 1, 1, tw - 1, th - 1, 14,
                  fill='#2A1F0E', outline='#C8A256', width=2)

    # Icon — sad waffle centred at top
    icon_cx = tw // 2
    icon_cy = 36
    _draw_sad_waffle(c, icon_cx, icon_cy, style)

    # Heading — centred below icon
    c.create_text(tw // 2, icon_cy + 30, text=heading, fill='#F0E0C0',
                  anchor='n', font=('Segoe UI', 10, 'bold'))

    # Body — centred below heading
    c.create_text(tw // 2, icon_cy + 50, text=body, fill='#A89070',
                  anchor='n', font=('Segoe UI', 9), width=tw - 40)

    # Buttons row — centred
    btn_h = 28
    btn_y = th - btn_h - 12
    btn_gap = 14
    custom = [b for b in (buttons or []) if isinstance(b, dict) and b.get('label')][:3]
    if custom:
        gap = 10
        btn_w = min(118, (tw - 40 - gap * (len(custom) - 1)) // len(custom))
        sx = (tw - (btn_w * len(custom) + gap * (len(custom) - 1))) // 2
        for i, b in enumerate(custom):
            fill, outline, text_clr = TOAST_BTN_KINDS.get(
                b.get('kind'), TOAST_BTN_KINDS['secondary'])
            _draw_toast_btn(c, sx + i * (btn_w + gap), btn_y, btn_w, btn_h,
                            fill, outline, str(b['label']), text_clr,
                            str(b.get('action') or 'dismiss'))
    elif style == 'cancel':
        btn1_w, btn2_w = 100, 110
        total = btn1_w + btn_gap + btn2_w
        sx = (tw - total) // 2
        _draw_toast_btn(c, sx, btn_y, btn1_w, btn_h,
                        '#3D1818', '#D94040', 'Discard', '#D94040', 'confirm')
        _draw_toast_btn(c, sx + btn1_w + btn_gap, btn_y, btn2_w, btn_h,
                        '#C8A256', '#D4A843', 'Keep going', '#2A1F0E', 'dismiss')
    elif style in ('warn', 'info'):
        btn_w = 110
        _draw_toast_btn(c, (tw - btn_w) // 2, btn_y, btn_w, btn_h,
                        '#3D2E14', '#5A4520', 'Dismiss', '#A89070', 'dismiss')
    else:
        btn1_w, btn2_w = 110, 90
        total = btn1_w + btn_gap + btn2_w
        sx = (tw - total) // 2
        _draw_toast_btn(c, sx, btn_y, btn1_w, btn_h,
                        '#C8A256', '#D4A843', 'Select mic', '#2A1F0E', 'select_mic')
        _draw_toast_btn(c, sx + btn1_w + btn_gap, btn_y, btn2_w, btn_h,
                        '#3D2E14', '#5A4520', 'Dismiss', '#A89070', 'dismiss')

    # ── Force the toast to actually paint and stay on top. ──
    # Windows likes to demote -topmost when the active app reclaims focus,
    # and Tkinter sometimes lazily defers paint. Force both: paint now,
    # lift above z-stack, and re-assert topmost a few times in the first
    # couple of seconds so the OS doesn't push us behind the user's app.
    try:
        _toast_win.update_idletasks()
        _toast_win.lift()
    except Exception:
        pass
    # Its buttons ("Keep waiting", "Paste as is") come just before a paste:
    # clicking them must leave the user's app in front.
    _no_activate(_toast_win)

    def _reassert_topmost():
        if _toast_win is None:
            return
        try:
            _toast_win.attributes('-topmost', True)
            _toast_win.lift()
        except Exception:
            pass
        _no_activate(_toast_win)

    # Re-assert topmost at 100/400/1200ms — covers the typical window
    # in which Windows can demote the toast behind a refocusing app.
    for delay_ms in (100, 400, 1200):
        try:
            _toast_win.after(delay_ms, _reassert_topmost)
        except Exception:
            pass

    # Auto-dismiss after the per-style timeout; cancel stays until answered.
    # Bumped warn/error from 6 s -> 9 s so users have time to actually read
    # the rate-limit guidance before it disappears.
    ms = _TOAST_AUTO_HIDE_MS.get(style)
    if ms:
        _toast_win.after(ms, _hide_toast)


def _draw_toast_btn(canvas, x, y, w, h, fill, outline, text, text_color, action):
    """Draw a clickable pill-shaped button on the toast canvas."""
    tag = f'btn_{action}_{x}'
    _rounded_rect(canvas, x, y, x + w, y + h, h // 2,
                         fill=fill, outline=outline, width=1, tags=tag)
    canvas.create_text(x + w // 2, y + h // 2, text=text, fill=text_color,
                       font=('Segoe UI', 8, 'bold'), tags=tag)
    canvas.tag_bind(tag, '<Button-1>', lambda e: _on_toast_action(action))


def _hide_toast(style=None):
    """Destroy the toast popup if visible. With ``style``, only a toast of
    that style goes (the pipeline withdraws its "still working" offer this
    way without touching a message that has replaced it)."""
    global _toast_win, _toast_style
    if style and _toast_style != style:
        return
    if _toast_win:
        try:
            _toast_win.destroy()
        except Exception:
            pass
        _toast_win = None
        _toast_style = None
    # Restore the pill if it was meant to be visible (show_toast hides it).
    if _visible:
        try:
            _root.deiconify()
        except Exception:
            pass
        _no_activate(_root)


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
        # A toast (the "still working" offer, or a message) hides the pill
        # while it is up; _hide_toast brings the pill back afterwards.
        if first and _toast_win is None:
            _root.deiconify()
            _root.lift()
            _root.attributes('-topmost', True)
            _no_activate(_root)


def _end_working(result: str):
    """Leave the working look: a short tick, or straight back to hidden.
    Never touches a toast, so a message that is already up stays readable."""
    global _visible, _mode, _done_after_id
    _cancel_done_timer()
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
        # Hide the waffle grid so only the toast popup is visible
        if _root:
            _root.withdraw()
        _show_toast(
            style=cmd.get("style", "error"),
            heading=cmd.get("heading", ""),
            body=cmd.get("body", ""),
            buttons=cmd.get("buttons"),
        )

    elif ctype == "hide_toast":
        _hide_toast(cmd.get("style"))

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
