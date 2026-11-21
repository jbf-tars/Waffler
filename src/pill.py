"""The one small pill every pop-up is now.

Waffler's pop-up was a 380 x 210 card: a waffle, a heading, three lines of
body and up to three buttons. It is now one dark capsule, 40 points high and
as wide as its words: the waffle mark (with a small badge on its corner when
the message is Done or a Problem), ONE line of six words at most, at most ONE
button, and a small cross when it does not go away by itself. It belongs to the same family as the meeting bar.

The five kinds
--------------
``done``     a small cream badge with a tick on the mark's corner. No button.
             Two seconds. A click opens the entry it is about, when there is
             one (what "Show in Journal" did).
``working``  three dots after the words. It stays until the work ends, then
             becomes its Done or Problem pill in place (``after_work``).
``ask``      "Your call": one button and a cross. Doing nothing is always
             safe: the pill never answers for the person.
``problem``  a small red badge on the mark's corner. Six seconds; with a
             button it stays until its button or its cross is used.
``tip``      quieter ink. Its cross counts as a dismissal, as "Got it" did.

Where the words come from
-------------------------
Nothing that raises a message changed: the app still says what happened in a
heading, a sentence and its buttons (src/user_messages.py, src/unsent.py,
src/pasteboard.py and the lines in app.py). ``pill_for`` turns that into the
pill, from the table below, which is the approved board row by row. The long
sentence is kept as the pill's ``description``: VoiceOver reads it and the log
keeps it; it is never drawn.

A message that is not in the table still becomes a pill (``_unlisted``): a
Problem for a warning or an error, Done for a notice, its heading cut to six
words.

This module draws nothing and imports nothing of the platform's, so the Mac
overlay, the Windows overlay and the tests all read the same sizes, colours
and rules from it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, NamedTuple, Optional, Tuple

DONE, WORKING, ASK, PROBLEM, TIP = "done", "working", "ask", "problem", "tip"
KINDS = (DONE, WORKING, ASK, PROBLEM, TIP)

MAX_WORDS = 6

# How long each kind stays, in seconds. None: until answered, withdrawn or
# replaced.
DONE_S = 2.0
PROBLEM_S = 6.0
TIP_S = 10.0
# A Working pill whose work never reports its end (the app could not restart
# itself, say) is taken down after this long, so it cannot sit there for good.
WORKING_CAP_S = 30.0

# Actions a click can send that the old card did not have (app.py
# _on_toast_action). The others are the old buttons' own.
DISMISS_ACTION = "dismiss"
RESTART_ACTION = "restart_app"
KEYS_ACTION = "open_keys"
MIC_ACTION = "select_mic"
JOURNAL_ACTION = "open_journal"

# ── The look (the design's numbers; both overlays draw from these) ───────────
HEIGHT = 40
FILL = "#14110F"
HAIRLINE_ALPHA = 0.14           # a 1 pt line of white at 14%
INK = "#F4F0EA"
INK_QUIET = "#CEC6BB"           # a tip's words
GOLD = "#E8C267"                # the first of the three dots
DOTS = ("#E8C267", "#8A7437", "#4D4226")
RED = "#F0624F"
CROSS = "#B3AA9E"
BUTTON_FILL = "#F4F0EA"
BUTTON_INK = "#12100E"
TEXT_PT = 14                    # medium
BUTTON_PT = 12                  # semibold
MARK = 22                       # the waffle mark
PAD_LEFT = 14                   # from the pill's left edge to the mark
MARK_GAP = 14                   # from the mark (its badge included) to the words
PAD_RIGHT = 20                  # from the end of the words (or the dots) to the edge
PAD_RIGHT_PRESS = 6             # from a button or the cross to the edge
ITEM_GAP = 14                   # between the words, the button and the cross
# One thing on the left, not two: the tick and the red dot are small round
# badges on the mark's bottom-right corner, overlapping it by about a third,
# each with a ring in the pill's own colour so it looks cut out of the mark.
BADGE_TICK = 14                 # a cream circle with a dark tick: Done
BADGE_TICK_OUT = 6              # how far it hangs past the mark, right and down
BADGE_DOT = 12                  # a red circle: Problem
BADGE_DOT_OUT = 5
BADGE_RING = 2
BADGE_FILL = "#F4F0EA"
BADGE_INK = "#14110F"
BADGE_TICK_PT = 1.6             # the tick's stroke
# The tick, in an 8 x 8 box in the middle of its badge, y downwards.
TICK_BOX = 8
TICK_POINTS = ((1.5, 4.2), (3.3, 6.0), (6.5, 2.2))
# A Working pill's three dots trail its words.
DOT, DOT_GAP = 4, 3
DOTS_LEAD = -4                  # they sit a little closer to the words
BUTTON_H = 28
BUTTON_PAD = 12
BUTTON_LEAD = 2                 # a little more air before the button
CLOSE = 28
CLOSE_LEAD = -4                 # the cross sits close to what is before it
CROSS_ARM = 3                   # the cross is 6 x 6 in the middle of its 28
# The words are placed so that the middle of a capital letter is the middle of
# the pill, which is also the mark's centre line; this is added after that
# (down is positive), in half points, if the renders show them high or low.
TEXT_NUDGE = 0.0

GAP = 8                         # between the pill and the dictation pill or the meeting bar
RISE = 6                        # how far it rises as it appears
FADE_S = 0.18

# ── The waffle record mark ───────────────────────────────────────────────────
# Waffler's own record button, in place of a red dot: a round-cornered waffle
# of sixteen pockets. Gold: press to record. The same waffle in red, breathing
# slowly: recording now, press to stop. The page draws the same numbers
# (ui/components.css .wrec), and the meeting bar draws these (22 points, as the
# pill's mark). In a box of REC_UNITS: a pocket is REC_CELL wide, REC_GAP from
# the next, REC_PAD from the edge.
REC_UNITS, REC_GRID, REC_PAD, REC_CELL, REC_GAP = 24, 4, 3, 3, 2
REC_CORNER = 0.27               # of its size
REC_GOLD, REC_GOLD_GRID = GOLD, "#B8892F"
REC_RED, REC_RED_GRID = RED, "#A8322A"
REC_PULSE_S = 2.4               # one slow breath
REC_PULSE_LOW = 0.72            # how faint it gets at the bottom of one


class RecordMark(NamedTuple):
    size: float
    radius: float
    body: str
    cell: str
    pockets: Tuple[Tuple[float, float, float], ...]      # (x, y, side), from the mark's top-left


def record_mark(size: float, recording: bool = False) -> RecordMark:
    """The waffle record mark ``size`` points across: gold at rest, red while
    recording. The same drawing in both: only the two colours change."""
    u = float(size) / REC_UNITS
    steps = [REC_PAD + i * (REC_CELL + REC_GAP) for i in range(REC_GRID)]
    pockets = tuple((x * u, y * u, REC_CELL * u) for y in steps for x in steps)
    body, cell = (REC_RED, REC_RED_GRID) if recording else (REC_GOLD, REC_GOLD_GRID)
    return RecordMark(float(size), float(size) * REC_CORNER, body, cell, pockets)


def record_pulse(seconds: float, still: bool = False) -> float:
    """How solid the red mark is ``seconds`` into a recording: 1 down to
    REC_PULSE_LOW and back, once every REC_PULSE_S. ``still`` (the person
    asked for less motion): always 1."""
    if still:
        return 1.0
    swing = 0.5 + 0.5 * math.cos(2.0 * math.pi * (float(seconds) % REC_PULSE_S) / REC_PULSE_S)
    return REC_PULSE_LOW + (1.0 - REC_PULSE_LOW) * swing


@dataclass(frozen=True)
class Pill:
    kind: str
    text: str
    button: Optional[dict] = None       # {"label", "action"}
    close: Optional[str] = None         # what the cross sends; None: no cross
    click: Optional[str] = None         # what a click on the pill itself sends
    seconds: Optional[float] = None     # None: it stays
    description: str = ""               # the long sentence: VoiceOver and the log

    def as_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text,
                "button": dict(self.button) if self.button else None,
                "close": self.close, "click": self.click, "seconds": self.seconds,
                "description": self.description}


def from_dict(raw) -> Optional[Pill]:
    """The pill a command carries, or None when it is not one."""
    try:
        if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
            return None
        text = str(raw.get("text") or "").strip()
        if not text:
            return None
        button = raw.get("button")
        if isinstance(button, dict) and button.get("label") and button.get("action"):
            button = {"label": str(button["label"]), "action": str(button["action"])}
        else:
            button = None
        seconds = raw.get("seconds")
        seconds = float(seconds) if isinstance(seconds, (int, float)) and seconds > 0 else None
        return Pill(raw["kind"], text, button,
                    str(raw["close"]) if raw.get("close") else None,
                    str(raw["click"]) if raw.get("click") else None,
                    seconds, str(raw.get("description") or ""))
    except Exception:
        return None


# ── From the old message to the pill ─────────────────────────────────────────

def _real_buttons(buttons) -> list:
    out = []
    for b in buttons or ():
        if isinstance(b, dict) and b.get("label") and b.get("action"):
            out.append(b)
    return out


def _action(buttons, *prefixes) -> Optional[str]:
    """The action of the old card's first button that starts with one of
    ``prefixes`` (any that does something, with none given)."""
    for b in _real_buttons(buttons):
        action = str(b["action"])
        if prefixes:
            if action.startswith(prefixes):
                return action
        elif action != DISMISS_ACTION and not action.startswith(("tip_dismiss:", "tip_mute:")):
            return action
    return None


def _keys(body: str) -> str:
    return "Ctrl+V" if "Ctrl+V" in body else "Cmd+V"


def _six(text: str) -> str:
    words = str(text or "").split()
    return " ".join(words[:MAX_WORDS])


class _Row(NamedTuple):
    old: object                     # the old heading, or a compiled pattern for it
    kind: Optional[str]             # None: this one is not a pop-up any more
    text: object = ""               # the line, or a function (match, body) -> line
    label: Optional[str] = None     # the one button
    action: object = None           # its action: an id, or a function (buttons) -> id
    when: Optional[Callable[[str], bool]] = None    # only when the old body says so
    close: object = None            # for "Your call": what the cross sends


def _from_caller(*prefixes, default=None):
    return lambda buttons: _action(buttons, *prefixes) or default


def _mic_name(match, _body) -> str:
    name = " ".join(match.group(1).split())
    if len(name.split()) > MAX_WORDS - 1 or len(name) > 28:
        return "New microphone connected"
    return f"{name} connected"


def _hold(_match, body) -> str:
    found = re.search(r"Hold (.+?) while you speak", body)
    key = " ".join(found.group(1).split()) if found else ""
    if not key or len(key.split()) > MAX_WORDS - 3:
        return "Hold the key to talk"
    return f"Hold {key} to talk"


_ALLOW = _from_caller("open_", default="open_ax_settings")

# The board, row by row. The first row that fits is used, so a row with a
# ``when`` comes before the plain row for the same heading.
TABLE = (
    # ── Gone ──
    _Row(re.compile(r"^Used .+ instead$"), None),

    # ── Done ──
    _Row("Polish done", DONE, "Polished"),
    _Row(re.compile(r"^.+ done$"), DONE, "Done"),
    _Row("Text edited", DONE, "Edited"),
    _Row("Draft added", DONE, "Draft added"),
    _Row(re.compile(r"^Searching .+$"), DONE, "Searching"),
    _Row("Result copied", DONE, "Copied"),
    _Row("Draft copied", DONE, "Draft copied"),
    _Row("Copied", DONE, "Copied"),
    _Row("Result not pasted", PROBLEM, "Saved, not pasted", "Open",
         _from_caller("show_", "open_journal", default=JOURNAL_ACTION),
         when=lambda body: "couldn't be copied" in body),
    _Row("Result not pasted", DONE, lambda m, body: f"Copied. Press {_keys(body)}"),
    _Row("Copied, not pasted", DONE, "Copied. Paste it yourself"),
    _Row("Transform cancelled", DONE, "Cancelled"),
    _Row("Command cancelled", DONE, "Cancelled"),
    _Row("Cancelled", PROBLEM, "Cancelled. Not saved", when=lambda body: "couldn't save" in body),
    _Row("Cancelled", DONE, "Cancelled"),
    _Row("Your text already looks good", DONE, "Nothing to change"),
    _Row("Nothing to change", DONE, "Nothing to change"),
    _Row("Saved for later", DONE, "Saved for later"),
    _Row("Recovered", DONE, "Recovered"),
    _Row("Recording the meeting", DONE, "Recording the meeting"),
    _Row("Meeting finished", DONE, "Meeting finished"),

    # ── Working ──
    _Row("Waffler is busy", WORKING, "One moment"),
    _Row("Still working", WORKING, "One moment"),
    _Row("A transform is running", WORKING, "One moment"),
    _Row("A command is running", WORKING, "One moment"),
    _Row("Not ready yet", WORKING, "One moment"),
    _Row("Waffler is restarting", WORKING, "Restarting"),
    _Row("Recording stopped", WORKING, "Tidying"),

    # ── Your call ──
    _Row("Still working on it", ASK, "Still working", "Send later",
         _from_caller("send_later", default="send_later"),
         close=_from_caller("cancel_processing", default="cancel_processing")),
    _Row("Still cleaning up", ASK, "Still tidying", "Paste as is",
         _from_caller("paste_raw", default="paste_raw"),
         close=_from_caller("cancel_processing", default="cancel_processing")),
    _Row(re.compile(r"^(.+) detected$"), ASK, _mic_name, "Use it", _from_caller("mic_use:"),
         close=_from_caller("mic_later:", default=DISMISS_ACTION)),
    _Row("Long recording", ASK, "One minute left"),

    # ── Problem ──
    _Row("We couldn't hear you", PROBLEM, "Didn’t hear you", "Mic", MIC_ACTION),
    _Row("Waffler can't hear your microphone", PROBLEM, "Can’t hear your mic", "Choose",
         _from_caller("choose_microphone", default=MIC_ACTION)),
    _Row("Mic reset", PROBLEM, "Mic cut out. Say it again"),
    _Row("Mic dropped out", PROBLEM, "Mic cut out. Say it again"),
    _Row("Your mic was slow to stop", PROBLEM, "Mic cut out. Say it again"),
    _Row("Your microphone was disconnected", PROBLEM, "Mic disconnected"),
    _Row("Your microphone is stuck", PROBLEM, "Mic is stuck", "Restart", RESTART_ACTION),
    _Row("Microphone is off", PROBLEM, "Microphone is off", "Allow",
         _from_caller("open_", default="open_mic_settings")),
    _Row("Microphone is restricted", PROBLEM, "Microphone is blocked", "Open",
         _from_caller("open_", default="open_mic_settings")),
    _Row("Accessibility is off", PROBLEM, "Can’t read text yet", "Allow", _ALLOW,
         when=lambda body: "read selected text" in body),
    _Row("Accessibility is off", PROBLEM, "Can’t paste yet", "Allow", _ALLOW),
    _Row("Waffler can't hear your shortcut", PROBLEM, "Can’t hear your shortcut", "Allow",
         _from_caller("open_", default="open_input_settings")),
    _Row("Couldn't paste", PROBLEM, lambda m, body: f"Copied. Press {_keys(body)}", "Allow", _ALLOW,
         when=lambda body: "needs Accessibility" in body),
    _Row("Couldn't paste", PROBLEM, lambda m, body: f"Copied. Press {_keys(body)}"),
    _Row("Not pasted", PROBLEM, lambda m, body: f"Copied. Press {_keys(body)}"),
    _Row("Couldn't copy to the clipboard", PROBLEM, "Saved, not pasted", "Open", JOURNAL_ACTION),
    _Row("Couldn't copy", PROBLEM, "Couldn’t copy"),
    _Row("Not sent", PROBLEM, "Not sent. Say it again", when=lambda body: "couldn't be saved" in body),
    _Row("Not sent", PROBLEM, "Not sent. Saved", "Open", JOURNAL_ACTION),
    _Row("That took too long", PROBLEM, "Took too long. Saved", when=lambda body: "is saved" in body),
    _Row("That took too long", PROBLEM, "Took too long"),
    _Row("Pasted without the clean-up", PROBLEM, "Pasted as said", "Add key", KEYS_ACTION,
         when=lambda body: "No key is set up" in body),
    _Row("Pasted without the clean-up", PROBLEM, "Pasted as said"),
    _Row(re.compile(r"^Clean-up paused .+$"), PROBLEM, "Pasted as said"),
    _Row("Limit reached", PROBLEM, "Limit reached"),
    _Row("Connection failed", PROBLEM, "No connection"),
    _Row("Access denied", PROBLEM, "Key refused", "Settings", KEYS_ACTION),
    _Row("Couldn't rewrite", PROBLEM, "Couldn’t do that"),
    _Row("Couldn't run that", PROBLEM, "Couldn’t do that"),
    _Row("Took too long", PROBLEM, "Couldn’t do that"),
    _Row("Couldn't hear the command", PROBLEM, "Didn’t catch that"),
    _Row("Nothing was replaced", PROBLEM, "Left as it was"),
    _Row("Select some text first", PROBLEM, "Select some text first"),
    _Row("Too much text", PROBLEM, "Too much at once"),
    _Row("That's a long instruction", PROBLEM, "Too much at once"),
    _Row("Can't read this field", PROBLEM, "Can’t read that here"),
    _Row("Clipboard is busy", PROBLEM, "Can’t read that here"),
    _Row("Can't copy from here", PROBLEM, "Can’t read that here"),
    _Row("Can't copy right now", PROBLEM, "Can’t read that here"),
    _Row("Couldn't read the selection", PROBLEM, "Can’t read that here"),
    _Row("Nothing to paste yet", PROBLEM, "Nothing to paste yet"),
    _Row("Nothing to copy yet", PROBLEM, "Nothing to copy yet"),
    _Row("The meeting couldn't be recorded", PROBLEM, "Couldn’t record", "Mic", MIC_ACTION),
    _Row("Couldn't record", PROBLEM, "Couldn’t record", "Mic", MIC_ACTION),
    _Row("Something went wrong", PROBLEM, "Something went wrong",
         when=lambda body: "didn't go through" in body),
    _Row("Something went wrong", PROBLEM, "Something went wrong. Saved"),
    _Row("Not saved to History", PROBLEM, "Pasted, not saved"),
    _Row("Couldn't open your browser", PROBLEM, "Couldn’t open your browser"),

    # ── Tip ──
    _Row("Don't tap, hold", TIP, _hold),
    _Row("Your Mac's lid is closed", TIP, "Lid closed. Mic is muffled", "Choose",
         _from_caller("choose_microphone", default=MIC_ACTION)),
    _Row("Your microphone is set low", TIP, "Mic is set low", "Fix",
         _from_caller("open_sound", default="open_sound_input")),
)


def _find(heading: str, body: str):
    for row in TABLE:
        if isinstance(row.old, str):
            match = heading == row.old
        else:
            match = row.old.match(heading)
        if match and (row.when is None or row.when(body)):
            return row, match
    return None, None


def _seconds(kind: str, has_button: bool, timeout) -> Optional[float]:
    try:
        given = float(timeout) if timeout and float(timeout) > 0 else None
    except (TypeError, ValueError):
        given = None
    if kind == DONE:
        return DONE_S
    if kind == WORKING:
        return None
    if kind == ASK:
        return given
    if kind == PROBLEM:
        return None if has_button else PROBLEM_S
    return given or TIP_S


def _describe(heading: str, body: str) -> str:
    heading, body = heading.strip(), body.strip()
    if heading and body:
        return f"{heading}. {body}" if heading[-1:] not in ".?!" else f"{heading} {body}"
    return heading or body


def _build(kind, text, label, action, close, buttons, timeout, heading, body) -> Pill:
    button = {"label": label, "action": action} if label and action else None
    click = None
    if kind == DONE:
        # A click opens the entry it is about, when there is one.
        click = _action(buttons, "show_", "open_journal")
        button, close = None, None
    elif kind == WORKING:
        button, close = None, None
    elif kind == ASK:
        close = (close or DISMISS_ACTION) if button else None
    elif kind == PROBLEM:
        close = DISMISS_ACTION if button else None
    elif kind == TIP:
        # The cross is "Got it": two of them and the tip stays away.
        close = _action(buttons, "tip_dismiss:") or DISMISS_ACTION
    return Pill(kind, text, button, close, click, _seconds(kind, bool(button), timeout),
                _describe(heading, body))


def _unlisted(style, heading, body, buttons, timeout) -> Optional[Pill]:
    text = _six(heading) or _six(body)
    if not text:
        return None
    if style == "cancel":
        return _build(ASK, text, "Discard", "confirm", DISMISS_ACTION, buttons, None, heading, body)
    kind = PROBLEM if style in ("error", "warn") else DONE
    label = action = None
    if kind == PROBLEM:
        for b in _real_buttons(buttons):
            if str(b["action"]) != DISMISS_ACTION:
                label, action = " ".join(str(b["label"]).split()[:3]), str(b["action"])
                break
    return _build(kind, text, label, action, None, buttons, timeout, heading, body)


def pill_for(style, heading, body, buttons=None, timeout=None) -> Optional[Pill]:
    """The pill for an old pop-up, or None when it is not a pop-up any more
    ("Used another service instead"). Never raises."""
    try:
        heading = heading if isinstance(heading, str) else ""
        body = body if isinstance(body, str) else ""
        buttons = buttons if isinstance(buttons, (list, tuple)) else None
        row, match = _find(heading.strip(), body)
        if row is None:
            return _unlisted(style, heading, body, buttons, timeout)
        if row.kind is None:
            return None
        text = row.text(match, body) if callable(row.text) else row.text
        action = row.action(buttons) if callable(row.action) else row.action
        close = row.close(buttons) if callable(row.close) else row.close
        return _build(row.kind, _six(text), row.label, action, close, buttons, timeout, heading, body)
    except Exception:
        return None


def after_work(pill: Optional[Pill], result: str) -> Optional[Pill]:
    """The work a Working pill stood for has ended with no message of its
    own: it becomes "Done" in place, or goes (``result`` "quiet"). Any other
    pill is left as it is."""
    if pill is None or pill.kind != WORKING:
        return pill
    if result == "done":
        return Pill(DONE, "Done", seconds=DONE_S, description="Done.")
    return None


# ── Its size, and where a click landed ───────────────────────────────────────

class Layout(NamedTuple):
    width: float
    height: float
    mark_x: float
    mark_y: float
    badge: Optional[Tuple[float, float, float]]     # x, y (from the top), diameter
    text_x: float
    text_w: float
    dots_x: Optional[float]         # a Working pill's three dots, after the words
    button: Optional[Tuple[float, float, float, float]]     # x, y, w, h (y from the top)
    close: Optional[Tuple[float, float, float, float]]


_BADGES = {DONE: (BADGE_TICK, BADGE_TICK_OUT), PROBLEM: (BADGE_DOT, BADGE_DOT_OUT)}
DOTS_W = DOT * 3 + DOT_GAP * 2


def layout(pill: Pill, measure: Callable[[str, float, str], float]) -> Layout:
    """Where everything sits. ``measure(text, points, weight)`` is the
    platform's own text width; weight is "medium" or "semibold"."""
    x = float(PAD_LEFT)
    mark_x, mark_y = x, (HEIGHT - MARK) / 2.0
    x += MARK
    badge = None
    if pill.kind in _BADGES:
        size, out = _BADGES[pill.kind]
        badge = (mark_x + MARK + out - size, mark_y + MARK + out - size, float(size))
        x += out                    # the air is measured from the badge's edge
    x += MARK_GAP
    text_x = x
    text_w = float(measure(pill.text, TEXT_PT, "medium"))
    x += text_w
    dots_x = None
    if pill.kind == WORKING:
        x += ITEM_GAP + DOTS_LEAD
        dots_x = x
        x += DOTS_W
    button = close = None
    top = (HEIGHT - BUTTON_H) / 2.0
    if pill.button:
        x += ITEM_GAP + BUTTON_LEAD
        w = float(measure(pill.button["label"], BUTTON_PT, "semibold")) + 2 * BUTTON_PAD
        button = (x, top, w, float(BUTTON_H))
        x += w
    if pill.close:
        x += ITEM_GAP + CLOSE_LEAD
        close = (x, (HEIGHT - CLOSE) / 2.0, float(CLOSE), float(CLOSE))
        x += CLOSE
    x += PAD_RIGHT_PRESS if (button or close) else PAD_RIGHT
    return Layout(x, float(HEIGHT), mark_x, mark_y, badge, text_x, text_w, dots_x, button, close)


