#!/usr/bin/env python3
"""
VoiceFlow Overlay Process
Runs as a subprocess — owns the macOS main thread for PyObjC.
Receives JSON commands from stdin; emits JSON events on stdout.

Commands:  {"type": "show"}
           {"type": "hide"}
           {"type": "level", "value": 0.0-1.0}
           {"type": "quit"}

Events:    {"event": "cancel"}
           {"event": "stop"}
           {"event": "ready"}
"""

import sys
import json
import threading
import math
import time
import random
import queue

# ── PyObjC ────────────────────────────────────────────────────────────
import objc
import AppKit
from AppKit import (
    NSApplication,
    NSWindow,
    NSView,
    NSColor,
    NSBezierPath,
    NSScreen,
    NSFloatingWindowLevel,
    NSBackingStoreBuffered,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
)
from Foundation import NSObject, NSTimer, NSRunLoop, NSDefaultRunLoopMode

# NSWindowStyleMaskBorderless = 0
NSWindowStyleMaskBorderless = 0

# ── Shared state (written by stdin thread, read by timer on main thread) ──
_cmd_queue: queue.Queue = queue.Queue()
_g_window = None
_g_view = None


# ── Helpers ───────────────────────────────────────────────────────────

def emit(event: str, **kwargs):
    """Send JSON event to parent process via stdout."""
    data = {"event": event, **kwargs}
    print(json.dumps(data), flush=True)


def make_rect(x, y, w, h):
    return AppKit.NSMakeRect(x, y, w, h)


# ── Custom NSView ─────────────────────────────────────────────────────

class PillView(NSView):
    """Draws the pill-shaped overlay with VU bars, cancel & stop buttons."""

    NUM_BARS = 10

    def initWithFrame_(self, frame):
        self = objc.super(PillView, self).initWithFrame_(frame)
        if self:
            self._bars   = [0.0] * self.NUM_BARS
            self._targets = [0.0] * self.NUM_BARS
        return self

    def isOpaque(self):
        return False

    def acceptsFirstResponder(self):
        return True

    # ── Drawing ───────────────────────────────────────────────────────

    def drawRect_(self, rect):
        b = self.bounds()
        w = b.size.width
        h = b.size.height
        cy = h / 2.0

        # 1. Clear
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(b)

        # 2. Pill background (white)
        r = h / 2.0
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, r, r)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.98).set()
        pill.fill()

        # Border
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.75, 0.75, 0.75, 0.55).set()
        pill.setLineWidth_(0.5)
        pill.stroke()

        margin  = 6.0
        btn_r   = 5.0

        # 3. Cancel button (X) — left
        x_cx = margin + btn_r
        self._draw_circle_button(x_cx, cy, btn_r, (0.85, 0.85, 0.85), border=False)
        self._draw_x(x_cx, cy, 2.5)

        # 4. Stop button (■) — right
        s_cx = w - margin - btn_r
        self._draw_circle_button(s_cx, cy, btn_r, (0.85, 0.85, 0.85), border=False)
        self._draw_stop_square(s_cx, cy, 4.0)

        # 5. VU bars — centre
        bars_start = x_cx + btn_r + 6.0
        bars_end   = s_cx - btn_r - 6.0
        self._draw_bars(bars_start, bars_end, cy, h)

    def _draw_circle_button(self, cx, cy, r, color_rgb, border=False):
        rect = make_rect(cx - r, cy - r, r * 2, r * 2)
        circle = NSBezierPath.bezierPathWithOvalInRect_(rect)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            color_rgb[0], color_rgb[1], color_rgb[2], 0.90
        ).set()
        circle.fill()

    def _draw_x(self, cx, cy, off):
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.35, 0.35, 0.35, 1.0).set()
        p = NSBezierPath.bezierPath()
        p.setLineWidth_(1.6)
        p.setLineCapStyle_(AppKit.NSRoundLineCapStyle)
        p.moveToPoint_(AppKit.NSMakePoint(cx - off, cy - off))
        p.lineToPoint_(AppKit.NSMakePoint(cx + off, cy + off))
        p.moveToPoint_(AppKit.NSMakePoint(cx + off, cy - off))
        p.lineToPoint_(AppKit.NSMakePoint(cx - off, cy + off))
        p.stroke()

    def _draw_stop_square(self, cx, cy, size):
        NSColor.whiteColor().set()
        NSBezierPath.fillRect_(make_rect(cx - size / 2, cy - size / 2, size, size))

    def _draw_bars(self, x_start, x_end, cy, h):
        n = self.NUM_BARS
        total_w = x_end - x_start
        bar_w   = 3.0
        spacing = total_w / n
        min_h   = 4.0
        max_h   = h * 0.65

        for i in range(n):
            bh  = min_h + self._bars[i] * (max_h - min_h)
            bx  = x_start + i * spacing + (spacing - bar_w) / 2.0
            by  = cy - bh / 2.0
            lvl = self._bars[i]

            # Dark bars on white background — light up when loud
            brightness = 0.2 + lvl * 0.8
            NSColor.colorWithCalibratedRed_green_blue_alpha_(brightness, brightness, brightness, 0.95).set()

            bar_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                make_rect(bx, by, bar_w, bh), 1.5, 1.5
            )
            bar_path.fill()

    # ── Mouse clicks ──────────────────────────────────────────────────

    def mouseDown_(self, event):
        loc = event.locationInWindow()
        b   = self.bounds()
        w   = b.size.width

        if loc.x < 50:
            emit("cancel")
        elif loc.x > w - 50:
            emit("stop")

    # ── Animation timer ───────────────────────────────────────────────

    def animTick_(self, timer):
        # Drain command queue
        try:
            while True:
                cmd = _cmd_queue.get_nowait()
                _dispatch_cmd(cmd)
        except queue.Empty:
            pass

        # Smooth bar interpolation
        changed = False
        for i in range(self.NUM_BARS):
            diff = self._targets[i] - self._bars[i]
            if abs(diff) > 0.005:
                self._bars[i] += diff * 0.40
                changed = True
            else:
                self._bars[i] = self._targets[i]

        if changed:
            self.setNeedsDisplay_(True)

    def setTargets_(self, targets):
        self._targets = list(targets)


