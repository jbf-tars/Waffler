"""The record button is Waffler's own: a waffle, not a red dot.

One rule, the same on the phone and the watch: a GOLD waffle means "start
recording"; the SAME waffle in RED means "recording now, press to stop".
Wherever the desktop app starts or shows a recording it draws that mark:

  * the page (ui/components.css .wrec): Home's "Record a meeting", and the
    dictation's own signs of recording (the status, Home's live strip, the
    setup's chip);
  * the Meetings tab (ui/meetings-app, built from natter-notes): New
    recording, Record / Add more, Finish, and the meeting being recorded in
    the list;
  * the meeting bar (src/pill.py has the numbers, src/overlay_process.py
    draws them) and the menu bar's clock.

No behaviour changed: the names a screen reader says, the keys and what a
press does are as they were. These tests pin the two states and that no plain
red circle is left as a record control.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from test_ui_logic import _css_rules_of, code_only, read  # noqa: E402

GOLD, GOLD_GRID = "#E8C267", "#B8892F"
SHEETS = ("tokens.css", "components.css", "style.css", "setup.css", "meetings.css")


def _rules(name, selector_part):
    return [(s, d) for s, d in _css_rules_of(name) if selector_part in s]


def _pockets(decls):
    """(x%, y%, size%) of every pocket a rule draws."""
    return [(float(x), float(y), float(w))
            for x, y, w in re.findall(r"var\(--wrec-c\)\s+([\d.]+)%\s+([\d.]+)%\s*/\s*([\d.]+)%\s+[\d.]+%\s+no-repeat", decls)]


# ── the colours ──────────────────────────────────────────────────────────────

def test_the_waffle_is_gold_and_its_recording_twin_is_the_apps_red():
    css = read("tokens.css")
    light = css[css.index(":root,\nbody {"):css.index("/* ── Night theme")]
    assert re.search(r"--waffle:\s*%s;" % GOLD, light) and re.search(r"--waffle-grid:\s*%s;" % GOLD_GRID, light)
    assert re.search(r"--rec:\s*#D94040;", light)               # the recording colour the page already had
    grid = re.search(r"--rec-grid:\s*(#[0-9A-F]{6});", light).group(1)
    r, g, b = (int(grid[i:i + 2], 16) for i in (1, 3, 5))
    assert (r, g, b) < (0xD9, 0x40, 0x40) and r > g + 60 and r > b + 60, "a darker red, still red"
    # The same in the night theme: the mark is one thing in both.
    rest = css[css.index("/* ── Night theme"):]
    for name in ("--waffle", "--waffle-grid", "--rec", "--rec-grid"):
        assert not re.search(r"(?m)^\s*%s\s*:" % name, rest), name


# ── the drawing ──────────────────────────────────────────────────────────────

def test_the_mark_is_a_round_cornered_waffle_of_sixteen_pockets():
    (sel, decls), = [(s, d) for s, d in _rules("components.css", ".wrec") if "--wrec-c:" in d]
    assert sel.startswith(".wrec,")
    assert "--wrec-body: var(--waffle);" in decls and "--wrec-cell: var(--waffle-grid);" in decls
    assert "border-radius: 27%" in decls and "var(--wrec-body);" in decls
    assert "box-shadow: inset 0 0 0 1px var(--wrec-cell)" in decls        # a rim, so gold holds on a cream button
    pockets = _pockets(decls)
    assert len(pockets) == 16 and len(set(pockets)) == 16
    # 3 of 24 wide, 2 apart, 3 from the edge: the same numbers as the meeting bar's (src/pill.py).
    import pill
    assert (pill.REC_UNITS, pill.REC_PAD, pill.REC_CELL, pill.REC_GAP, pill.REC_GRID) == (24, 3, 3, 2, 4)
    want = [100.0 * (pill.REC_PAD + i * (pill.REC_CELL + pill.REC_GAP)) / (pill.REC_UNITS - pill.REC_CELL) for i in range(4)]
    assert sorted({x for x, _, _ in pockets}) == pytest.approx(want, abs=0.01)
    assert sorted({y for _, y, _ in pockets}) == pytest.approx(want, abs=0.01)
    assert {w for _, _, w in pockets} == {100.0 * pill.REC_CELL / pill.REC_UNITS}


def test_recording_is_the_same_waffle_in_red_breathing_slowly():
    (sel, decls), = [(s, d) for s, d in _rules("components.css", ".wrec.is-rec") if "--wrec-body" in d]
    assert ".is-on > .wrec" in sel
    assert "--wrec-body: var(--rec);" in decls and "--wrec-cell: var(--rec-grid);" in decls
    assert "background" not in decls and "border-radius" not in decls     # the same drawing, only its colours
    css = code_only(read("components.css"))
    frames = css[css.index("@keyframes wrec-pulse"):]
    frames = frames[:frames.index("}\n}") + 3]
    assert "opacity: 1" in frames and "opacity: 0.72" in frames and "transform" not in frames
    assert "status-rec" not in css                                        # the old ring round the dot


def test_it_breathes_only_for_people_who_have_not_asked_for_less_motion():
    css = code_only(read("components.css"))
    moving = [(s, d) for s, d in _css_rules_of("components.css") if "wrec-pulse" in d]
    (sel, decls), = moving
    assert decls.strip() == "animation: wrec-pulse 2.4s ease-in-out infinite;"      # slow
    # ...the very marks that are red, and inside the media rule, nowhere else.
    (red_sel, _), = [(s, d) for s, d in _rules("components.css", ".wrec.is-rec") if "--wrec-body" in d]
    assert sel == red_sel
    media = css[css.index("@media (prefers-reduced-motion: no-preference)"):]
    assert media.index("wrec-pulse 2.4s") < media.index("@keyframes wrec-pulse")
    assert css.count("animation: wrec-pulse") == 1
    assert "animation-iteration-count: 1 !important" in code_only(read("style.css"))   # and the page's one rule


def test_no_plain_red_circle_is_left_on_the_page():
    for name in SHEETS:
        for sel, decls in _css_rules_of(name):
            round_ = re.search(r"border-radius:\s*(50%|999px)", decls)
            assert not (round_ and "var(--rec)" in decls), f"{name}: {sel} is a red circle"
            if "var(--rec)" in decls and "--wrec-body" not in decls:
                pytest.fail(f"{name}: {sel} uses the recording red outside the waffle")
    html = read("index.html")
    for gone in ("j-rec-dot", "j-rec-stop", "mt-dot ", "mt-rec", "mt-live"):
        assert gone not in html and gone not in read("meetings.css") and gone not in read("meetings.js"), gone


# ── Home's Record a meeting ──────────────────────────────────────────────────

def test_homes_button_wears_the_mark_and_says_what_it_always_said():
    html = read("index.html")
    button = html[html.index('<button type="button" class="j-rec" id="jRecord"'):]
    button = button[:button.index("</button>")]
    assert 'aria-label="Record a meeting"' in button and button.count("<span") == 3
    assert '<span class="wrec j-rec-mark" aria-hidden="true"></span>' in button
    assert '<span class="j-rec-label" id="jRecordLabel">Record a meeting</span>' in button
    src = code_only(read("meetings.js"))
    assert "jr.classList.toggle('is-on', !!s.recording);" in src         # .is-on > .wrec: the red one
    assert "jr.setAttribute('aria-label', s.recording ? 'Finish the meeting and write the notes' : 'Record a meeting');" in src
    assert "$('jRecordLabel').textContent = s.recording ? 'Finish' : 'Record a meeting';" in src
    assert "$('jRecord').addEventListener('click', toggle);" in src


def test_homes_button_is_large_and_the_pill_does_not_turn_red():
    rules = {}
    for sel, decls in _css_rules_of("meetings.css"):
        rules.setdefault(sel, decls)            # the first rule, not a narrow window's
    assert "width: 30px; height: 30px" in rules[".j-rec .wrec"]
    assert "background: var(--btn-pri-bg)" in rules[".j-rec"]
    for sel, decls in _css_rules_of("meetings.css"):
        if ".j-rec.is-on" in sel:
            assert "background" not in decls, sel                       # red on red would hide the waffle
    assert "danger-solid" not in code_only(read("meetings.css")).split("/* Ask. */")[0].split(".j-rec {")[1]


# ── the dictation's own signs of recording ───────────────────────────────────

@pytest.mark.parametrize("selector", [
    ".status.listening .status-dot",
    ".status.recording .status-dot",
    '.j-live[data-state="listening"] .j-live-dot',
    ".ob-recchip:not(.ob-recchip-honey):not(.ob-recchip-off) i",
    ".dot-rec",
])
def test_a_dictation_being_recorded_shows_the_red_waffle(selector):
    drawn = [d for s, d in _rules("components.css", ".wrec") if "--wrec-c:" in d and selector in " ".join(s.split())]
    red = [d for s, d in _rules("components.css", ".wrec.is-rec") if selector in " ".join(s.split())]
    assert drawn and red, selector
    small = [d for s, d in _css_rules_of("components.css") if selector in s and "width: 14px; height: 14px" in d]
    assert small, "14 px: the least at which the pockets show"


def test_the_other_states_keep_their_dots():
    comp = code_only(read("components.css"))
    assert re.search(r"\.status\.idle \.status-dot,\s*\.status\.done \.status-dot \{ background: var\(--ok\);", comp)
    assert re.search(r"\.status\.processing \.status-dot,\s*\.status\.not-sent \.status-dot \{ background: var\(--honey\); \}", comp)
    style = code_only(read("style.css"))
    assert '.j-live[data-state="processing"] .j-live-dot { background: var(--honey);' in style
    assert '.j-live[data-state="paused"] .j-live-dot { background: var(--muted);' in style


# ── the meeting bar and the menu bar (src/pill.py, src/overlay_process.py, app.py) ──

MAC = (ROOT / "src" / "overlay_process.py").read_text(encoding="utf-8")
WIN = (ROOT / "src" / "overlay_process_windows.py").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")


def test_the_numbers_of_the_mark_gold_at_rest_red_while_recording():
    import pill
    gold, red = pill.record_mark(22), pill.record_mark(22, recording=True)
    assert (gold.body, gold.cell) == (GOLD, GOLD_GRID) == (pill.GOLD, pill.REC_GOLD_GRID)
    assert (red.body, red.cell) == (pill.RED, pill.REC_RED_GRID) == ("#F0624F", "#A8322A")
    assert gold.pockets == red.pockets and gold.radius == red.radius      # the SAME waffle
    assert len(gold.pockets) == 16 and gold.radius == pytest.approx(22 * 0.27)
    xs = sorted({x for x, _, _ in gold.pockets})
    side = gold.pockets[0][2]
    assert len(xs) == 4 and xs[0] == pytest.approx(22 * 3 / 24) and side == pytest.approx(22 * 3 / 24)
    assert xs[-1] + side == pytest.approx(22 - xs[0]), "the same rim on every side"
    for a, b in zip(xs, xs[1:]):
        assert b - (a + side) == pytest.approx(22 * 2 / 24)             # the ridge between two pockets
    # Any size: Home's is the same drawing, larger.
    big = pill.record_mark(30)
    assert [round(v / 30, 6) for v in big.pockets[5]] == [round(v / 22, 6) for v in gold.pockets[5]]


def test_the_red_mark_breathes_slowly_and_stands_still_on_request():
    import pill
    assert pill.REC_PULSE_S >= 2.0 and 0.6 <= pill.REC_PULSE_LOW < 1.0     # slow, and soft: never near gone
    assert pill.record_pulse(0) == pytest.approx(1.0)
    assert pill.record_pulse(pill.REC_PULSE_S / 2) == pytest.approx(pill.REC_PULSE_LOW)
    assert pill.record_pulse(pill.REC_PULSE_S * 3) == pytest.approx(1.0)
    values = [pill.record_pulse(t / 10.0) for t in range(0, 100)]
    assert min(values) >= pill.REC_PULSE_LOW - 1e-9 and max(values) <= 1.0 + 1e-9
    assert all(pill.record_pulse(t / 10.0, still=True) == 1.0 for t in range(0, 50))


def test_the_meeting_bar_shows_the_red_waffle_the_time_and_finish():
    bar = MAC[MAC.index("class MeetingBarView(NSView):"):MAC.index("def _meeting_origin():")]
    assert "_draw_record_mark(MEET_MARK_X, MEET_MARK_Y, _pl.MARK, recording=True, alpha=_meet_mark_alpha())" in bar
    assert "bezierPathWithOvalInRect_" not in bar, "no red dot"
    assert "_meet_clock(_meet_elapsed)" in bar and "MEET_TIME_X" in bar          # the time stays beside it
    assert '"Finish"' in bar and '"Writing the notes"' in bar
    assert 'if not _meet_done and x >= MEET_W - MEET_FINISH_W - 10:' in bar and 'emit("meeting_finish")' in bar
    draw = MAC[MAC.index("def _draw_record_mark("):MAC.index("class WaffleView(NSView):")]
    assert "_pl.record_mark(size, recording)" in draw and "for px, py, side in mark.pockets:" in draw
    assert not re.search(r"#[0-9A-Fa-f]{6}", draw), "its colours are src/pill.py's"


def test_the_bars_mark_breathes_only_while_recording_and_never_with_reduce_motion():
    alpha = MAC[MAC.index("def _meet_mark_alpha():"):MAC.index("class MeetingBarView(NSView):")]
    assert "_pl.record_pulse(time.monotonic() - _meet_pulse_from, still=_reduce_motion())" in alpha
    pulse = MAC[MAC.index("def _meet_pulse(on):"):MAC.index("def _apply_meeting(cmd):")]
    assert "if on and _meet_view is not None and not _reduce_motion():" in pulse
    assert "_meet_pulse_timer.invalidate()" in pulse
    apply = MAC[MAC.index("def _apply_meeting(cmd):"):MAC.index("class PillMenuView(NSView):")]
    off = apply[:apply.index("_meet_elapsed = float(")]
    assert "_meet_pulse(False)" in off                                    # the bar is gone: nothing ticks
    assert "_meet_pulse(not _meet_done)" in apply                         # nor while it says "Writing the notes"
    # Only the mark is redrawn on a tick, not the whole bar.
    assert "self.setNeedsDisplayInRect_(NSMakeRect(MEET_MARK_X - 1, MEET_MARK_Y - 1, _pl.MARK + 2, _pl.MARK + 2))" in MAC


def test_windows_has_no_meeting_bar_so_no_dot_to_replace():
    assert '"meeting"' not in WIN and "MeetingBar" not in WIN
    assert "The meeting bar (Mac)" in (ROOT / "src" / "overlay.py").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "darwin", reason="draws with AppKit")
def test_the_bar_really_draws_a_red_waffle_at_22_points():
    """The real view, drawn off screen (no window): the mark's body and its
    pockets come out in the two reds, where the numbers say they are."""
    pytest.importorskip("AppKit")
    code = r"""
