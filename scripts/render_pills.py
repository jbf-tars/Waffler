#!/usr/bin/env python3
"""Draw the small pill off screen, to PNG files, without opening a window.

    venv/bin/python scripts/render_pills.py <folder>

One picture for each kind (Done, Working, Your call, Problem with and without
a button, Tip), the shortest and the longest line, close-ups of the badge,
and a pill beside the meeting bar. It draws
the real views of src/overlay_process.py into a bitmap, at 2x, on the grey the
design board uses, so the result can be laid next to the design. Mac only.
Nothing is shown on screen and nothing of the running app is touched.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import AppKit  # noqa: E402
from AppKit import NSBitmapImageRep, NSColor, NSGraphicsContext, NSMakeRect, NSBezierPath  # noqa: E402

import overlay_process as op  # noqa: E402
import pill as pl  # noqa: E402
import user_messages as um  # noqa: E402

SCALE = 2
MARGIN = 20
BOARD = "#2A2622"


def _bitmap(w, h, scale=SCALE):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, int(w * scale), int(h * scale), 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 0)
    rep.setSize_((w, h))
    return rep


def _draw_view(view, rep, x, y, board_h):
    """Draw ``view`` (flipped, its own size) with its top-left at (x, y) from
    the top of the board. The context is told it is flipped, as a flipped
    view's own is, so text comes out the right way up."""
    size = view.frame().size
    base = NSGraphicsContext.currentContext()
    flipped = NSGraphicsContext.graphicsContextWithCGContext_flipped_(base.CGContext(), True)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(flipped)
    flipped.saveGraphicsState()
    t = AppKit.NSAffineTransform.transform()
    t.translateXBy_yBy_(x, board_h - y)
    t.scaleXBy_yBy_(1.0, -1.0)
    t.concat()
    view.drawRect_(NSMakeRect(0, 0, size.width, size.height))
    flipped.restoreGraphicsState()
    NSGraphicsContext.restoreGraphicsState()


def _pill_view(p):
    lay = op._pill_layout(p)
    view = op.MessagePillView.alloc().initWithFrame_(NSMakeRect(0, 0, lay.width, lay.height))
    view.set_pill(p, lay)
    return view


def render(path, views, gap=pl.GAP, scale=SCALE, crop_w=None, guide=False):
    """``views`` side by side on one line, as they sit on screen. ``crop_w``
    keeps only that many points from the left (a close-up of the left end);
    ``guide`` draws a faint line through the middle of the pill's height, to
    judge whether the words sit on it."""
    w = sum(v.frame().size.width for v in views) + gap * (len(views) - 1) + 2 * MARGIN
    if crop_w:
        w = MARGIN + crop_w
    h = max(v.frame().size.height for v in views) + 2 * MARGIN
    rep = _bitmap(w, h, scale)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    op.hex_to_ns_color(BOARD).set()
    NSBezierPath.fillRect_(NSMakeRect(0, 0, w, h))
    x = MARGIN
    for v in views:
        _draw_view(v, rep, x, MARGIN, h)
        x += v.frame().size.width + gap
    if guide:
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.8, 1.0, 0.55).set()
        NSBezierPath.fillRect_(NSMakeRect(0, h / 2.0 - 0.125, w, 0.25))
    NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    data.writeToFile_atomically_(str(path), True)
    return int(w), int(h)


JOURNAL = [{"label": "Show in Journal", "action": "show_transform:1", "kind": "primary"}]
OFFER = [{"label": "Keep waiting", "action": "keep_waiting", "kind": "primary"},
         {"label": "Send later", "action": "send_later", "kind": "secondary"},
         {"label": "Cancel", "action": "cancel_processing", "kind": "danger"}]

SHOTS = [
    ("done", pl.pill_for("info", *um.transform_done_message("Polish", "darwin"), JOURNAL, 8)),
    ("working", pl.pill_for("warn", *um.take_limit_stopped(20))),
    ("your-call", pl.pill_for("info", "Still working on it", "Groq is slow to answer.", OFFER)),
    ("problem", pl.pill_for("warn", "Connection failed", "Check your internet or VPN.")),
    ("problem-button", pl.pill_for("warn", "Access denied", "API key may be invalid.")),
    ("tip", pl.pill_for("info", *um.hold_hint_message("fn"),
                        [{"label": "Got it", "action": "tip_dismiss:hold_hint"}], 10)),
    ("tip-button", pl.pill_for("info", *um.MIC_LID_CLOSED,
                               [{"label": "Choose microphone", "action": "choose_microphone"},
                                {"label": "Got it", "action": "tip_dismiss:lid_closed"}], 10)),
    ("longest", pl.pill_for("warn", "Something went wrong", "Your words are in the Journal.")),
]


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    AppKit.NSApplication.sharedApplication()       # fonts and colours need an app object
    for name, p in SHOTS:
        size = render(out / f"{name}.png", [_pill_view(p)])
        print(f"{name}: {p.kind} '{p.text}' {size[0] - 2 * MARGIN}x{size[1] - 2 * MARGIN}")
    # The shortest pill, for balance against the longest.
    shortest = pl.pill_for("info", *um.LAST_COPIED, None, 3)
    size = render(out / "shortest.png", [_pill_view(shortest)])
    print(f"shortest: '{shortest.text}' {size[0] - 2 * MARGIN}x{size[1] - 2 * MARGIN}")
    # Close-ups of the left end, twice the size again, to judge the badge;
    # and the same with a line through the middle of the pill, to judge
    # whether the words sit on the mark's centre line.
    for name in ("done", "problem"):
        p = dict(SHOTS)[name]
        render(out / f"{name}-left-closeup.png", [_pill_view(p)], scale=SCALE * 2, crop_w=110)
        render(out / f"{name}-left-closeup-guide.png", [_pill_view(p)], scale=SCALE * 4, crop_w=110,
               guide=True)
    # A pill beside the meeting bar, with the gap they keep.
    op._meet_elapsed, op._meet_done = 754, False
    bar = op.MeetingBarView.alloc().initWithFrame_(NSMakeRect(0, 0, op.MEET_W, op.MEET_H))
    size = render(out / "beside-meeting-bar.png", [bar, _pill_view(SHOTS[0][1])])
    print(f"beside-meeting-bar: {size[0] - 2 * MARGIN}x{size[1] - 2 * MARGIN}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "pills"))
