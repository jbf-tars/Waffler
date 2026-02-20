#!/usr/bin/env python3
"""
Natter Overlay Process — Windows (tkinter)

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
BG_COLOR     = '#1e1e24'   # Pill background (slightly lighter for depth)
BORDER_CLR   = '#7c3aed'   # Pill border (--accent purple)
CANCEL_BG    = '#2a2a35'   # Cancel button background
CANCEL_X_CLR = '#ef4444'   # Cancel X mark colour (--red)
STOP_BG      = '#7c3aed'   # Stop button background (--accent)
STOP_ICON    = '#ffffff'   # Stop icon colour (white for contrast)
BAR_IDLE     = '#3a3a4a'   # Idle bar colour (subtle)

NUM_BARS    = 12
WIN_W       = 104          # Slightly narrower
WIN_H       = 30           # Slightly taller for better proportions
RADIUS      = WIN_H // 2   # = 15 — radius of the pill caps
BTN_R       = 7            # Button circle radius

# Click-zone boundaries (x pixel)
CANCEL_HIT_X = 22          # left of this → cancel
STOP_HIT_X   = WIN_W - 22  # right of this → stop

# Toast constants
TOAST_W     = 260
TOAST_H     = 100
TOAST_PAD   = 10           # gap above pill

# ── Global state ───────────────────────────────────────────────────────
_cmd_queue: queue.Queue = queue.Queue()
_bars:    list = [0.0] * NUM_BARS
_targets: list = [0.0] * NUM_BARS
_visible: bool = False
_root           = None
_canvas         = None
_toast_win      = None
_toast_style    = None

# Screen position (set during init, reused for toast positioning)
_pill_x = 0
_pill_y = 0


# ── IPC helpers ────────────────────────────────────────────────────────

def emit(event: str, **kwargs):
    """Send a JSON event to the parent process via stdout."""
    data = {"event": event, **kwargs}
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


# ── Drawing ────────────────────────────────────────────────────────────

def _draw_pill():
    """Redraw the entire overlay canvas."""
    if _canvas is None:
        return

    _canvas.delete("all")
    W, H = WIN_W, WIN_H
    R = RADIUS
    cy = H // 2

    # 1. Pill body — filled rounded rectangle
    _canvas.create_oval(0, 0, R * 2, H, fill=BG_COLOR, outline=BG_COLOR)
    _canvas.create_oval(W - R * 2, 0, W, H, fill=BG_COLOR, outline=BG_COLOR)
    _canvas.create_rectangle(R, 0, W - R, H, fill=BG_COLOR, outline=BG_COLOR)

    # 2. Pill border (smooth arcs + top/bottom lines)
    bw = 1.5
    _canvas.create_arc(0, 0, R * 2, H,
                       start=90, extent=180,
                       outline=BORDER_CLR, style=tk.ARC, width=bw)
    _canvas.create_arc(W - R * 2, 0, W, H,
                       start=270, extent=180,
                       outline=BORDER_CLR, style=tk.ARC, width=bw)
    _canvas.create_line(R, 0, W - R, 0, fill=BORDER_CLR, width=bw)
    _canvas.create_line(R, H - 1, W - R, H - 1, fill=BORDER_CLR, width=bw)

    # 3. Cancel button (X) — left
    cx = 13
    _canvas.create_oval(cx - BTN_R, cy - BTN_R, cx + BTN_R, cy + BTN_R,
                        fill=CANCEL_BG, outline=CANCEL_BG)
    off = 3
    _canvas.create_line(cx - off, cy - off, cx + off, cy + off,
                        fill=CANCEL_X_CLR, width=1.5, capstyle=tk.ROUND)
    _canvas.create_line(cx + off, cy - off, cx - off, cy + off,
                        fill=CANCEL_X_CLR, width=1.5, capstyle=tk.ROUND)

    # 4. Stop button (■) — right, accent purple with white square
    sx = W - 13
    _canvas.create_oval(sx - BTN_R, cy - BTN_R, sx + BTN_R, cy + BTN_R,
                        fill=STOP_BG, outline=STOP_BG)
    sq = 6
    _canvas.create_rectangle(sx - sq // 2, cy - sq // 2,
                              sx + sq // 2, cy + sq // 2,
                              fill=STOP_ICON, outline=STOP_ICON)

    # 5. VU bars — centre zone
    _draw_vu_bars(24, W - 24, H, cy)


def _draw_vu_bars(x_start: int, x_end: int, H: int, cy: int):
    """Draw the animated VU bars in the centre of the pill."""
    total_w = x_end - x_start
    bar_w   = 2.5
    spacing = total_w / NUM_BARS
    min_h   = 3
    max_h   = int(H * 0.6)

    # Purple gradient: --accent (#7c3aed) → --accent-text (#a78bfa)
    r1, g1, b1 = 0x7c, 0x3a, 0xed
    r2, g2, b2 = 0xa7, 0x8b, 0xfa

    for i in range(NUM_BARS):
        lvl = max(0.0, min(1.0, _bars[i]))
        bh  = max(min_h, int(min_h + lvl * (max_h - min_h)))
        bx  = int(x_start + i * spacing + (spacing - bar_w) / 2)
        by  = cy - bh // 2

        if lvl < 0.02:
            # Idle: show subtle grey bars
            color = BAR_IDLE
            bh = min_h
            by = cy - bh // 2
        else:
            # Active: interpolate purple (dim when quiet, bright when loud)
            t = lvl
            r = int(r1 + (r2 - r1) * t)
            g = int(g1 + (g2 - g1) * t)
            b = int(b1 + (b2 - b1) * t)
            color = f'#{r:02x}{g:02x}{b:02x}'

        # Draw bar with rounded ends (small ovals at top and bottom)
        _canvas.create_rectangle(bx, by, bx + bar_w, by + bh,
                                 fill=color, outline=color)


# ── Toast popup ────────────────────────────────────────────────────────

def _show_toast(style: str, heading: str, body: str):
    """Show a dark floating toast above the pill."""
    global _toast_win, _toast_style
    _hide_toast()
    _toast_style = style

    _toast_win = tk.Toplevel(_root)
    _toast_win.overrideredirect(True)
    _toast_win.attributes('-topmost', True)
    _toast_win.configure(bg=TRANSPARENT)
    _toast_win.attributes('-transparentcolor', TRANSPARENT)

    # Toast dimensions
    tw, th = TOAST_W, TOAST_H
    # Position above pill, centred
    tx = _pill_x + (WIN_W - tw) // 2
    ty = _pill_y - th - TOAST_PAD
    _toast_win.geometry(f"{tw}x{th}+{tx}+{ty}")

    c = tk.Canvas(_toast_win, width=tw, height=th, bg=TRANSPARENT,
                  highlightthickness=0)
    c.pack()

    # Background rounded rectangle
    rad = 10
    bg = '#1e1e24'
    border = '#ef4444' if style == 'cancel' else '#f59e0b'
    _rounded_rect(c, 0, 0, tw, th, rad, fill=bg, outline=border, width=1.5)

    # Icon
    icon_x, icon_y = 18, 22
    if style == 'cancel':
        # Red circle with X
        c.create_oval(icon_x - 8, icon_y - 8, icon_x + 8, icon_y + 8,
                      fill='#3b1818', outline='#ef4444', width=1.5)
        c.create_line(icon_x - 4, icon_y - 4, icon_x + 4, icon_y + 4,
                      fill='#ef4444', width=1.5, capstyle=tk.ROUND)
        c.create_line(icon_x + 4, icon_y - 4, icon_x - 4, icon_y + 4,
                      fill='#ef4444', width=1.5, capstyle=tk.ROUND)
    else:
        # Amber warning triangle
        c.create_oval(icon_x - 8, icon_y - 8, icon_x + 8, icon_y + 8,
                      fill='#3b2e10', outline='#f59e0b', width=1.5)
        c.create_text(icon_x, icon_y, text='!', fill='#f59e0b',
                      font=('Segoe UI', 10, 'bold'))

    # Heading
    c.create_text(36, 16, text=heading, fill='#ffffff', anchor='nw',
                  font=('Segoe UI', 9, 'bold'))

    # Body text
    c.create_text(36, 34, text=body, fill='#9a9aaa', anchor='nw',
                  font=('Segoe UI', 8), width=tw - 46)

    # Buttons row
    btn_y = th - 26
    if style == 'cancel':
        # "Discard" button (red)
        _draw_toast_btn(c, 14, btn_y, 72, 20, '#3b1818', '#ef4444',
                        'Discard', '#ef4444', 'confirm')
        # "Keep going" button (grey)
        _draw_toast_btn(c, 92, btn_y, 72, 20, '#2a2a35', '#3a3a4a',
                        'Keep going', '#cccccc', 'dismiss')
    else:
        # "Select mic" button
        _draw_toast_btn(c, 14, btn_y, 84, 20, '#2a2a35', '#3a3a4a',
                        'Select mic', '#cccccc', 'select_mic')
        # "Dismiss" button
        _draw_toast_btn(c, 104, btn_y, 64, 20, '#2a2a35', '#3a3a4a',
                        'Dismiss', '#999999', 'dismiss')


def _draw_toast_btn(canvas, x, y, w, h, fill, outline, text, text_color, action):
    """Draw a clickable button on the toast canvas."""
    rad = h // 2
    tag = f'btn_{action}'

    # Rounded rectangle
    canvas.create_oval(x, y, x + h, y + h, fill=fill, outline=outline, tags=tag)
    canvas.create_oval(x + w - h, y, x + w, y + h, fill=fill, outline=outline, tags=tag)
    canvas.create_rectangle(x + rad, y, x + w - rad, y + h,
                            fill=fill, outline=fill, tags=tag)

    # Text
    canvas.create_text(x + w // 2, y + h // 2, text=text, fill=text_color,
                       font=('Segoe UI', 7, 'bold'), tags=tag)

    canvas.tag_bind(tag, '<Button-1>', lambda e: _on_toast_action(action))


def _rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    """Draw a rounded rectangle on a canvas."""
    canvas.create_arc(x1, y1, x1 + r * 2, y1 + r * 2,
                      start=90, extent=90, style=tk.PIESLICE, **kwargs)
    canvas.create_arc(x2 - r * 2, y1, x2, y1 + r * 2,
                      start=0, extent=90, style=tk.PIESLICE, **kwargs)
    canvas.create_arc(x2 - r * 2, y2 - r * 2, x2, y2,
                      start=270, extent=90, style=tk.PIESLICE, **kwargs)
    canvas.create_arc(x1, y2 - r * 2, x1 + r * 2, y2,
                      start=180, extent=90, style=tk.PIESLICE, **kwargs)
    # Remove outline from fill rectangles
    fill_kw = {k: v for k, v in kwargs.items() if k != 'outline' and k != 'width'}
    fill_kw['outline'] = kwargs.get('fill', '')
    canvas.create_rectangle(x1 + r, y1, x2 - r, y2, **fill_kw)
    canvas.create_rectangle(x1, y1 + r, x2, y2 - r, **fill_kw)


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
            _draw_pill()
            _root.deiconify()
            _root.lift()
            _root.attributes('-topmost', True)

    elif ctype == "hide":
        _visible = False
        _hide_toast()
        if _root:
            _root.withdraw()

    elif ctype == "level":
        level = max(0.0, min(1.0, float(cmd.get("value", 0.0))))
        t = time.time()
        for i in range(NUM_BARS):
            wave  = math.sin(i * 0.9 + t * 4.5) * 0.28 + 0.72
            noise = random.uniform(0.82, 1.0)
            _targets[i] = level * wave * noise

    elif ctype == "show_toast":
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
    for i in range(NUM_BARS):
        diff = _targets[i] - _bars[i]
        if abs(diff) > 0.005:
            _bars[i] += diff * 0.35
            changed = True
        else:
            _bars[i] = _targets[i]

    if changed and _visible:
        _draw_pill()

    if _root:
        try:
            _root.after(50, _animation_loop)
        except Exception:
            pass


# ── Mouse click ────────────────────────────────────────────────────────

def _on_click(event):
    x = event.x
    if x < CANCEL_HIT_X:
        emit("cancel_request")
    elif x > STOP_HIT_X:
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
    global _root, _canvas, _pill_x, _pill_y

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

    _pill_x = (screen_w - WIN_W) // 2
    _pill_y = screen_h - taskbar_h - WIN_H - gap
    _root.geometry(f"{WIN_W}x{WIN_H}+{_pill_x}+{_pill_y}")

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
