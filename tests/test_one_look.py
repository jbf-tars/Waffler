"""Home, Meetings and Settings are one app.

The Meetings tab is a page of its own in a frame (ui/meetings-app, built from
the natter-notes repository). It used to look like a second app inside the
window: another logo and the word "Meetings", a large white "New recording"
pill, other fonts, and a strip under it with a switch. Now:

  * the window hands the frame every design token of ui/tokens.css as it has
    resolved it (ui/meetings.js wafflerTokens), and the frame's own stylesheet
    for Waffler is written only in those names;
  * the frame has no brand header, and "New recording" is an ordinary control
    the height of the search field under it;
  * the strip is gone: its switch is a row in Settings, Shortcuts;
  * Settings' side menu has no footer, and About does not say who made it.

The pure parts run in Node; the rest is checked by source, and the build by
what is in it (it is minified, so by the rules and words that survive that).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_few_words import words  # noqa: E402
from test_ui_logic import NODE, code_only, js, needs_node, read  # noqa: E402

BUILD = ROOT / "ui" / "meetings-app"


# ── the tokens the window hands over ─────────────────────────────────────────

def _declared(block):
    return re.findall(r"(?m)^\s*(--[a-z0-9-]+)\s*:", block) or re.findall(r"(--[a-z0-9-]+)\s*:", block)


def _token_names():
    """Every token of tokens.css: the themed ones (the light block names them
    all) and the ones that are the same in both themes."""
    css = read("tokens.css")
    light = css[css.index(":root,\nbody {"):css.index("/* ── Night theme")]
    same = css[css.index("/* ── Type, space, shape, motion"):css.index("/* ── Names the older screens still use")]
    return _declared(light) + re.findall(r"(--[a-z0-9-]+)\s*:", same)


def _sent_names():
    src = code_only(read("meetings.js"))
    block = src[src.index("const TOKENS = ["):src.index("];", src.index("const TOKENS = ["))]
    return re.findall(r"'(--[a-z0-9-]+)'", block)


def test_the_window_hands_over_every_token_of_tokens_css_and_nothing_else():
    declared, sent = _token_names(), _sent_names()
    assert len(declared) > 80 and len(set(declared)) == len(declared)
    assert sorted(sent) == sorted(declared), set(sent) ^ set(declared)
    # The night theme changes only tokens the light theme names, so none is missed.
    css = read("tokens.css")
    night = css[css.index('body[data-theme="dark"] {'):css.index("/* System on a dark OS")]
    assert set(_declared(night)) <= set(sent)


def test_they_are_the_values_this_window_shows_not_a_copy_of_them():
    src = code_only(read("meetings.js"))
    fn = src[src.index("window.wafflerTokens = function"):src.index("function sendTokens()")]
    assert "window.getComputedStyle(document.body)" in fn and "css.getPropertyValue(name)" in fn
    assert "document.body.dataset.theme" in fn
    assert "link[rel=\"stylesheet\"][href$=\"tokens.css\"]" in fn        # where the font files are declared
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", fn), "no colour is written out by hand"


def test_the_frame_is_told_when_it_loads_when_it_asks_and_when_the_theme_changes():
    src = code_only(read("meetings.js"))
    assert "$('mtFrame').addEventListener('load', sendTokens)" in src
    assert "e.source === f.contentWindow && e.data && e.data.type === 'meetings-tokens-wanted'" in src
    assert "new MutationObserver(sendTokens).observe(document.body, { attributes: true, attributeFilter: ['data-theme'] })" in src
    assert "(prefers-color-scheme: dark)" in src
    page = src[src.index("window.loadMeetingsPage = function"):]
    assert "sendTokens();" in page[:page.index("\n")]


@needs_node
def test_what_is_handed_over_is_read_from_the_page():
    """meetings.js run in Node with a stand-in page: the message carries the
    resolved value of each token, the theme, and the address of tokens.css."""
    code = f"""
      const seen = [];
      global.window = {{
        addEventListener() {{}},
        getComputedStyle: (el) => ({{ getPropertyValue: (n) => (el === global.document.body ? (n === '--faint' ? '' : ' value-of' + n + ' ') : '') }}),
      }};
      global.document = {{
        body: {{ dataset: {{ theme: 'dark' }} }},
        addEventListener() {{}},
        getElementById: () => null,
        querySelector: (sel) => {{ seen.push(sel); return {{ href: 'file:///app/ui/tokens.css' }}; }},
      }};
      require({json.dumps(str(ROOT / "ui" / "meetings.js"))});
      const out = global.window.wafflerTokens();
      process.stdout.write(JSON.stringify({{ out, seen }}));
    """
    done = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    out = got["out"]
    assert out["type"] == "meetings-tokens" and out["theme"] == "dark"
    assert out["sheet"] == "file:///app/ui/tokens.css"
    assert out["tokens"]["--bg"] == "value-of--bg" and out["tokens"]["--f-ui"] == "value-of--f-ui"
    assert "--faint" not in out["tokens"]                       # a token with no value is left out, not sent empty
    assert set(out["tokens"]) == set(_sent_names()) - {"--faint"}


# ── the build wears them ─────────────────────────────────────────────────────

def _built(kind):
    files = sorted((BUILD / "assets").glob(f"*.{kind}"))
    assert len(files) == 1, f"one {kind} file in ui/meetings-app/assets, found {[f.name for f in files]}"
    return files[0].read_text(encoding="utf-8")


def _waffler_rules():
    """(selector, declarations) for every rule of the build that applies only inside Waffler."""
    css = re.sub(r"/\*.*?\*/", "", _built("css"), flags=re.S)
    rules = [(s.strip(), d) for s, d in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]
    return [(s, d) for s, d in rules if ".in-waffler" in s]


def test_the_build_on_disk_is_the_one_the_page_loads():
    html = (BUILD / "index.html").read_text(encoding="utf-8")
    for kind in ("js", "css"):
        name = sorted((BUILD / "assets").glob(f"*.{kind}"))[0].name
        assert f"./assets/{name}" in html
    assert (BUILD / "README.md").is_file()
    assert 'data-src="meetings-app/index.html?embed=1"' in read("index.html")


def test_the_frame_takes_the_tokens_and_asks_for_them():
    js_ = _built("js")
    assert "wafflerTokens" in js_                # read from the window around it as it starts
    assert "meetings-tokens-wanted" in js_       # asked for, where it may not read it
    assert '"meetings-tokens"' in js_            # and taken whenever they are sent again
    assert "in-waffler" in js_                   # the class its Waffler stylesheet hangs on
    assert "tokens\\.css" in js_ or "tokens.css" in js_


def _tints(css):
    """Every rgba() colour in this CSS, as the build's minifier spells one (#rrggbbaa)."""
    return {"#%02x%02x%02x%02x" % (int(r), int(g), int(b), round(float(a) * 255))
            for r, g, b, a in re.findall(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([0-9.]+)\)", css)}


def _wafflers_own_tints():
    """The translucent colours ui/style.css and ui/components.css write out."""
    return _tints(code_only(read("style.css")) + code_only(read("components.css")))


def test_inside_waffler_every_colour_font_and_radius_is_a_waffler_token():
    rules = _waffler_rules()
    assert len(rules) > 120, "the Waffler stylesheet is in the build"
    sent = set(_sent_names())
    # Natter's own names, pointed at Waffler's tokens in one rule; and one made of them.
    mapping = next(d for s, d in rules if s == ".in-waffler body")
    mapped = set(re.findall(r"(--[a-z0-9-]+)\s*:", mapping))
    for name in ("--rail", "--raised", "--hover", "--ui", "--serif", "--mono", "--ink-btn"):
        assert name in mapped, name
    for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+)", mapping):
        used = set(re.findall(r"var\((--[a-z0-9-]+)", value))
        assert used and used <= sent, f"{name} must be made of Waffler's tokens: {value}"
    for selector, decls in rules:
        for name in re.findall(r"var\((--[a-z0-9-]+)", decls):
            assert name in sent or name in mapped, f"{selector}: {name} is not a token of tokens.css"
        # No colour of its own: white on the solid red "Finish" (as on Home), and the
        # three faint tints Waffler's own stylesheets write in the same parts (the
        # shadow under the open tab, the glow of a field with the keyboard, the toast's).
        colours = [c.lower() for c in re.findall(r"#[0-9A-Fa-f]{3,8}\b", decls)] + sorted(_tints(decls))
        assert set(colours) <= {"#fff", "#ffffff"} | _wafflers_own_tints(), f"{selector}: {colours}"
        assert "rgb(" not in decls and "hsl" not in decls, f"{selector}: {decls}"
        families = re.findall(r"font(?:-family)?\s*:[^;]*?([\"'][A-Za-z ]+[\"'])", decls)
        assert not families, f"{selector}: a font is named by hand: {families}"
        assert "var(--ui)" not in decls and "var(--serif)" not in decls, selector


