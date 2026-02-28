#!/usr/bin/env python3
"""
Waffler Overlay Process
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
_g_toast_window = None
_g_toast_view = None
TOAST_W = 420
TOAST_H = 160
TOAST_PAD = 14


# ── Helpers ───────────────────────────────────────────────────────────

def emit(event: str, **kwargs):
    """Send JSON event to parent process via stdout."""
    data = {"event": event, **kwargs}
    print(json.dumps(data), flush=True)


def make_rect(x, y, w, h):
    return AppKit.NSMakeRect(x, y, w, h)


# ── Custom NSView ─────────────────────────────────────────────────────

class PillView(NSView):
    """Draws the pill-shaped overlay with waffle pattern and syrup filling effect."""

    NUM_BARS = 16

    def initWithFrame_(self, frame):
        self = objc.super(PillView, self).initWithFrame_(frame)
        if self:
            self._bars   = [0.0] * 16
            self._targets = [0.0] * 16
            self._syrup_level = 0.0  # 0.0 to 1.0 - controls syrup fill height
            self._syrup_target = 0.0
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

        # 2. Pill background - warm cream waffle color
        r = h / 2.0
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, r, r)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.992, 0.961, 0.98).set()  # #FFFDF5
        pill.fill()

        # 3. Waffle grid pattern (subtle)
        self._draw_waffle_grid(b, r)

        # 4. Syrup filling effect (from bottom up based on mic level)
        self._draw_syrup_fill(b, r)

        # Border - golden brown
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.784, 0.635, 0.337, 0.55).set()  # #C8A256
        pill.setLineWidth_(0.5)
        pill.stroke()

        margin  = 8.0
        btn_r   = 12.0

        # 3. Cancel button (X) — left
        x_cx = 20.0
        # Light cream #F0EDE6
        self._draw_circle_button(x_cx, cy, btn_r, (0.941, 0.929, 0.902), border=False)
        self._draw_x(x_cx, cy, 2.5)

        # 4. Stop button (■) — right
        s_cx = w - 20.0
        # Golden #C8A256
        self._draw_circle_button(s_cx, cy, btn_r, (0.784, 0.635, 0.337), border=False)
        self._draw_stop_square(s_cx, cy, 8.0)

        # 5. VU bars — centre
        bars_start = 40.0
        bars_end   = w - 40.0
        self._draw_bars(bars_start, bars_end, cy, h)

    def _draw_circle_button(self, cx, cy, r, color_rgb, border=False):
        rect = make_rect(cx - r, cy - r, r * 2, r * 2)
        circle = NSBezierPath.bezierPathWithOvalInRect_(rect)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            color_rgb[0], color_rgb[1], color_rgb[2], 0.90
        ).set()
        circle.fill()

    def _draw_x(self, cx, cy, off):
        # Dark golden #8B6914
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.545, 0.412, 0.078, 1.0).set()
        p = NSBezierPath.bezierPath()
        p.setLineWidth_(1.5)
        p.setLineCapStyle_(AppKit.NSRoundLineCapStyle)
        p.moveToPoint_(AppKit.NSMakePoint(cx - off, cy - off))
        p.lineToPoint_(AppKit.NSMakePoint(cx + off, cy + off))
        p.moveToPoint_(AppKit.NSMakePoint(cx + off, cy - off))
        p.lineToPoint_(AppKit.NSMakePoint(cx - off, cy + off))
        p.stroke()

    def _draw_stop_square(self, cx, cy, size):
        NSColor.whiteColor().set()
        NSBezierPath.fillRect_(make_rect(cx - size / 2, cy - size / 2, size, size))

    def _draw_waffle_grid(self, bounds, radius):
        """Draw subtle waffle grid pattern."""
        w = bounds.size.width
        h = bounds.size.height

        # Very subtle grid lines
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.784, 0.635, 0.337, 0.08).set()  # #C8A256 at 8% opacity

        # Vertical lines
        grid_spacing = 8.0
        x = grid_spacing
        while x < w - radius:
            path = NSBezierPath.bezierPath()
            path.setLineWidth_(0.5)
            path.moveToPoint_(AppKit.NSMakePoint(x, 2))
            path.lineToPoint_(AppKit.NSMakePoint(x, h - 2))
            path.stroke()
            x += grid_spacing

        # Horizontal lines
        y = grid_spacing
        while y < h - 2:
            path = NSBezierPath.bezierPath()
            path.setLineWidth_(0.5)
            path.moveToPoint_(AppKit.NSMakePoint(radius, y))
            path.lineToPoint_(AppKit.NSMakePoint(w - radius, y))
            path.stroke()
            y += grid_spacing

    def _draw_syrup_fill(self, bounds, radius):
        """Draw syrup filling from bottom based on mic level."""
        w = bounds.size.width
        h = bounds.size.height

        # Animate syrup level smoothly
        changed = False
        if abs(self._syrup_level - self._syrup_target) > 0.01:
            self._syrup_level += (self._syrup_target - self._syrup_level) * 0.25
            changed = True
        else:
            self._syrup_level = self._syrup_target

        if changed:
            self.setNeedsDisplay_(True)

        # Calculate syrup fill height
        syrup_height = h * self._syrup_level
        if syrup_height < 2:
            return  # Don't draw if too small

        # Save graphics state before clipping
        AppKit.NSGraphicsContext.currentContext().saveGraphicsState()

        # Create clipping path for pill shape
        pill_clip = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius)
        pill_clip.addClip()

        # Draw syrup with gradient effect (golden-brown)
        syrup_rect = make_rect(0, 0, w, syrup_height)

        # Create gradient from darker to lighter golden syrup (fully opaque like Windows)
        gradient = AppKit.NSGradient.alloc().initWithColors_(
            [
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.545, 0.373, 0.078, 1.0),  # #8B5F14 darker syrup - OPAQUE
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.784, 0.635, 0.337, 1.0),  # #C8A256 golden - OPAQUE
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.831, 0.690, 0.416, 1.0),  # #D4B06A lighter - OPAQUE
            ]
        )
        gradient.drawInRect_angle_(syrup_rect, 90.0)  # Draw vertically

        # Add subtle drip effect at top of syrup
        if syrup_height > 5:
            self._draw_syrup_drips(w, syrup_height)

        # Restore graphics state to remove clipping
        AppKit.NSGraphicsContext.currentContext().restoreGraphicsState()

    def _draw_syrup_drips(self, width, syrup_top):
        """Draw dripping effect at top of syrup."""
        # Small drips at random-ish positions
        drip_positions = [0.2, 0.4, 0.6, 0.8]
        drip_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.784, 0.635, 0.337, 0.6)

        t = time.time()
        for i, pos in enumerate(drip_positions):
            # Animate drip height
            drip_offset = abs(math.sin(t * 2.0 + i * 1.5)) * 2.0

            x = width * pos
            y = syrup_top + drip_offset

            # Draw small drip circle
            drip_r = 1.5
            drip_rect = make_rect(x - drip_r, y - drip_r, drip_r * 2, drip_r * 2)
            drip_circle = NSBezierPath.bezierPathWithOvalInRect_(drip_rect)
            drip_color.set()
            drip_circle.fill()

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

            # Golden gradient: #8B6914 → #D4AF6A
            r1, g1, b1 = 0x8B/255.0, 0x69/255.0, 0x14/255.0  # Dark golden
            r2, g2, b2 = 0xD4/255.0, 0xAF/255.0, 0x6A/255.0  # Light golden

            if lvl < 0.02:
                # Idle: subtle cream #E8E4DC
                NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.910, 0.894, 0.863, 0.95
                ).set()
            else:
                # Active: golden gradient
                t = lvl
                r = r1 + (r2 - r1) * t
                g = g1 + (g2 - g1) * t
                b = b1 + (b2 - b1) * t
                NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.95).set()

            bar_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                make_rect(bx, by, bar_w, bh), 1.5, 1.5
            )
            bar_path.fill()

    # ── Mouse clicks ──────────────────────────────────────────────────

    def mouseDown_(self, event):
        loc = event.locationInWindow()
        b   = self.bounds()
        w   = b.size.width

        # Click zones for larger buttons
        CANCEL_HIT_X = 36
        STOP_HIT_X = 164

        if loc.x < CANCEL_HIT_X:
            emit("cancel")
        elif loc.x > STOP_HIT_X:
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


# ── Toast View (Error/Warning Popups) ─────────────────────────────────

class ToastView(NSView):
    """Toast notification popup with golden branding matching PC version."""

    def initWithFrame_style_heading_body_(self, frame, style, heading, body):
        self = objc.super(ToastView, self).initWithFrame_(frame)
        if self:
            self._style = style
            self._heading = heading
            self._body = body
            self._button_zones = []
        return self

    def drawRect_(self, rect):
        """Draw toast with dark panel, golden border, icon, text, and buttons."""
        # TODO: Full implementation based on Windows version (overlay_process_windows.py lines 238-386)
        # Dark panel #18181f with golden border #C8A256
        # Icon (red X or golden ! based on style)
        # White heading text, gray body text
        # Horizontal divider
        # Two clickable buttons at bottom
        b = self.bounds()
        w = b.size.width
        h = b.size.height

        # Dark background with golden border
        bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 12.0, 12.0)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.094, 0.094, 0.122, 0.98).set()  # #18181f
        bg.fill()

        # Golden border #C8A256
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.784, 0.635, 0.337, 1.0).set()
        bg.setLineWidth_(2.0)
        bg.stroke()

        # Simple text rendering for now
        # TODO: Add icon, formatted text, divider, and buttons
        pass

    def mouseDown_(self, event):
        """Handle button clicks in toast."""
        loc = event.locationInWindow()
        for (rect, action) in self._button_zones:
            if NSPointInRect(loc, rect):
                emit("toast_action", action=action)
                _hide_toast()
                break


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
            # Update syrup fill level (main visual effect)
            _g_view._syrup_target = level

            # Also update bars for additional visual feedback
            t = time.time()
            targets = []
            for i in range(PillView.NUM_BARS):
                wave  = math.sin(i * 0.9 + t * 4.5) * 0.28 + 0.72
                noise = random.uniform(0.82, 1.0)
                targets.append(level * wave * noise)
            _g_view.setTargets_(targets)

            # Trigger redraw for syrup animation
            _g_view.setNeedsDisplay_(True)

    elif ctype == "show_toast":
        _show_toast(cmd.get("style", "error"), cmd.get("heading", ""), cmd.get("body", ""))

    elif ctype == "hide_toast":
        _hide_toast()

    elif ctype == "quit":
        NSApplication.sharedApplication().terminate_(None)


# ── Toast Functions ──────────────────────────────────────────────────

def _show_toast(style, heading, body):
    """Show toast notification popup above the pill overlay."""
    global _g_toast_window, _g_toast_view, _g_window
    _hide_toast()

    # Get screen dimensions
    screen = NSScreen.mainScreen()
    screen_frame = screen.visibleFrame()
    screen_w = int(screen_frame.size.width)
    screen_h = int(screen_frame.size.height)

    # Position toast above the pill
    tx = (screen_w - TOAST_W) // 2
    # Calculate pill Y position (same logic as main overlay)
    dock_height = 60  # Estimate
    gap = 12
    pill_y = dock_height + gap
    ty = pill_y + 44 + TOAST_PAD  # Above the pill (pill height is 44px)

    # Create toast window
    _g_toast_window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        make_rect(tx, ty, TOAST_W, TOAST_H),
        NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered,
        False
    )
    _g_toast_window.setLevel_(NSFloatingWindowLevel + 2)
    _g_toast_window.setOpaque_(False)
    _g_toast_window.setBackgroundColor_(NSColor.clearColor())
    _g_toast_window.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
        AppKit.NSWindowCollectionBehaviorStationary
    )

    # Create toast view
    _g_toast_view = ToastView.alloc().initWithFrame_style_heading_body_(
        make_rect(0, 0, TOAST_W, TOAST_H), style, heading, body
    )
    _g_toast_window.setContentView_(_g_toast_view)
    _g_toast_window.makeKeyAndOrderFront_(None)


def _hide_toast():
    """Hide and clean up toast popup."""
    global _g_toast_window, _g_toast_view
    if _g_toast_window:
        _g_toast_window.orderOut_(None)
        _g_toast_window = None
        _g_toast_view = None


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

    # Overlay dimensions — compact pill (much smaller: 200x44)
    ow, oh = 200, 44
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