def _inside(box, x, y) -> bool:
    return box is not None and box[0] <= x <= box[0] + box[2] and box[1] <= y <= box[1] + box[3]


def hit(lay: Layout, x: float, y: float) -> Optional[str]:
    """"button", "close" or "body" for a point (y from the top), else None."""
    if not (0 <= x <= lay.width and 0 <= y <= lay.height):
        return None
    if _inside(lay.button, x, y):
        return "button"
    if _inside(lay.close, x, y):
        return "close"
    return "body"


def action_for(pill: Pill, where: Optional[str]) -> Optional[str]:
    """What a click sends, or None when it does nothing and the pill stays.
    A "Your call" pill and a tip are only ever answered by their button or
    their cross; a click on a Done or a Problem with nothing to press closes
    it."""
    if where == "button":
        return pill.button["action"] if pill.button else None
    if where == "close":
        return pill.close
    if where != "body":
        return None
    if pill.click:
        return pill.click
    if pill.kind == DONE or (pill.kind == PROBLEM and not pill.button):
        return DISMISS_ACTION
    return None


# ── Where it goes ────────────────────────────────────────────────────────────
# As low and out of the way as it can be. Rectangles are (x, y, w, h).
#
# The dictation pill's window sits at the bottom centre of the screen (or
# where the person dragged it), a small distance above the Dock or taskbar.
#   * While the dictation pill is NOT on screen (the usual case: a message
#     comes after the dictation has finished), the message rests in that same
#     place: centred on it, its bottom edge level with the bottom of the
#     dictation pill's window.
#   * While the dictation pill IS on screen and the resting place would cover
#     it, the message sits directly above it with a small gap. The top of the
#     dictation pill's window is the top of the waffle itself, so that is the
#     top of what the person sees. When the dictation pill then goes, a
#     Done stays where it is for its two seconds (nothing jumps); any other
#     message comes down into the resting place (``settles``), so a Problem
#     is not left floating over nothing.
# A message in the resting place gives it back when the dictation pill needs
# it (a new dictation, or the working pill): it ends, it does not move.
#
# ``y_down`` is for a screen whose y grows downwards (Windows), where a
# window's (x, y) is its top-left; without it y grows upwards (the Mac) and
# (x, y) is the bottom-left.

