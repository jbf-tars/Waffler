#!/usr/bin/env python3
"""
VoiceFlow Overlay Process — Windows (tkinter)

Runs as a subprocess — owns its own tkinter mainloop.
Receives JSON commands from stdin; emits JSON events on stdout.

Commands:  {"type": "show"}
           {"type": "hide"}
           {"type": "level", "value": 0.0-1.0}
           {"type": "quit"}

Events:    {"event": "cancel"}
           {"event": "stop"}
           {"event": "ready"}

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
TRANSPARENT = '#010101'   # Magic colour used for window-level transparency
BG_COLOR    = '#ffffff'   # Pill background (white)
BORDER_CLR  = '#b0b0b0'   # Pill border (light grey)

NUM_BARS    = 10
WIN_W       = 120         # 1/4 size (was 260)
WIN_H       = 24          # 1/4 size (was 48)
RADIUS      = WIN_H // 2  # = 12 — radius of the pill caps

# Click-zone boundaries (x pixel)
CANCEL_HIT_X = 20          # left of this → cancel
STOP_HIT_X   = WIN_W - 20  # right of this → stop

# ── Global state ───────────────────────────────────────────────────────
_cmd_queue: queue.Queue = queue.Queue()
_bars:    list = [0.0] * NUM_BARS
_targets: list = [0.0] * NUM_BARS
_visible: bool = False
_root           = None
_canvas         = None


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

    # 1. Pill body
    # Left cap
    _canvas.create_oval(0, 0, R * 2, H, fill=BG_COLOR, outline=BG_COLOR)
    # Right cap
    _canvas.create_oval(W - R * 2, 0, W, H, fill=BG_COLOR, outline=BG_COLOR)
    # Middle rectangle
    _canvas.create_rectangle(R, 0, W - R, H, fill=BG_COLOR, outline=BG_COLOR)

    # 2. Pill border (arcs + top/bottom lines)
    _canvas.create_arc(0, 0, R * 2, H,
                       start=90, extent=180,
                       outline=BORDER_CLR, style=tk.ARC, width=1)
    _canvas.create_arc(W - R * 2, 0, W, H,
                       start=270, extent=180,
                       outline=BORDER_CLR, style=tk.ARC, width=1)
    _canvas.create_line(R, 0, W - R, 0, fill=BORDER_CLR, width=1)
    _canvas.create_line(R, H - 1, W - R, H - 1, fill=BORDER_CLR, width=1)

    # 3. Cancel button (X) — left (smaller for compact size)
    btn_r = 5
    cx = 10
    _canvas.create_oval(cx - btn_r, cy - btn_r, cx + btn_r, cy + btn_r,
                        fill='#d0d0d0', outline='#d0d0d0')
    off = 3
    _canvas.create_line(cx - off, cy - off, cx + off, cy + off,
                        fill='#404040', width=1.5, capstyle=tk.ROUND)
    _canvas.create_line(cx + off, cy - off, cx - off, cy + off,
                        fill='#404040', width=1.5, capstyle=tk.ROUND)

    # 4. Stop button (■) — right
    sx = W - 10
    _canvas.create_oval(sx - btn_r, cy - btn_r, sx + btn_r, cy + btn_r,
                        fill='#d0d0d0', outline='#d0d0d0')
    sq = 5
    _canvas.create_rectangle(sx - sq // 2, cy - sq // 2,
                              sx + sq // 2, cy + sq // 2,
                              fill='#404040', outline='#404040')

    # 5. VU bars — centre zone
    _draw_vu_bars(20, W - 20, H, cy)


def _draw_vu_bars(x_start: int, x_end: int, H: int, cy: int):
    """Draw the animated VU bars in the centre of the pill."""
    total_w = x_end - x_start
    bar_w   = 3
    spacing = total_w / NUM_BARS
    min_h   = 2
    max_h   = int(H * 0.7)

    for i in range(NUM_BARS):
        lvl = max(0.0, min(1.0, _bars[i]))
        bh  = max(min_h, int(min_h + lvl * (max_h - min_h)))
        bx  = int(x_start + i * spacing + (spacing - bar_w) / 2)
        by  = cy - bh // 2

        # Dark grey when quiet, white when loud (white background)
        brightness = int(0.25 + lvl * 0.75)  # 0.25→1.0
        color = f'#{brightness:02x}{brightness:02x}{brightness:02x}'

        _canvas.create_rectangle(bx, by, bx + bar_w, by + bh,
                                 fill=color, outline=color)


# ── Command handler (runs on main/tkinter thread via _animation_loop) ──

def _handle_cmd(cmd: dict):
    global _visible

    ctype = cmd.get("type")

    if ctype == "show":
        _visible = True
        if _root:
            _draw_pill()
            _root.deiconify()
            _root.lift()
            _root.attributes('-topmost', True)

    elif ctype == "hide":
        _visible = False
        if _root:
            _root.withdraw()

    elif ctype == "level":
        level = max(0.0, min(1.0, float(cmd.get("value", 0.0))))
        t = time.time()
        for i in range(NUM_BARS):
            wave  = math.sin(i * 0.9 + t * 4.5) * 0.28 + 0.72
            noise = random.uniform(0.82, 1.0)
            _targets[i] = level * wave * noise

    elif ctype == "quit":
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
            _bars[i] += diff * 0.40
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
        emit("cancel")
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
    global _root, _canvas

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

    x = (screen_w - WIN_W) // 2
    y = screen_h - taskbar_h - WIN_H - gap
    _root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

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
