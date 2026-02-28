#!/usr/bin/env python3
"""
Waffler Overlay Process — macOS (PyObjC)

Runs as a subprocess — owns its own PyObjC mainloop.
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
    NSRoundLineCapStyle,
    NSFont,
    NSAttributedString,
    NSForegroundColorAttributeName,
    NSFontAttributeName,
    NSMutableParagraphStyle,
    NSCenterTextAlignment,
)
from Foundation import NSObject, NSTimer, NSRunLoop, NSDefaultRunLoopMode, NSMakePoint, NSMakeRect

NSWindowStyleMaskBorderless = 0


# ── Custom Window Class ───────────────────────────────────────────────

class ClickableWindow(NSWindow):
    """Window that accepts mouse clicks even when not key."""
    def canBecomeKeyWindow(self):
        return True
    def canBecomeMainWindow(self):
        return False


# ── Constants ──────────────────────────────────────────────────────────

# Waffle palette — warm, realistic waffle tones (EXACT Windows colors)
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
_g_window       = None
_g_view         = None
_toast_win      = None
_toast_style    = None

# Screen position (set during init, reused for toast positioning)
_waffle_x = 0.0
_waffle_y = 0.0


# ── IPC helpers ────────────────────────────────────────────────────────

def emit(event: str, **kwargs):
    """Send a JSON event to the parent process via stdout."""
    data = {"event": event, **kwargs}
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


# ── Color helpers ──────────────────────────────────────────────────────

def hex_to_ns_color(hex_str):
    """Convert hex color like '#D4A843' to NSColor."""
    hex_str = hex_str.lstrip('#')
    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)


# ── Custom NSView ──────────────────────────────────────────────────────

class WaffleView(NSView):
    """Draws the square waffle overlay with 4x4 grid and syrup filling effect."""

    def initWithFrame_(self, frame):
        self = objc.super(WaffleView, self).initWithFrame_(frame)
        if self:
            self._bars   = [0.0] * NUM_CELLS
            self._targets = [0.0] * NUM_CELLS
        return self

    def isOpaque(self):
        return False

    def isFlipped(self):
        return True  # Use top-left origin like Windows

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse_(self, event):
        """Accept clicks even when window is not key."""
        return True

    # ── Drawing ───────────────────────────────────────────────────────

    def drawRect_(self, rect):
        """Redraw the entire overlay as a realistic waffle with syrup."""
        b = self.bounds()

        # 1. Clear background
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(b)

        # 2. Waffle body — rounded rectangle (the raised ridges / crust)
        self._draw_rounded_rect(0, 0, WAFFLE_W, WAFFLE_H, CORNER_R,
                                fill=WAFFLE_BODY, outline=WAFFLE_RIM, width=2)

        # 3. Outer rim highlight — subtle light line along top edge for 3D
        # Top-left arc (with flipped coords: top is Y=0)
        arc_path = NSBezierPath.bezierPath()
        arc_path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
            NSMakePoint(CORNER_R, CORNER_R), CORNER_R - 1, 90, 180
        )
        hex_to_ns_color(CELL_HILITE).set()
        arc_path.setLineWidth_(1)
        arc_path.stroke()

        # Top edge line
        top_line = NSBezierPath.bezierPath()
        top_line.moveToPoint_(NSMakePoint(CORNER_R, 2))
        top_line.lineToPoint_(NSMakePoint(WAFFLE_W - CORNER_R, 2))
        hex_to_ns_color(CELL_HILITE).set()
        top_line.setLineWidth_(1)
        top_line.stroke()

        # 4. Draw cells (pockets) with 3D depth effect
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                i = row * GRID_COLS + col
                cx = GRID_OX + col * (CELL_SIZE + GRID_GAP)
                # With isFlipped=True, Y=0 is at top, so no coordinate flip needed
                cy = GRID_OY + row * (CELL_SIZE + GRID_GAP)

                # Cell pocket background
                cell_rect = NSMakeRect(cx, cy, CELL_SIZE, CELL_SIZE)
                hex_to_ns_color(CELL_BG).set()
                NSBezierPath.fillRect_(cell_rect)

                # 3D depth: highlight on top & left edges (pocket rim catching light)
                # Top edge (with flipped coords: cy is top)
                top = NSBezierPath.bezierPath()
                top.moveToPoint_(NSMakePoint(cx, cy))
                top.lineToPoint_(NSMakePoint(cx + CELL_SIZE, cy))
                hex_to_ns_color(CELL_HILITE).set()
                top.setLineWidth_(1)
                top.stroke()

                # Left edge
                left = NSBezierPath.bezierPath()
                left.moveToPoint_(NSMakePoint(cx, cy))
                left.lineToPoint_(NSMakePoint(cx, cy + CELL_SIZE))
                hex_to_ns_color(CELL_HILITE).set()
                left.setLineWidth_(1)
                left.stroke()

                # 3D depth: shadow on bottom & right edges (pocket depth)
                # Bottom edge (with flipped coords: cy + CELL_SIZE is bottom)
                bottom = NSBezierPath.bezierPath()
                bottom.moveToPoint_(NSMakePoint(cx, cy + CELL_SIZE))
                bottom.lineToPoint_(NSMakePoint(cx + CELL_SIZE, cy + CELL_SIZE))
                hex_to_ns_color(CELL_SHADOW).set()
                bottom.setLineWidth_(1)
                bottom.stroke()

                # Right edge
                right = NSBezierPath.bezierPath()
                right.moveToPoint_(NSMakePoint(cx + CELL_SIZE, cy))
                right.lineToPoint_(NSMakePoint(cx + CELL_SIZE, cy + CELL_SIZE))
                hex_to_ns_color(CELL_SHADOW).set()
                right.setLineWidth_(1)
                right.stroke()

                # Syrup fill from bottom up (with flipped coords: bottom = cy + CELL_SIZE)
                lvl = max(0.0, min(1.0, self._bars[i]))
                fill_h = int(lvl * CELL_SIZE)
                if fill_h > 0:
                    # Syrup color darkens as it fills more
                    color = SYRUP_COLOR if lvl > 0.6 else SYRUP_LIGHT
                    sy_bottom = cy + CELL_SIZE  # Bottom edge of cell (higher Y with flipped coords)
                    sy_top = sy_bottom - fill_h  # Top of syrup layer
                    syrup_rect = NSMakeRect(cx + 1, sy_top, CELL_SIZE - 2, fill_h)
                    hex_to_ns_color(color).set()
                    NSBezierPath.fillRect_(syrup_rect)

                    # Tiny sheen highlight on syrup surface (1px line at top of syrup)
                    if fill_h > 3:
                        sheen = NSBezierPath.bezierPath()
                        sheen.moveToPoint_(NSMakePoint(cx + 2, sy_top + 1))
                        sheen.lineToPoint_(NSMakePoint(cx + CELL_SIZE - 2, sy_top + 1))
                        hex_to_ns_color(SYRUP_SHEEN).set()
                        sheen.setLineWidth_(1)
                        sheen.stroke()

        # 5. Cancel button (X) — dark filled circle with red X
        self._draw_button(BTN_CANCEL_CX, BTN_CANCEL_CY, BTN_R)
        self._draw_x(BTN_CANCEL_CX, BTN_CANCEL_CY, 4)

        # 6. Stop button (■) — dark filled circle with white square
        self._draw_button(BTN_STOP_CX, BTN_STOP_CY, BTN_R)
        self._draw_stop_square(BTN_STOP_CX, BTN_STOP_CY, 4)

    def _draw_rounded_rect(self, x, y, w, h, r, fill=None, outline=None, width=1):
        """Draw a rounded rectangle."""
        # Create path
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, y, w, h), r, r
        )

        # Fill
        if fill:
            hex_to_ns_color(fill).set()
            path.fill()

        # Stroke
        if outline:
            hex_to_ns_color(outline).set()
            path.setLineWidth_(width)
            path.stroke()

    def _draw_button(self, cx, cy, r):
        """Draw a circular button."""
        circle_rect = NSMakeRect(cx - r, cy - r, r * 2, r * 2)
        circle = NSBezierPath.bezierPathWithOvalInRect_(circle_rect)

        # Fill
        hex_to_ns_color(BTN_FILL).set()
        circle.fill()

        # Outline
        hex_to_ns_color(BTN_RING).set()
        circle.setLineWidth_(2)
        circle.stroke()

    def _draw_x(self, cx, cy, off):
        """Draw X symbol for cancel button."""
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(2)
        path.setLineCapStyle_(NSRoundLineCapStyle)

        # First diagonal
        path.moveToPoint_(NSMakePoint(cx - off, cy - off))
        path.lineToPoint_(NSMakePoint(cx + off, cy + off))

        # Second diagonal
        path.moveToPoint_(NSMakePoint(cx + off, cy - off))
        path.lineToPoint_(NSMakePoint(cx - off, cy + off))

        hex_to_ns_color(CANCEL_X_CLR).set()
        path.stroke()

    def _draw_stop_square(self, cx, cy, sq):
        """Draw square symbol for stop button."""
        square_rect = NSMakeRect(cx - sq, cy - sq, sq * 2, sq * 2)
        hex_to_ns_color(STOP_ICON).set()
        NSBezierPath.fillRect_(square_rect)

    # ── Mouse clicks ──────────────────────────────────────────────────

    def mouseDown_(self, event):
        # Get click location in window coordinates (bottom-left origin)
        loc = event.locationInWindow()
        x = loc.x
        y_window = loc.y

        # Convert to view coordinates (with isFlipped=True, Y=0 is at top)
        # Window: Y=0 at bottom, Y=WIN_H at top
        # View:   Y=0 at top, Y=WIN_H at bottom
        y = WIN_H - y_window  # Flip Y coordinate

        # Cancel button — bottom-left circle (shows confirmation dialog like Windows)
        cancel_dist2 = (x - BTN_CANCEL_CX) ** 2 + (y - BTN_CANCEL_CY) ** 2
        stop_dist2 = (x - BTN_STOP_CX) ** 2 + (y - BTN_STOP_CY) ** 2

        if cancel_dist2 <= BTN_HIT_R2:
            sys.stderr.write(f"[DEBUG] X button clicked at x={x}, y={y}, emitting cancel_request\n")
            sys.stderr.flush()
            emit("cancel_request")  # Show dialog like Windows
        # Stop button — bottom-right circle (process recording)
        elif stop_dist2 <= BTN_HIT_R2:
            emit("stop")

    # ── Animation timer ───────────────────────────────────────────────

    def animTick_(self, timer):
        global _visible

        # Drain command queue
        try:
            while True:
                cmd = _cmd_queue.get_nowait()
                _dispatch_cmd(cmd)
        except queue.Empty:
            pass

        # Smooth bar interpolation
        changed = False
        for i in range(NUM_CELLS):
            diff = self._targets[i] - self._bars[i]
            if abs(diff) > 0.005:
                self._bars[i] += diff * 0.35
                changed = True
            else:
                self._bars[i] = self._targets[i]

        if changed and _visible:
            self.setNeedsDisplay_(True)

    def setTargets_(self, targets):
        self._targets = list(targets)


# ── Toast View (Error/Warning Popups) ─────────────────────────────────

class ToastView(NSView):
    """Toast notification popup matching Windows design."""

    def initWithFrame_style_heading_body_(self, frame, style, heading, body):
        self = objc.super(ToastView, self).initWithFrame_(frame)
        if self:
            self._style = style
            self._heading = heading
            self._body = body
            self._button_zones = []
        return self

    def isOpaque(self):
        return False

    def isFlipped(self):
        return True  # Use top-left origin like Windows

    def acceptsFirstResponder(self):
        return True

    def drawRect_(self, rect):
        """Draw toast with dark panel, golden border, waffle icon, text, and buttons."""
        b = self.bounds()
        w = TOAST_W
        h = TOAST_H

        # Clear background
        NSColor.clearColor().set()
        NSBezierPath.fillRect_(b)

        # Warm dark background with golden accent border
        bg_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(1, 1, w - 2, h - 2), 14, 14
        )

        # Dark fill #2A1F0E
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0x2A/255.0, 0x1F/255.0, 0x0E/255.0, 1.0
        ).set()
        bg_path.fill()

        # Golden border #C8A256
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0xC8/255.0, 0xA2/255.0, 0x56/255.0, 1.0
        ).set()
        bg_path.setLineWidth_(2)
        bg_path.stroke()

        # Icon — sad waffle centred at top
        icon_cx = w // 2
        icon_cy = 36  # With isFlipped=True, Y=0 is at top
        self._draw_sad_waffle(icon_cx, icon_cy, self._style)

        # ── Heading text ──
        heading_y = 80
        heading_font = NSFont.boldSystemFontOfSize_(14)
        heading_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0xF5/255.0, 0xF0/255.0, 0xE8/255.0, 1.0  # Cream color
        )

        # Create paragraph style for centering
        para_style = NSMutableParagraphStyle.alloc().init()
        para_style.setAlignment_(NSCenterTextAlignment)

        heading_attrs = {
            NSFontAttributeName: heading_font,
            NSForegroundColorAttributeName: heading_color,
            NSMutableParagraphStyle: para_style,
        }

        heading_rect = NSMakeRect(20, heading_y, w - 40, 30)
        self._heading.drawInRect_withAttributes_(heading_rect, heading_attrs)

        # ── Body text ──
        body_y = 105
        body_font = NSFont.systemFontOfSize_(11)
        body_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0x88/255.0, 0x88/255.0, 0x88/255.0, 1.0  # Gray
        )

        body_attrs = {
            NSFontAttributeName: body_font,
            NSForegroundColorAttributeName: body_color,
            NSMutableParagraphStyle: para_style,
        }

        body_rect = NSMakeRect(20, body_y, w - 40, 30)
        self._body.drawInRect_withAttributes_(body_rect, body_attrs)

        # ── Buttons ──
        self._button_zones = []  # Clear and rebuild button zones

        btn_h = 32
        btn_y = h - btn_h - 18

        if self._style == 'cancel':
            # Two buttons: Discard (red) and Keep going (golden)
            btn1_w, btn2_w = 110, 120
            btn_gap = 14
            total = btn1_w + btn_gap + btn2_w
            sx = (w - total) // 2

            # Discard button (left, red)
            self._draw_toast_button(sx, btn_y, btn1_w, btn_h,
                                   '#D94040', '#D94040', 'Discard', '#ffffff', 'confirm')

            # Keep going button (right, golden)
            self._draw_toast_button(sx + btn1_w + btn_gap, btn_y, btn2_w, btn_h,
                                   '#D4A843', '#D4A843', 'Keep going', '#ffffff', 'dismiss')
        else:
            # Error style buttons
            btn1_w, btn2_w = 120, 100
            btn_gap = 14
            total = btn1_w + btn_gap + btn2_w
            sx = (w - total) // 2

            self._draw_toast_button(sx, btn_y, btn1_w, btn_h,
                                   '#7c3aed', '#7c3aed', 'Select mic', '#ffffff', 'select_mic')
            self._draw_toast_button(sx + btn1_w + btn_gap, btn_y, btn2_w, btn_h,
                                   '#3a3a4a', '#3a3a4a', 'Dismiss', '#9a9aaa', 'dismiss')

    def _draw_toast_button(self, x, y, w, h, fill, outline, text, text_color, action):
        """Draw a rounded button with text and track its click zone."""
        r = h // 2

        # Draw rounded rectangle button
        btn_rect = NSMakeRect(x, y, w, h)
        btn_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            btn_rect, r, r
        )

        # Fill
        hex_to_ns_color(fill).set()
        btn_path.fill()

        # Outline
        hex_to_ns_color(outline).set()
        btn_path.setLineWidth_(1.5)
        btn_path.stroke()

        # Button text
        btn_font = NSFont.boldSystemFontOfSize_(11)
        btn_text_color = hex_to_ns_color(text_color)

        para_style = NSMutableParagraphStyle.alloc().init()
        para_style.setAlignment_(NSCenterTextAlignment)

        btn_attrs = {
            NSFontAttributeName: btn_font,
            NSForegroundColorAttributeName: btn_text_color,
            NSMutableParagraphStyle: para_style,
        }

        # Center text vertically and horizontally
        text_y = y + (h - 14) // 2  # Approximate vertical center
        text_rect = NSMakeRect(x, text_y, w, h)
        text.drawInRect_withAttributes_(text_rect, btn_attrs)

        # Store button zone for click detection
        self._button_zones.append({
            'x': x,
            'y': y,
            'w': w,
            'h': h,
            'action': action
        })

    def _draw_sad_waffle(self, cx, cy, style):
        """Draw a sad waffle icon with expressive face."""
        s = 44
        x0 = cx - s // 2
        y0 = cy - s // 2

        body_clr = WAFFLE_BODY  # Always use same golden color
        rim_clr  = WAFFLE_RIM if style != 'cancel' else '#904030'
        cell_clr = CELL_BG if style != 'cancel' else '#A06040'
        hilite   = CELL_HILITE
        shadow   = CELL_SHADOW if style != 'cancel' else '#703020'

        # Waffle body
        body_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x0, y0, s, s), 7, 7
        )
        hex_to_ns_color(body_clr).set()
        body_path.fill()
        hex_to_ns_color(rim_clr).set()
        body_path.setLineWidth_(2)
        body_path.stroke()

        # 3x3 grid of cells
        pad = 5
        gap = 3
        cell = (s - 2 * pad - 2 * gap) // 3  # ~9px
        for r in range(3):
            for col in range(3):
                px = x0 + pad + col * (cell + gap)
                py = y0 + pad + r * (cell + gap)

                # Cell background
                cell_rect = NSMakeRect(px, py, cell, cell)
                hex_to_ns_color(cell_clr).set()
                NSBezierPath.fillRect_(cell_rect)

                # Highlight top edge
                top = NSBezierPath.bezierPath()
                top.moveToPoint_(NSMakePoint(px, py))
                top.lineToPoint_(NSMakePoint(px + cell, py))
                hex_to_ns_color(hilite).set()
                top.setLineWidth_(1)
                top.stroke()

                # Highlight left edge
                left = NSBezierPath.bezierPath()
                left.moveToPoint_(NSMakePoint(px, py))
                left.lineToPoint_(NSMakePoint(px, py + cell))
                hex_to_ns_color(hilite).set()
                left.setLineWidth_(1)
                left.stroke()

                # Shadow bottom edge
                bottom = NSBezierPath.bezierPath()
                bottom.moveToPoint_(NSMakePoint(px, py + cell))
                bottom.lineToPoint_(NSMakePoint(px + cell, py + cell))
                hex_to_ns_color(shadow).set()
                bottom.setLineWidth_(1)
                bottom.stroke()

                # Shadow right edge
                right = NSBezierPath.bezierPath()
                right.moveToPoint_(NSMakePoint(px + cell, py))
                right.lineToPoint_(NSMakePoint(px + cell, py + cell))
                hex_to_ns_color(shadow).set()
                right.setLineWidth_(1)
                right.stroke()

    def mouseDown_(self, event):
        """Handle button clicks in toast."""
        # Get click location in window coordinates
        loc = event.locationInWindow()
        x = loc.x
        y_window = loc.y

        # Convert to view coordinates (with isFlipped=True, Y=0 is at top)
        # Window: Y=0 at bottom, Y=TOAST_H at top
        # View:   Y=0 at top, Y=TOAST_H at bottom
        y = TOAST_H - y_window  # Flip Y coordinate

        # Check if click is inside any button zone
        for zone in self._button_zones:
            if (zone['x'] <= x <= zone['x'] + zone['w'] and
                zone['y'] <= y <= zone['y'] + zone['h']):
                # Emit toast_action event and hide toast
                emit("toast_action", action=zone['action'])
                _hide_toast()
                return


# ── Command dispatcher (runs on main thread via timer) ────────────────

def _dispatch_cmd(cmd):
    global _g_window, _g_view, _visible
    ctype = cmd.get("type")

    if ctype == "show":
        _visible = True
        _hide_toast()
        if _g_window:
            _g_window.makeKeyAndOrderFront_(None)
            _g_window.orderFrontRegardless()
            if _g_view:
                _g_view.setNeedsDisplay_(True)

    elif ctype == "hide":
        _visible = False
        _hide_toast()
        if _g_window:
            _g_window.orderOut_(None)

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

        if _g_view:
            _g_view.setTargets_(_targets)

    elif ctype == "show_toast":
        sys.stderr.write(f"[DEBUG] Received show_toast: style={cmd.get('style')}, heading={cmd.get('heading')}\n")
        sys.stderr.flush()
        _show_toast(
            style=cmd.get("style", "error"),
            heading=cmd.get("heading", ""),
            body=cmd.get("body", ""),
        )
        sys.stderr.write(f"[DEBUG] Toast display completed\n")
        sys.stderr.flush()

    elif ctype == "hide_toast":
        _hide_toast()

    elif ctype == "quit":
        _hide_toast()
        NSApplication.sharedApplication().terminate_(None)


# ── Toast Functions ──────────────────────────────────────────────────

def _show_toast(style: str, heading: str, body: str):
    """Show a warm Waffler-branded toast above the waffle."""
    global _toast_win, _toast_style
    _hide_toast()
    _toast_style = style

    # Centre toast above the waffle
    tx = _waffle_x + (WAFFLE_W - TOAST_W) // 2
    ty = _waffle_y + WAFFLE_H + TOAST_PAD

    # Create toast window
    _toast_win = ClickableWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(tx, ty, TOAST_W, TOAST_H),
        NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered,
        False
    )
    _toast_win.setLevel_(NSFloatingWindowLevel + 2)
    _toast_win.setOpaque_(False)
    _toast_win.setBackgroundColor_(NSColor.clearColor())
    _toast_win.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces |
        NSWindowCollectionBehaviorStationary
    )

    # Create toast view
    toast_view = ToastView.alloc().initWithFrame_style_heading_body_(
        NSMakeRect(0, 0, TOAST_W, TOAST_H), style, heading, body
    )
    _toast_win.setContentView_(toast_view)
    _toast_win.makeKeyAndOrderFront_(None)
    _toast_win.orderFrontRegardless()  # FORCE window to front!
    _toast_win.display()  # FORCE immediate render!
    toast_view.setNeedsDisplay_(True)  # TRIGGER view to draw!


def _hide_toast():
    """Destroy the toast popup if visible."""
    global _toast_win, _toast_style
    if _toast_win:
        _toast_win.orderOut_(None)
        _toast_win = None
        _toast_style = None


# ── Stdin reader (background thread) ─────────────────────────────────

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


# ── Main ──────────────────────────────────────────────────────────────

def main():
    global _g_window, _g_view, _waffle_x, _waffle_y

    # Background stdin reader
    threading.Thread(target=_stdin_reader, daemon=True, name="StdinReader").start()

    # NSApplication
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    # Screen geometry
    screen = NSScreen.mainScreen()
    sf = screen.frame()
    vf = screen.visibleFrame()
    sw = sf.size.width
    sh = sf.size.height

    # Calculate dock height (macOS dock is at bottom)
    dock_height = vf.origin.y  # Space from bottom to visible frame
    gap = 16  # px above dock

    # Position: bottom-centre, 16 px above dock
    _waffle_x = (sw - WIN_W) / 2.0
    _waffle_y = dock_height + gap

    # Create NSWindow (borderless)
    _g_window = ClickableWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(_waffle_x, _waffle_y, WIN_W, WIN_H),
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
    _g_view = WaffleView.alloc().initWithFrame_(NSMakeRect(0, 0, WIN_W, WIN_H))
    _g_window.setContentView_(_g_view)

    # Animation + command-drain timer (50 ms = 20 fps)
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.05, _g_view, b"animTick:", None, True
    )
    NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSDefaultRunLoopMode)

    # Start hidden; the parent will send {"type": "show"} when ready
    _g_window.orderOut_(None)

    emit("ready")
    app.run()


if __name__ == "__main__":
    main()