import sys, json
sys.path.insert(0, %r)
import AppKit
from AppKit import NSBitmapImageRep, NSGraphicsContext, NSMakeRect
import overlay_process as op, pill as pl
AppKit.NSApplication.sharedApplication()
S = 4
rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
    None, op.MEET_W * S, op.MEET_H * S, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 0)
rep.setSize_((op.MEET_W, op.MEET_H))
op._meet_elapsed, op._meet_done = 754, False
op._meet_mark_alpha = lambda: 1.0
view = op.MeetingBarView.alloc().initWithFrame_(NSMakeRect(0, 0, op.MEET_W, op.MEET_H))
ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
flipped = NSGraphicsContext.graphicsContextWithCGContext_flipped_(ctx.CGContext(), True)
NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.setCurrentContext_(flipped)
t = AppKit.NSAffineTransform.transform(); t.translateXBy_yBy_(0, op.MEET_H); t.scaleXBy_yBy_(1.0, -1.0); t.concat()
view.drawRect_(NSMakeRect(0, 0, op.MEET_W, op.MEET_H))
NSGraphicsContext.restoreGraphicsState()
def at(x, y):
    c = rep.colorAtX_y_(int(x * S), int(y * S))
    return [round(c.redComponent() * 255), round(c.greenComponent() * 255), round(c.blueComponent() * 255)]