def _rule(selector):
    found = [d for s, d in _waffler_rules() if s == selector]
    assert found, selector
    return ";".join(found)


def test_the_type_is_the_windows_own():
    assert "font:var(--t-body)" in _rule(".in-waffler body").replace(" ", "")
    # A meeting's title is set as "Home" and every Settings page's title are.
    title = _rule(".in-waffler .title").replace(" ", "")
    assert "font:40034px/1.08var(--f-serif)" in title and "letter-spacing:-.015em" in title.replace("-0.015em", "-.015em")
    settings = code_only(read("style.css"))
    ptitle = settings[settings.index(".settings-panel .ptitle {"):]
    assert "font: 400 34px/1.08 var(--f-serif);" in ptitle[:ptitle.index("}")]
    # Times and lengths as beside a dictation on Home (ui/style.css .j-w).
    assert "font:40011px/1.4var(--f-ui)" in _rule(".in-waffler .item .m").replace(" ", "")
    assert "font: 400 11px/1.4 var(--f-ui)" in settings[settings.index(".j-w {"):settings.index(".j-body {")]
    # Dates in the italic serif, the notes in the serif, a shortcut in the mono.
    assert "var(--t-accent)" in _rule(".in-waffler .list h3")
    assert "var(--f-serif)" in _rule(".in-waffler .md")
    assert "var(--f-mono)" in _rule(".in-waffler kbd")
    assert "outline:2pxsolidvar(--focus)" in _rule(".in-waffler :focus-visible").replace(" ", "")


