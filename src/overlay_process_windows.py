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
BG_COLOR     = '#FFFDF5'   # Warm cream waffle background
BORDER_CLR   = '#C8A256'   # Golden-brown border
CANCEL_BG    = '#F0EDE6'   # Cancel button background
CANCEL_X_CLR = '#8B6914'   # Cancel X mark colour (dark golden)
STOP_BG      = '#C8A256'   # Stop button background (golden)
STOP_ICON    = '#ffffff'   # Stop icon colour (white for contrast)
BAR_IDLE     = '#E8E4DC'   # Idle bar colour (subtle cream)
SYRUP_DARK   = '#8B5F14'   # Dark syrup color
SYRUP_MID    = '#C8A256'   # Mid syrup color
SYRUP_LIGHT  = '#D4B06A'   # Light syrup color
WAFFLE_GRID  = '#C8A256'   # Waffle grid lines

NUM_BARS    = 16
WIN_W       = 200
WIN_H       = 44
RADIUS      = WIN_H // 2   # = 22 — radius of the pill caps
BTN_R       = 12           # Button circle radius

# Click-zone boundaries (x pixel)
CANCEL_HIT_X = 36          # left of this → cancel
STOP_HIT_X   = WIN_W - 36  # right of this → stop

# Toast constants
TOAST_W     = 420
TOAST_H     = 160
TOAST_PAD   = 14           # gap above pill

# ── Global state ───────────────────────────────────────────────────────
_cmd_queue: queue.Queue = queue.Queue()
_bars:    list = [0.0] * NUM_BARS
_targets: list = [0.0] * NUM_BARS
_syrup_level: float = 0.0  # Current syrup fill level (0.0 to 1.0)
_syrup_target: float = 0.0  # Target syrup fill level for smooth animation
_visible: bool = False
_root           = None
_canvas         = None
_toast_win      = None
_toast_style    = None

# Screen position (set during init, reused for toast positioning)
_pill_x = 0
_pill_y = 0
_screen_w = 0


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
    cx = 20
    _canvas.create_oval(cx - BTN_R, cy - BTN_R, cx + BTN_R, cy + BTN_R,
                        fill=CANCEL_BG, outline=CANCEL_BG)
    off = 4
    _canvas.create_line(cx - off, cy - off, cx + off, cy + off,
                        fill=CANCEL_X_CLR, width=1.5, capstyle=tk.ROUND)
    _canvas.create_line(cx + off, cy - off, cx - off, cy + off,
                        fill=CANCEL_X_CLR, width=1.5, capstyle=tk.ROUND)

    # 4. Stop button (■) — right, accent purple with white square
    sx = W - 20
    _canvas.create_oval(sx - BTN_R, cy - BTN_R, sx + BTN_R, cy + BTN_R,
                        fill=STOP_BG, outline=STOP_BG)
    sq = 8
    _canvas.create_rectangle(sx - sq // 2, cy - sq // 2,
                              sx + sq // 2, cy + sq // 2,
                              fill=STOP_ICON, outline=STOP_ICON)

    # 5. Waffle grid pattern (subtle)
    _draw_waffle_grid(W, H, R)

    # 6. Syrup filling effect (from bottom up)
    _draw_syrup_fill(W, H, R, cy)

    # 7. VU bars — centre zone (lighter now for contrast with syrup)
    _draw_vu_bars(40, W - 40, H, cy)


def _draw_waffle_grid(W: int, H: int, R: int):
    """Draw subtle waffle grid pattern."""
    grid_spacing = 10
    grid_color = f'{WAFFLE_GRID}15'  # Very transparent

    # Vertical lines
    x = grid_spacing
    while x < W - R:
        _canvas.create_line(x, 2, x, H - 2, fill=grid_color, width=0.5)
        x += grid_spacing

    # Horizontal lines
    y = grid_spacing
    while y < H - 2:
        _canvas.create_line(R, y, W - R, y, fill=grid_color, width=0.5)
        y += grid_spacing


