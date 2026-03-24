#!/usr/bin/env python3
"""
Waffler Overlay Process — Windows (tkinter)

Runs as a subprocess — owns its own tkinter mainloop.
Receives JSON commands from stdin; emits JSON events on stdout.

Commands:  {"type": "show"}
           {"type": "hide"}
           {"type": "level", "value": 0.0-1.0}
           {"type": "show_toast", "style": "cancel"|"error", "heading": "...", "body": "..."}
           {"type": "hide_toast"}
           {"type": "quit"}

Events:    {"event": "cancel_request"}
           {"event": "stop"}
           {"event": "ready"}
           {"event": "toast_action", "action": "confirm"|"dismiss"|"select_mic"|"troubleshoot"}

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

# Toast constants
TOAST_W     = 380
TOAST_H     = 170
TOAST_PAD   = 12           # gap above waffle

# ── Global state ───────────────────────────────────────────────────────
_cmd_queue: queue.Queue = queue.Queue()
_bars:    list = [0.0] * NUM_CELLS
_targets: list = [0.0] * NUM_CELLS
_visible: bool = False
_root           = None
_canvas         = None
_toast_win      = None
_toast_style    = None

# Screen position (set during init, reused for toast positioning)
_waffle_x = 0
_waffle_y = 0


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

    _canvas.delete("all")

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

    # 4. Cancel button (X) — dark filled circle with red X
    _canvas.create_oval(
        BTN_CANCEL_CX - BTN_R, BTN_CANCEL_CY - BTN_R,
        BTN_CANCEL_CX + BTN_R, BTN_CANCEL_CY + BTN_R,
        fill=BTN_FILL, outline=BTN_RING, width=2
    )
    off = 4
    _canvas.create_line(
        BTN_CANCEL_CX - off, BTN_CANCEL_CY - off,
        BTN_CANCEL_CX + off, BTN_CANCEL_CY + off,
        fill=CANCEL_X_CLR, width=2, capstyle=tk.ROUND
    )
    _canvas.create_line(
        BTN_CANCEL_CX + off, BTN_CANCEL_CY - off,
        BTN_CANCEL_CX - off, BTN_CANCEL_CY + off,
        fill=CANCEL_X_CLR, width=2, capstyle=tk.ROUND
    )

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


# ── Toast popup ────────────────────────────────────────────────────────

def _rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    """Draw a rounded rectangle using tk smooth polygon."""
    # Clamp radius so it doesn't exceed half the width or height
    r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
    if r < 1:
        canvas.create_rectangle(x1, y1, x2, y2, **kwargs)
        return
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
    canvas.create_polygon(pts, smooth=True, **kwargs)


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


def _show_toast(style: str, heading: str, body: str):
    """Show a warm Waffler-branded toast above the waffle."""
    global _toast_win, _toast_style
    _hide_toast()
    _toast_style = style

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
    if style == 'cancel':
        btn1_w, btn2_w = 100, 110
        total = btn1_w + btn_gap + btn2_w
        sx = (tw - total) // 2
        _draw_toast_btn(c, sx, btn_y, btn1_w, btn_h,
                        '#3D1818', '#D94040', 'Discard', '#D94040', 'confirm')
        _draw_toast_btn(c, sx + btn1_w + btn_gap, btn_y, btn2_w, btn_h,
                        '#C8A256', '#D4A843', 'Keep going', '#2A1F0E', 'dismiss')
    else:
        btn1_w, btn2_w = 110, 90
        total = btn1_w + btn_gap + btn2_w
        sx = (tw - total) // 2
        _draw_toast_btn(c, sx, btn_y, btn1_w, btn_h,
                        '#C8A256', '#D4A843', 'Select mic', '#2A1F0E', 'select_mic')
        _draw_toast_btn(c, sx + btn1_w + btn_gap, btn_y, btn2_w, btn_h,
                        '#3D2E14', '#5A4520', 'Dismiss', '#A89070', 'dismiss')


def _draw_toast_btn(canvas, x, y, w, h, fill, outline, text, text_color, action):
    """Draw a clickable pill-shaped button on the toast canvas."""
    tag = f'btn_{action}'
    _rounded_rect(canvas, x, y, x + w, y + h, h // 2,
                         fill=fill, outline=outline, width=1, tags=tag)
    canvas.create_text(x + w // 2, y + h // 2, text=text, fill=text_color,
                       font=('Segoe UI', 8, 'bold'), tags=tag)
    canvas.tag_bind(tag, '<Button-1>', lambda e: _on_toast_action(action))


def _hide_toast():
    """Destroy the toast popup if visible."""
    global _toast_win, _toast_style
    if _toast_win:
        try:
            _toast_win.destroy()
        except Exception:
            pass
        _toast_win = None
        _toast_style = None


def _on_toast_action(action: str):
    """Handle toast button clicks."""
    emit("toast_action", action=action)
    _hide_toast()


# ── Command handler (runs on main/tkinter thread via _animation_loop) ──

def _handle_cmd(cmd: dict):
    global _visible

    ctype = cmd.get("type")

    if ctype == "show":
        _visible = True
        _hide_toast()
        if _root:
            _draw_waffle()
            _root.deiconify()
            _root.lift()
            _root.attributes('-topmost', True)

    elif ctype == "hide":
        _visible = False
        _hide_toast()
        if _root:
            _root.withdraw()

    elif ctype == "level":
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
        )

    elif ctype == "hide_toast":
        _hide_toast()

    elif ctype == "quit":
        _hide_toast()
        if _root:
            try:
                _root.destroy()
            except Exception:
                pass


# ── Animation loop (tkinter after-callback, 50 ms = 20 fps) ───────────

def _animation_loop():
    # Drain the command queue (safe on main thread)
    try:
        while True:
            cmd = _cmd_queue.get_nowait()
            _handle_cmd(cmd)
    except queue.Empty:
        pass

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

    if _root:
        try:
            _root.after(50, _animation_loop)
        except Exception:
            pass


# ── Mouse click ────────────────────────────────────────────────────────

def _on_click(event):
    x, y = event.x, event.y
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
    _root.mainloop()


if __name__ == "__main__":
    main()