def test_the_side_menu_is_settings_side_menu():
    app = _rule(".in-waffler .app").replace(" ", "")
    assert "grid-template-columns:228pxminmax(0,1fr)" in app
    assert "grid-template-columns: 228px minmax(0, 1fr);" in code_only(read("style.css"))
    rail = _rule(".in-waffler .rail").replace(" ", "")
    assert "background:var(--surface)" in rail and "border-right:1pxsolidvar(--line)" in rail
    on = _rule(".in-waffler .item.on").replace(" ", "")
    assert "background:var(--card)" in on and "0001pxvar(--ring-ctl)" in on
    assert "background:var(--honey)" in _rule(".in-waffler .item.on:before").replace(" ", "")


def test_there_is_no_second_brand_in_the_frame():
    js_ = _built("js")
    # The word beside the old logo was "Meetings" inside Waffler and "Natter"
    # outside. Inside Waffler the header is not drawn, so the choice between
    # the two words is made in one place only: the page that says it could not start.
    assert len(re.findall(r'"Meetings"\s*:\s*"Natter"', js_)) == 1
    assert 'className:"word",children:"Natter"' in js_
    # Waffler's own window still names the page, for a screen reader and in the top bar.
    html = read("index.html")
    assert '<h1 class="sr-only" id="meetingsTitle" tabindex="-1">Meetings</h1>' in html
    assert ">Meetings</button>" in html


def test_new_recording_is_an_ordinary_control_the_height_of_the_search_field():
    record = _rule(".in-waffler .rail .record").replace(" ", "")
    search = _rule(".in-waffler .search").replace(" ", "")
    assert "height:34px" in record and "height:34px" in search
    assert "border-radius:var(--r-input)" in record and "border-radius:var(--r-input)" in search
    assert "background:var(--btn-sec-bg)" in record and "box-shadow:var(--shadow-btn)" in record     # Waffler's secondary button
    assert "var(--btn-pri-bg)" not in record and "var(--r-pill)" not in record                      # not the white pill
    assert "white-space:nowrap" in record
    assert "inset0001pxvar(--ring-ctl)" in search                                                  # Home's search field
    js_ = _built("js")
    assert "New recording" in js_ and "Ctrl N" in js_           # it still says its shortcut
    assert "var(--rec)" in _rule(".in-waffler .dot")            # and keeps the small red dot


# ── the strip under the Meetings tab is a row in Settings, Shortcuts ─────────

def test_the_meetings_tab_is_the_frame_and_nothing_under_it():
    html = read("index.html")
    panel = html[html.index('<div class="meetings-panel" id="meetingsPanel"'):]
    panel = panel[:panel.index("</div>")]
    assert panel.count("<iframe") == 1 and "<input" not in panel and "<label" not in panel
    for gone in ("mt-foot", "mtTapToggle", "Double-tap hotkey to record"):
        assert gone not in html and gone not in read("meetings.css") and gone not in read("meetings.js"), gone
    assert "meeting_double_tap" not in read("meetings.js")