def _draw_syrup_fill(W: int, H: int, R: int, cy: int):
    """Draw syrup filling from bottom up based on mic level."""
    global _syrup_level, _syrup_target

    # Smooth animation towards target
    if abs(_syrup_level - _syrup_target) > 0.01:
        _syrup_level += (_syrup_target - _syrup_level) * 0.25

    syrup_height = int(H * _syrup_level)
    if syrup_height < 2:
        return

    # Create syrup gradient (darker at bottom, lighter at top)
    # Draw multiple thin rectangles to simulate gradient
    gradient_steps = min(20, syrup_height)
    for i in range(gradient_steps):
        y = H - syrup_height + (i * syrup_height // gradient_steps)
        h = max(1, syrup_height // gradient_steps)

        # Interpolate color from dark to light
        ratio = i / max(1, gradient_steps - 1)
        # Dark syrup #8B5F14 to light syrup #D4B06A
        r = int(0x8B + (0xD4 - 0x8B) * ratio)
        g = int(0x5F + (0xB0 - 0x5F) * ratio)
        b = int(0x14 + (0x6A - 0x14) * ratio)
        color = f'#{r:02x}{g:02x}{b:02x}'

        # Draw syrup layer (rounded at edges)
        _canvas.create_rectangle(R, y, W - R, y + h, fill=color, outline=color)

    # Rounded bottom caps
    _canvas.create_arc(0, H - R * 2, R * 2, H,
                      start=180, extent=90,
                      fill=SYRUP_DARK, outline=SYRUP_DARK, style=tk.PIESLICE)
    _canvas.create_arc(W - R * 2, H - R * 2, W, H,
                      start=270, extent=90,
                      fill=SYRUP_DARK, outline=SYRUP_DARK, style=tk.PIESLICE)


def _draw_vu_bars(x_start: int, x_end: int, H: int, cy: int):
    """Draw the animated VU bars in the centre of the pill."""
    total_w = x_end - x_start
    bar_w   = 3
    spacing = total_w / NUM_BARS
    min_h   = 4
    max_h   = int(H * 0.65)

    # Golden gradient: #8B6914 → #D4AF6A
    r1, g1, b1 = 0x8B, 0x69, 0x14
    r2, g2, b2 = 0xD4, 0xAF, 0x6A

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
            # Active: interpolate golden (dim when quiet, bright when loud)
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
    # Centre horizontally on screen, sit above pill vertically
    tx = (_screen_w - tw) // 2
    ty = _pill_y - th - TOAST_PAD
    _toast_win.geometry(f"{tw}x{th}+{tx}+{ty}")

    c = tk.Canvas(_toast_win, width=tw, height=th, bg=TRANSPARENT,
                  highlightthickness=0)
    c.pack()

    # Shadow layer (offset darker rect behind the main panel)
    _rounded_rect(c, 4, 4, tw, th, 14, fill='#0c0c10', outline='#0c0c10')

    # Main panel — dark background with subtle golden border
    _rounded_rect(c, 0, 0, tw - 4, th - 4, 14,
                  fill='#18181f', outline='#C8A256', width=2)

    # ── Icon ──
    icon_x, icon_y = 32, 44

    if style == 'cancel':
        # Red warning circle with X
        c.create_oval(icon_x - 15, icon_y - 15, icon_x + 15, icon_y + 15,
                      fill='#2d1520', outline='#ef4444', width=1.5)
        off = 6
        c.create_line(icon_x - off, icon_y - off, icon_x + off, icon_y + off,
                      fill='#ef4444', width=2, capstyle=tk.ROUND)
        c.create_line(icon_x + off, icon_y - off, icon_x - off, icon_y + off,
                      fill='#ef4444', width=2, capstyle=tk.ROUND)
    else:
        # Golden info circle with !
        c.create_oval(icon_x - 15, icon_y - 15, icon_x + 15, icon_y + 15,
                      fill='#3d2f1a', outline='#D4AF6A', width=1.5)
        c.create_text(icon_x, icon_y - 1, text='!', fill='#D4AF6A',
                      font=('Segoe UI', 12, 'bold'))

    # ── Text ──
    text_x = 60
    c.create_text(text_x, icon_y - 16, text=heading, fill='#ffffff',
                  anchor='nw', font=('Segoe UI', 11, 'bold'))
    c.create_text(text_x, icon_y + 6, text=body, fill='#8888a0',
                  anchor='nw', font=('Segoe UI', 9), width=tw - text_x - 28)

    # ── Divider line ──
    div_y = th - 58
    c.create_line(16, div_y, tw - 20, div_y, fill='#2a2a35', width=1)

    # ── Buttons row — centred ──
    btn_h = 30
    btn_y = th - btn_h - 18
    btn_gap = 14
    if style == 'cancel':
        btn1_w, btn2_w = 110, 120
        total = btn1_w + btn_gap + btn2_w
        sx = (tw - 4 - total) // 2   # account for shadow offset
        _draw_toast_btn(c, sx, btn_y, btn1_w, btn_h,
                        '#2d1520', '#ef4444', 'Discard', '#ef4444', 'confirm')
        _draw_toast_btn(c, sx + btn1_w + btn_gap, btn_y, btn2_w, btn_h,
                        '#7c3aed', '#7c3aed', 'Keep going', '#ffffff', 'dismiss')
    else:
        btn1_w, btn2_w = 120, 100
        total = btn1_w + btn_gap + btn2_w
        sx = (tw - 4 - total) // 2
        _draw_toast_btn(c, sx, btn_y, btn1_w, btn_h,
                        '#7c3aed', '#7c3aed', 'Select mic', '#ffffff', 'select_mic')
        _draw_toast_btn(c, sx + btn1_w + btn_gap, btn_y, btn2_w, btn_h,
                        '#2a2a35', '#3a3a4a', 'Dismiss', '#9a9aaa', 'dismiss')


def _draw_toast_btn(canvas, x, y, w, h, fill, outline, text, text_color, action):
    """Draw a clickable button on the toast canvas."""
    tag = f'btn_{action}'
    r = h // 2

    # Single-polygon rounded-rect button (no seam artifacts)
    pts = []
    n = 10
    for i in range(n + 1):
        a = math.pi / 2 - i * (math.pi / 2) / n
        pts.extend([x + w - r + r * math.cos(a), y + r - r * math.sin(a)])
    for i in range(n + 1):
        a = -i * (math.pi / 2) / n
        pts.extend([x + w - r + r * math.cos(a), y + h - r - r * math.sin(a)])
    for i in range(n + 1):
        a = -math.pi / 2 - i * (math.pi / 2) / n
        pts.extend([x + r + r * math.cos(a), y + h - r - r * math.sin(a)])
    for i in range(n + 1):
        a = math.pi - i * (math.pi / 2) / n
        pts.extend([x + r + r * math.cos(a), y + r - r * math.sin(a)])
    canvas.create_polygon(pts, fill=fill, outline=outline, width=1.5, tags=tag)

    # Label
    canvas.create_text(x + w // 2, y + h // 2, text=text, fill=text_color,
                       font=('Segoe UI', 9, 'bold'), tags=tag)

    canvas.tag_bind(tag, '<Button-1>', lambda e: _on_toast_action(action))


def _rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    """Draw a rounded rectangle as a single polygon (no seam artifacts)."""
    pts = []
    n = 16  # segments per corner arc — smooth enough at any size
    for i in range(n + 1):                       # top-right
        a = math.pi / 2 - i * (math.pi / 2) / n
        pts.extend([x2 - r + r * math.cos(a), y1 + r - r * math.sin(a)])
    for i in range(n + 1):                       # bottom-right
        a = -i * (math.pi / 2) / n
        pts.extend([x2 - r + r * math.cos(a), y2 - r - r * math.sin(a)])
    for i in range(n + 1):                       # bottom-left
        a = -math.pi / 2 - i * (math.pi / 2) / n
        pts.extend([x1 + r + r * math.cos(a), y2 - r - r * math.sin(a)])
    for i in range(n + 1):                       # top-left
        a = math.pi - i * (math.pi / 2) / n
        pts.extend([x1 + r + r * math.cos(a), y1 + r - r * math.sin(a)])
    canvas.create_polygon(pts, **kwargs)


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
        global _syrup_target
        level = max(0.0, min(1.0, float(cmd.get("value", 0.0))))

        # Update syrup fill level (main visual effect)
        _syrup_target = level

        # Also update bars for additional visual feedback
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
    global _root, _canvas, _pill_x, _pill_y, _screen_w

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

    _screen_w = screen_w
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
