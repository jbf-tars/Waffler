"""The Usage page and the Journal's margin are wired to the new numbers, and
the map is legible and reachable in both themes (ui/index.html, app.js,
style.css, tokens.css). Source checks only: the sentences and buckets have
their own tests in test_stats_ui_logic.py.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def code_only(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def usage_section():
    html = read("index.html")
    return html[html.index('id="secUsage"'):html.index('id="secPrivacy"')]


def test_the_new_figures_live_in_usage_and_add_no_page_or_section():
    usage = usage_section()
    detail = usage[usage.index('id="usageDetail"'):]
    for part in ('id="usagePace"', 'id="insStreakValue"', 'id="insWeekValue"', 'id="insWeekLine"',
                 'id="insWpmValue"', 'id="usageSaved"', 'id="insSavedValue"', 'id="usageHeat"',
                 'id="heatGrid"', 'id="peakBars"', 'id="appsRows"', 'id="typingWpm"'):
        assert part in detail, part
    # Nothing to count yet: the page shows its empty card and none of these.
    assert usage.index('id="usageEmpty"') < usage.index('id="usageDetail"')
    html = read("index.html")
    assert html.count('class="snav-item"') == 9           # the Settings sections are as they were
    assert html.count('<button type="button" id="nav') == 3  # and so are the three pages


def test_the_typing_speed_is_a_labelled_number_field_with_a_place_for_its_error():
    usage = usage_section()
    assert '<label for="typingWpm">Your typing speed</label>' in usage
    field = usage[usage.index('<input id="typingWpm"'):]
    field = field[:field.index(">") + 1]
    for attr in ('type="number"', 'min="10"', 'max="200"', 'step="1"', 'onchange="onTypingSpeedChange(this)"',
                 'aria-describedby="typingWpmDesc"'):
        assert attr in field, attr
    assert 'id="typingWpmErr" role="alert" hidden' in usage


def test_how_this_is_estimated_is_a_real_disclosure():
    usage = usage_section()
    btn = usage[usage.index('<button type="button" class="link-btn" id="insSavedHowBtn"'):]
    btn = btn[:btn.index(">") + 1]
    assert 'aria-expanded="false"' in btn and 'aria-controls="insSavedHow"' in btn
    assert 'id="insSavedHow" hidden' in usage
    app = code_only(read("app.js"))
    toggle = app[app.index("function toggleSavedHow()"):]
    toggle = toggle[:toggle.index("\n}\n")]
    assert "box.hidden = !open" in toggle and "setAttribute('aria-expanded', String(open))" in toggle


def test_the_estimate_is_labelled_an_estimate_wherever_it_is_a_figure():
    usage = usage_section()
    assert '<h2 class="sh2">Time saved<span class="aside">an estimate</span></h2>' in usage
    app = code_only(read("app.js"))
    assert "WL.minutesSavedView(st)" in app
    logic = read("logic.js")
    assert "an estimate, if you typed at" in logic and "It is an estimate, not a measurement" in logic


def test_the_map_is_a_grid_with_one_tab_stop_labelled_cells_and_arrow_keys():
    usage = usage_section()
    grid = usage[usage.index('id="heatGrid"'):]
    grid = grid[:grid.index(">") + 1]
    assert 'role="grid"' in grid and 'aria-rowcount="7"' in grid and 'aria-colcount="26"' in grid
    assert "arrow keys" in grid and 'aria-describedby="heatSummary"' in grid
    assert 'id="heatTip" aria-hidden="true" hidden' in usage        # the tip is for the eyes: the cells are labelled
    app = code_only(read("app.js"))
    render = app[app.index("function _renderHeat(st)"):app.index("function _heatKey(e)")]
    assert 'role="gridcell"' in render and 'role="row" class="heat-row"' in render
    assert 'aria-label="${escHtml(c.label)}"' in render
    assert "tabindex=\"${c.i === v.last ? 0 : -1}\"" in render        # one stop, on today
    assert "<button" not in render                                      # a square does nothing when pressed
    assert "v.cells.filter((c) => !c.future)" in render                 # tomorrow has no cell
    assert "'focusin'" in render and "'mouseover'" in render and "'focusout'" in render
    key = app[app.index("function _heatKey(e)"):app.index("function _heatShowTip(")]
    assert "WL.heatMove(" in key and "e.preventDefault()" in key and "'Escape'" in key and ".focus()" in key


def test_the_peak_hour_has_a_table_for_screen_readers_and_the_bars_are_decoration():
    usage = usage_section()
    assert '<table class="sr-only" id="peakTable"></table>' in usage
    assert 'id="peakBars" aria-hidden="true"' in usage
    app = code_only(read("app.js"))
    peak = app[app.index("function _renderPeakHour(st)"):app.index("function _renderTopApps(st)")]
    assert "<caption>Dictations by hour of the day</caption>" in peak and '<th scope="row">' in peak


def test_the_journal_margin_line_and_the_page_use_the_same_week_sentences():
    app = code_only(read("app.js"))
    stats = app[app.index("function renderStats("):]
    stats = stats[:stats.index("\n}\n")]
    assert "WL.weekView(stats).rail" in stats and "weekLine.hidden = !line" in stats
    # The two older readings of the margin are as they were.
    assert "WL.weekBars(stats.daily_words, stats.daily_start)" in stats
    assert "WL.streakView(stats.streak_days, stats.longest_streak_days)" in stats
    html = read("index.html")
    assert '<p class="j-msub j-weekline" id="journalWeekLine" hidden></p>' in html
    load = app[app.index("async function loadUsageStats()"):app.index("function _renderUsageChart(")]
    assert "_renderInsights(st);" in load
    ins = app[app.index("function _renderInsights(st)"):app.index("function _renderSaved(st)")]
    for call in ("WL.weekStreakView(st)", "WL.weekView(st)", "WL.wpmView(st)"):
        assert call in ins, call


def test_the_typing_speed_is_saved_through_save_settings_and_the_page_recounts():
    app = code_only(read("app.js"))
    change = app[app.index("async function onTypingSpeedChange(input)"):app.index("let _heat = null;")]
    assert "WL.typingSpeedCheck(input.value)" in change
    assert "saveSetting('typing_wpm', check.value)" in change
    assert "await loadUsageStats()" in change
    save = app[app.index("async function saveSetting(key, value)"):]
    save = save[:save.index("\n}\n")]
    assert "key === 'typing_wpm'" in save and "WL.typingSpeedSaved(value)" in save


def test_no_new_text_in_the_page_has_a_dash_an_emoji_or_a_confirm():
    usage = usage_section()
    text = re.sub(r"<[^>]+>", " ", usage)
    assert "—" not in text and "–" not in text
    for src in (usage, read("app.js")[read("app.js").index("// ── Usage: weeks, time saved"):]):
        assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", src)
    assert "confirm(" not in code_only(read("app.js"))


# ── the map's colours and motion ─────────────────────────────────────────────

def _hex(css, name, block_start, block_end):
    block = css[block_start:block_end]
    m = re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}})", block)
    assert m, (name, block[:60])
    return m.group(1)


def _lum(h):
    r, g, b = (int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _theme_blocks():
    css = read("tokens.css")
    light = css.index(":root,\nbody {")
    dark = css.index('body[data-theme="dark"] {')
    auto = css.index('@media (prefers-color-scheme: dark)')
    type_start = css.index("/* ── Type, space, shape, motion")
    return css, {"light": (light, dark), "dark": (dark, auto), "auto": (auto, type_start)}


def test_the_five_steps_are_set_in_every_theme_and_each_step_shows():
    css, blocks = _theme_blocks()
    cards = {"light": "#FFFFFF", "dark": "#1C1917", "auto": "#1C1917"}
    for theme, (a, b) in blocks.items():
        steps = [_hex(css, f"heat-{i}", a, b) for i in range(5)]
        for lo, hi in zip(steps, steps[1:]):
            assert _ratio(lo, hi) >= 1.25, (theme, lo, hi, _ratio(lo, hi))     # a step you can see
        assert _ratio(steps[4], cards[theme]) >= 3, theme                      # the strongest mark stands out
        assert _ratio(steps[1], cards[theme]) >= 1.4, theme                    # a day with words is not the card
    # System on a dark computer uses the night ramp.
    assert [_hex(css, f"heat-{i}", *blocks["auto"]) for i in range(5)] == \
        [_hex(css, f"heat-{i}", *blocks["dark"]) for i in range(5)]


def test_the_map_has_a_visible_focus_ring_and_never_moves_or_animates():
    style = read("style.css")
    assert ".heat-grid .hm-c:focus-visible { outline: 2px solid var(--focus);" in style
    block = style[style.index("/* ── Usage: weeks, time saved"):style.index("/* ── Privacy and data")]
    # components.css takes the ring off tabindex="-1" things; this rule is stronger.
    assert "transition" not in block and "animation" not in block and "@keyframes" not in block
    assert "transform: rotate(180deg)" in block                        # the caret turns; it does not slide
    assert ".heat-row { display: grid; grid-template-columns: repeat(26, minmax(0, 1fr));" in block
    # Only colour tokens colour the map: nothing is hard-coded in the page's own CSS.
    assert not re.search(r"\.hm-[lc][^{]*\{[^}]*#[0-9A-Fa-f]{3,6}", block)
    assert "--faint" not in block.replace("background: var(--faint)", "")   # --faint is for bars, never text


def test_the_page_still_holds_at_the_narrowest_width():
    style = read("style.css")
    narrow = style[style.index("@media (max-width: 560px)"):]
    narrow = narrow[:narrow.index("/* ── Motion")]
    assert ".ledger-3" in narrow and ".ins-pair" in narrow and ".ins-pane + .ins-pane" in narrow