@needs_node
def test_the_row_says_what_two_taps_do_in_few_words():
    v = js("L.meetingTapView({}, {})")
    assert v["label"] == "Double-tap to record a meeting"
    assert 4 <= len(words(v["label"])) <= 6
    assert v["desc"] == "Tap the hotkey twice to start. Twice to finish."
    assert len(words(v["desc"])) <= 10
    full = js("L.MEETING_TAP_TEXT.how")
    assert f'<li id="shortcutsHowMeetingTap">{full}</li>' in read("index.html")
    assert "not a hands-free dictation" in full              # which of the two uses of two taps wins
    assert "—" not in full and "–" not in full


@needs_node
def test_it_is_on_unless_switched_off_as_the_engine_reads_it():
    assert js("L.meetingTapView(undefined, undefined).on") is True
    assert js("L.meetingTapView({}, null).on") is True
    assert js("L.meetingTapView({meeting_double_tap: false}, {meeting_double_tap: true}).on") is False
    assert js("L.meetingTapView({meeting_double_tap: true}, {meeting_double_tap: false}).on") is True
    # An app that has not been restarted yet does not say it with the shortcuts: the settings do.
    assert js("L.meetingTapView({ok: true}, {meeting_double_tap: false}).on") is False
    for stray in ("'false'", "0", "null", "'no'"):
        assert js(f"L.meetingTapView({{meeting_double_tap: {stray}}}, {{}}).on") is True, stray


@needs_node
def test_the_toast_after_changing_it_is_a_plain_sentence():
    assert js("L.meetingTapSaved(true)") == "Two taps of your hotkey record a meeting."
    assert js("L.meetingTapSaved(false)") == "Two taps no longer record a meeting."


def test_the_row_is_a_labelled_switch_under_the_other_double_tap():
    app = read("app.js")
    assert "r.id === 'hands_free' ? _doubleTapRowHtml() + _meetingTapRowHtml() : ''" in app
    row = app[app.index("function _meetingTapRowHtml()"):app.index("async function setMeetingTap(on)")]
    assert '<label for="meetingTapToggle">' in row
    assert re.search(r'<input type="checkbox" class="tog" role="switch" id="meetingTapToggle" '
                     r'aria-describedby="meetingTapDesc"', row)
    assert 'role="listitem"' in row and "escHtml(v.label)" in row and "escHtml(v.desc)" in row
    assert "WL.meetingTapView(_shortcuts, _lastSettings)" in row
    assert "meetingTapToggle" not in read("index.html")           # drawn by the page's code, no static copy


def test_the_switch_saves_the_setting_and_goes_back_when_refused():
    app = code_only(read("app.js"))
    assert "if (e.target && e.target.id === 'meetingTapToggle') setMeetingTap(e.target.checked);" in app
    fn = app[app.index("async function setMeetingTap(on)"):app.index("function renderShortcuts()")]
    assert "await saveSetting('meeting_double_tap', !!on)" in fn
    assert "tog.checked = !on" in fn
    assert "key === 'meeting_double_tap'" in app and "WL.meetingTapSaved(value)" in app


def test_the_app_says_the_switch_with_the_shortcuts_and_still_applies_it_live():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    fn = src[src.index("    def get_shortcuts(self)"):src.index("    def check_shortcut(self")]
    assert 'page["meeting_double_tap"] = stored.get("meeting_double_tap") is not False' in fn
    assert '"meeting_double_tap":    stored.get("meeting_double_tap") is not False' in src     # get_settings, as before
    assert 'if "meeting_double_tap" in settings and _pipeline is not None:' in src
    assert "_pipeline.apply_meeting_tap()" in src


# ── nothing says who made it ─────────────────────────────────────────────────

def test_no_screen_says_made_by_one_person():
    for name in ("index.html", "app.js", "logic.js", "account.js", "meetings.js"):
        text = read(name).lower()
        for gone in ("made by", "one person"):
            assert gone not in text, f"{name}: {gone}"


def test_about_keeps_the_licence_in_one_plain_line():
    html = read("index.html")
    about = html[html.index('id="secAbout"'):]
    about = about[:about.index("</section>")]
    facts = about[about.index('<div class="sheet facts">'):]
    facts = facts[:facts.index("\n          </div>")]
    assert re.findall(r'<div class="k">([^<]+)</div><div class="v">([^<]+)</div>', facts) == [
        ("Licence", "MIT, free for ever"), ("Source", "jbf-tars/Waffler on GitHub")]
    assert ".facts { display: grid; grid-template-columns: repeat(2, 1fr);" in read("style.css")
