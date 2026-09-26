"""What the tray (Windows) or menu bar (Mac) icon says about a dictation.

The icon never changed, so with the window hidden nothing showed that a
dictation was recording or still being processed. Now the tooltip names the
state, the Windows icon gains an amber dot while Waffler is working, and the
Mac menu bar icon dims (app.py applies these).
"""

from pathlib import Path

IDLE = "idle"
RECORDING = "recording"
WORKING = "working"
NOT_SENT = "not_sent"

TIPS = {
    IDLE: "Waffler",
    RECORDING: "Waffler: recording",
    WORKING: "Waffler: working on your dictation",
    NOT_SENT: "Waffler: a recording wasn't sent. Open the Journal to send it.",
}

# The dot added to the Windows icon while working: amber, like the window's
# "Cleaning up" pill, with a light ring so it reads on dark and light taskbars.
DOT_FILL = (217, 119, 6, 255)      # #d97706
DOT_RING = (255, 248, 230, 255)
ICO_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64)]


def tip_for(state: str) -> str:
    return TIPS.get(state, TIPS[IDLE])


def shows_working_icon(state: str) -> bool:
    return state == WORKING


def make_working_icon(src_ico: Path, dst_ico: Path) -> Path:
    """Write a copy of the app icon with an amber dot in the corner.

    Built once from the shipped icon.ico, so it always matches the real icon.
    Returns ``dst_ico``. Raises if Pillow cannot read or write the icon; the
    caller then keeps the plain icon and the tooltip still changes.
    """
    from PIL import Image, ImageDraw

    src = Image.open(str(src_ico))
    frames = []
    for size in ICO_SIZES:
        img = src.copy().convert("RGBA").resize(size, Image.LANCZOS)
        w, h = size
        d = max(6, round(w * 0.46))          # dot diameter
        ring = max(1, round(w / 16))
        x1, y1 = w - 1, h - 1
        x0, y0 = x1 - d, y1 - d
        draw = ImageDraw.Draw(img)
        draw.ellipse([x0, y0, x1, y1], fill=DOT_RING)
        draw.ellipse([x0 + ring, y0 + ring, x1 - ring, y1 - ring], fill=DOT_FILL)
        frames.append(img)
    dst_ico = Path(dst_ico)
    dst_ico.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(str(dst_ico), format="ICO", sizes=ICO_SIZES,
                    append_images=frames[:-1])
    return dst_ico