# ── Command dispatcher (runs on main thread via timer) ────────────────

def _dispatch_cmd(cmd):
    global _g_window
    ctype = cmd.get("type")

    if ctype == "show":
        if _g_window:
            _g_window.makeKeyAndOrderFront_(None)
            _g_window.orderFrontRegardless()

    elif ctype == "hide":
        if _g_window:
            _g_window.orderOut_(None)

    elif ctype == "level":
        level = float(cmd.get("value", 0.0))
        if _g_view:
            t = time.time()
            targets = []
            for i in range(PillView.NUM_BARS):
                wave  = math.sin(i * 0.9 + t * 4.5) * 0.28 + 0.72
                noise = random.uniform(0.82, 1.0)
                targets.append(level * wave * noise)
            _g_view.setTargets_(targets)

    elif ctype == "quit":
        NSApplication.sharedApplication().terminate_(None)


# ── Stdin reader (background thread) ─────────────────────────────────

def _stdin_reader():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
            _cmd_queue.put(cmd)
        except json.JSONDecodeError:
            pass
    # stdin closed — quit
    _cmd_queue.put({"type": "quit"})


# ── Main ──────────────────────────────────────────────────────────────

def main():
    global _g_window, _g_view

    # Background stdin reader
    threading.Thread(target=_stdin_reader, daemon=True, name="StdinReader").start()

    # NSApplication
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    # Screen geometry
    screen      = NSScreen.mainScreen()
    sf          = screen.frame()
    vf          = screen.visibleFrame()
    sw, sh      = sf.size.width, sf.size.height
    dock_height = sf.size.height - vf.size.height - vf.origin.y  # dock
    gap         = 16  # px above dock — sit close to the bottom

    # Overlay dimensions — compact pill (much smaller: 120x24)
    ow, oh = 120, 24
    ox = (sw - ow) / 2.0
    oy = dock_height + gap

    # Create NSWindow (borderless)
    _g_window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        make_rect(ox, oy, ow, oh),
        NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered,
        False,
    )
    _g_window.setLevel_(NSFloatingWindowLevel + 1)
    _g_window.setOpaque_(False)
    _g_window.setBackgroundColor_(NSColor.clearColor())
    _g_window.setHasShadow_(True)
    _g_window.setIgnoresMouseEvents_(False)
    _g_window.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
    )

    # Create custom view
    _g_view = PillView.alloc().initWithFrame_(make_rect(0, 0, ow, oh))
    _g_window.setContentView_(_g_view)

    # Animation + command-drain timer (50 ms)
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.05, _g_view, b"animTick:", None, True
    )
    NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSDefaultRunLoopMode)

    emit("ready")
    app.run()


if __name__ == "__main__":
    main()