m = pl.record_mark(pl.MARK, True)
px, py, side = m.pockets[5]
ox, oy = op.MEET_MARK_X, op.MEET_MARK_Y
print(json.dumps({"pocket": at(ox + px + side / 2, oy + py + side / 2),
                  "ridge": at(ox + px + side + (pl.MARK * 1 / 24), oy + py + side / 2),
                  "outside": at(ox + pl.MARK + 5, oy + pl.MARK / 2), "body": m.body, "cell": m.cell}))
""" % str(ROOT / "src")
    import json, os, tempfile
    env = dict(os.environ, WAFFLER_DATA_DIR=tempfile.mkdtemp(prefix="waffler-test-mark-"))
    done = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True, timeout=120, env=env)
    assert done.returncode == 0, done.stderr[-800:]
    got = json.loads(done.stdout.strip().splitlines()[-1])
    rgb = lambda h: [int(h[i:i + 2], 16) for i in (1, 3, 5)]            # noqa: E731
    near = lambda a, b: all(abs(x - y) <= 14 for x, y in zip(a, b))     # noqa: E731
    assert near(got["pocket"], rgb(got["cell"])), got
    assert near(got["ridge"], rgb(got["body"])), got
    assert near(got["outside"], rgb("#14110F")), got                    # the bar itself, not a red disc


def test_the_menu_bar_shows_the_red_waffle_and_the_time_not_a_dot():
    clock = APP[APP.index("def _meeting_clock():"):APP.index("def _dictations_for_ask(")]
    assert "\\u25cf" not in clock and "\u25cf" not in clock and "●" not in clock
    assert "set_title(clock)" in clock and 'set_title("", on=False)' in clock
    assert "set_bar(True, s)" in clock and "set_bar(True, 0, done=True)" in clock      # the bar, as before
    image = APP[APP.index("def _record_mark_image("):APP.index("def _menubar_show_recording(")]
    assert "_pill.record_mark(size, recording=True)" in image and "image.setTemplate_(False)" in image


class _Button:
    def __init__(self, image="waffle-template"):
        self._image, self.title, self.set = image, None, []

    def image(self):
        return self._image

    def setImage_(self, image):
        self._image = image
        self.set.append(image)

    def setTitle_(self, title):
        self.title = title


class _Item:
    def __init__(self, button):
        self._button = button

    def button(self):
        return self._button


def _menubar(image_fn):
    part = APP[APP.index("def _menubar_show_recording("):APP.index("def _meeting_clock():")]
    ns = {"_menubar_plain_image": None, "_record_mark_image": image_fn}
    exec(part, ns)
    return ns


def test_the_menu_bar_icon_is_the_red_waffle_while_recording_and_comes_back_after():
    ns = _menubar(lambda: "red-waffle")
    button = _Button()
    show = ns["_menubar_show_recording"]
    show(_Item(button), True, "0:01")
    show(_Item(button), True, "0:02")
    assert button.image() == "red-waffle" and button.set == ["red-waffle"], "set once, not every second"
    assert button.title == " 0:02"
    show(_Item(button), False)
    assert button.image() == "waffle-template" and button.title == ""
    show(_Item(button), False)                                            # twice is harmless
    assert button.image() == "waffle-template" and ns["_menubar_plain_image"] is None


def test_the_menu_bar_still_shows_the_time_if_the_mark_cannot_be_drawn():
    def broken():
        raise RuntimeError("no AppKit")
    ns = _menubar(broken)
    button = _Button()
    ns["_menubar_show_recording"](_Item(button), True, "1:05")
    assert button.image() == "waffle-template" and button.title == " 1:05"
    ns["_menubar_show_recording"](_Item(button), False)
    assert button.image() == "waffle-template" and button.title == ""
    # With no icon at all (the image could not be loaded) only the time shows.
    bare = _Button(image=None)
    _menubar(lambda: "red-waffle")["_menubar_show_recording"](_Item(bare), True, "0:09")
    assert bare.image() is None and bare.title == " 0:09"


# ── the Meetings tab (ui/meetings-app, built from natter-notes ui/) ──────────

def _built(kind):
    (one,) = sorted((ROOT / "ui" / "meetings-app" / "assets").glob(f"*.{kind}"))
    return one.read_text(encoding="utf-8")


def _built_rules():
    css = re.sub(r"/\*.*?\*/", "", _built("css"), flags=re.S)
    return [(" ".join(s.split()), d) for s, d in re.findall(r"([^{}@;]+)\{([^{}]*)\}", css)]


def _built_rule(selector):
    found = [d for s, d in _built_rules() if s == selector]
    assert found, selector
    return ";".join(found)


def test_the_meetings_tab_draws_the_same_waffle():
    """The frame has its own stylesheet, so the rule is repeated there: the
    same sixteen pockets, the same tokens, the same breath."""
    theirs = _built_rule(".in-waffler .wrec")
    (_, ours), = [(s, d) for s, d in _rules("components.css", ".wrec") if "--wrec-c:" in d]
    assert _pockets(theirs) == _pockets(ours) and len(_pockets(theirs)) == 16
    flat = theirs.replace(" ", "")
    assert "--wrec-body:var(--waffle)" in flat and "--wrec-cell:var(--waffle-grid)" in flat
    assert "border-radius:27%" in flat and "box-shadow:inset0001pxvar(--wrec-cell)" in flat
    red = _built_rule(".in-waffler .wrec.is-rec").replace(" ", "")
    assert "--wrec-body:var(--rec)" in red and "--wrec-cell:var(--rec-grid)" in red
    # It breathes only inside the rule for people who have not asked for less motion.
    css = _built("css")
    tight = css.replace(" ", "")
    assert tight.count("animation:wrec-pulse") == 1
    assert ("@media(prefers-reduced-motion:no-preference){.in-waffler.wrec.is-rec"
            "{animation:wrec-pulse2.4sease-in-outinfinite}}") in tight
    frames = css[css.index("@keyframes wrec-pulse"):][:120].replace(" ", "")
    assert "opacity:1" in frames and "opacity:.72" in frames
    # The three tokens it needs are handed to the frame.
    sent = code_only(read("meetings.js"))
    for name in ("'--rec'", "'--rec-grid'", "'--waffle'", "'--waffle-grid'"):
        assert name in sent, name


def test_every_record_control_in_the_meetings_tab_wears_it():
    js_ = _built("js")
    # One component: the waffle inside Waffler, the old dot in stand-alone Natter.
    assert '"wrec is-rec":"wrec"' in js_ and '"dot live":"dot"' in js_
    # The live dot beside the timer is drawn only outside Waffler (the phone's page,
    # which Waffler never shows, keeps its own).
    assert re.search(r'!\w+&&\w+\.jsx\("span",\{className:"dot live"\}\),\w+\.jsx\("span",\{className:"timer"', js_)
    for words in ("New recording", "Add more", "Finish", "Ctrl N"):
        assert words in js_, words
    assert "Finish recording and write the notes" in js_               # the stop control's title, as it was
    sizes = {".in-waffler .rail .record .wrec": "18px", ".in-waffler .welcome .record .wrec": "20px",
             ".in-waffler .item .t .wrec": "14px"}
    for selector, px in sizes.items():
        assert f"width:{px};height:{px}" in _built_rule(selector).replace(" ", ""), selector
    assert "opacity:.45" in _built_rule(".in-waffler .record:disabled .wrec").replace(" ", "").replace("0.45", ".45")


def test_finish_in_the_meetings_tab_is_the_ordinary_button_with_the_red_waffle():
    stop = _built_rule(".in-waffler .stop").replace(" ", "")
    assert "background:var(--btn-pri-bg)" in stop and "color:var(--btn-pri-fg)" in stop
    assert "danger-solid" not in stop, "not a red button: the red waffle would not show on it"
    assert "background: var(--btn-pri-bg)" in [d for s, d in _css_rules_of("meetings.css") if s == ".j-rec"][0]
    for selector, decls in _built_rules():
        if ".in-waffler" in selector:
            assert not ("var(--rec)" in decls and "--wrec-body" not in decls), f"{selector}: a red of its own"