def origin_above(anchor, anchor_size, width: float, y_down: bool = False) -> Tuple[float, float]:
    """Centred over the dictation pill, a small gap above it."""
    ax, ay = anchor
    aw, ah = anchor_size
    if y_down:
        return ax + (aw - width) / 2.0, ay - GAP - HEIGHT
    return ax + (aw - width) / 2.0, ay + ah + GAP


def origin_resting(anchor, anchor_size, width: float, y_down: bool = False) -> Tuple[float, float]:
    """In the dictation pill's own resting place: centred on it, bottom edges
    level."""
    ax, ay = anchor
    aw, ah = anchor_size
    if y_down:
        return ax + (aw - width) / 2.0, ay + ah - HEIGHT
    return ax + (aw - width) / 2.0, ay


def place(anchor, anchor_size, width: float, shown=None, y_down: bool = False) -> Tuple[float, float]:
    """Where a message ``width`` wide goes. ``anchor`` is where the dictation
    pill's window is, or would be; ``shown`` is that window's rectangle while
    the dictation pill is on screen, else None."""
    x, y = origin_resting(anchor, anchor_size, width, y_down)
    if shown is not None and overlaps((x, y, width, HEIGHT), shown):
        return origin_above((shown[0], shown[1]), (shown[2], shown[3]), width, y_down)
    return x, y


def settles(pill, rect, left, y_down: bool = False) -> bool:
    """The dictation pill, whose window was ``left``, has gone from the
    screen. Does the message at ``rect`` come down into the resting place?
    Only one that was sitting above that pill, and never a Done: it has two
    seconds, and a jump would be more to look at than the message."""
    if pill is None or pill.kind == DONE:
        return False
    x, y, w, h = rect
    lx, ly, lw, lh = left
    if not (x < lx + lw and lx < x + w):
        return False                    # not over that pill: another screen
    return (y + h <= ly) if y_down else (y >= ly + lh)


def overlaps(a, b) -> bool:
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def clear_of(rect, other, screen):
    """``rect`` moved so it does not cover ``other`` (the meeting bar): beside
    it on the same line when there is room on the screen, else under it."""
    if other is None or not overlaps(rect, other):
        return rect
    x, y, w, h = rect
    ox, oy, ow, oh = other
    sx, sy, sw, sh = screen
    if ox + ow + GAP + w <= sx + sw:
        return (ox + ow + GAP, oy, w, h)
    if ox - GAP - w >= sx:
        return (ox - GAP - w, oy, w, h)
    return (x, oy - GAP - h, w, h)
