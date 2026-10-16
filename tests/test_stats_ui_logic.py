"""The window's readings of the new numbers (ui/logic.js), run in Node.

Week streak, this week against last, minutes saved, words a minute, busiest
hour, top apps and the activity map are all drawn from get_stats; these check
the pure parts: the sentences, the buckets and the keys of the map. Without
Node the checks that need it are skipped, as in test_ui_logic.py.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
LOGIC = UI / "logic.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")


def js(expr, setup="", env=None):
    code = (f"const L = require({json.dumps(str(LOGIC))});\n{setup}\n"
            f"Promise.resolve({expr}).then((v) => process.stdout.write(JSON.stringify(v === undefined ? null : v)));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8",
                         env=dict(os.environ, **(env or {})))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def week(**kw):
    base = {"words_this_week": 2140, "words_last_week": 2400, "words_last_week_same_days": 1910,
            "week_change_pct": 12, "weekday": 6, "week_count": 9}
    base.update(kw)
    return base


# ── minutes and hours ────────────────────────────────────────────────────────

@needs_node
def test_minutes_are_said_in_the_biggest_sensible_unit():
    cases = {0: "0 min", -3: "0 min", None: "0 min", 0.2: "under a minute", 0.5: "1 min", 1.5: "2 min",
             44.4: "44 min", 59.4: "59 min", 59.6: "1 h", 60: "1 h", 61: "1 h 1 min", 125: "2 h 5 min",
             432: "7 h 12 min", 5999: "99 h 59 min", 6000: "100 h", 123456: "2,057 h"}
    for minutes, want in cases.items():
        assert js(f"L.formatMinutes({json.dumps(minutes)})") == want, minutes


@needs_node
def test_an_hour_is_said_on_the_clock_with_midnight_and_noon_by_name():
    assert [js(f"L.clockHour({h})") for h in (0, 1, 11, 12, 13, 23, 24)] == [
        "midnight", "1 am", "11 am", "noon", "1 pm", "11 pm", "midnight"]
    assert js("L.hourRange(14)") == "2 pm to 3 pm"
    assert js("L.hourRange(23)") == "11 pm to midnight"
    assert js("L.hourRange(0)") == "midnight to 1 am"
    assert js("L.hourRange(11)") == "11 am to noon"
    assert js("L.hourRange(12)") == "noon to 1 pm"


# ── the typing speed box ─────────────────────────────────────────────────────

@needs_node
def test_the_typing_speed_box_takes_a_whole_number_from_10_to_200():
    for raw, want in (("40", 40), (" 55 ", 55), ("10", 10), ("200", 200), (65, 65)):
        assert js(f"L.typingSpeedCheck({json.dumps(raw)})") == {"ok": True, "value": want, "message": ""}, raw
    for raw in ("9", "201", "", "  ", "abc", "40.5", "-40", "1e2", "4 0", None, "0", "00x", "12345"):
        r = js(f"L.typingSpeedCheck({json.dumps(raw)})")
        assert r["ok"] is False and r["value"] is None and r["message"], raw
    assert js("L.TYPING_SPEED_PROBLEM") == "Typing speed needs a whole number of words a minute, from 10 to 200."
    assert js("L.TYPING_WPM") == {"min": 10, "max": 200, "def": 40}


@needs_node
def test_the_typing_speed_toast_is_one_plain_sentence():
    line = js("L.typingSpeedSaved(55)")
    assert line == "Typing speed set to 55 words a minute. Minutes saved is updated."
    assert "—" not in line and "–" not in line


# ── this week against last ───────────────────────────────────────────────────

@needs_node
def test_a_whole_week_is_compared_with_last_week():
    v = js(f"L.weekView({json.dumps(week())})")
    assert v["words"] == "2,140" and v["wordsLabel"] == "words"
    assert v["phrase"] == "up 12% on last week"
    assert v["line"] == "Up 12% on last week."
    assert v["rail"] == "This week: 2,140 words, up 12% on last week."
    assert v["last"] == "Last week in full: 2,400 words."


@needs_node
def test_a_week_in_progress_is_compared_with_the_same_days():
    v = js(f"L.weekView({json.dumps(week(weekday=2, week_change_pct=-8))})")
    assert v["phrase"] == "down 8% on the same days last week"
    assert v["rail"] == "This week: 2,140 words, down 8% on the same days last week."
    assert v["line"] == "Down 8% on the same days last week."


@needs_node
def test_a_small_difference_is_about_the_same_and_a_small_base_is_said_in_words():
    same = js(f"L.weekView({json.dumps(week(week_change_pct=2))})")
    assert same["phrase"] == "about the same as last week"
    assert js(f"L.weekView({json.dumps(week(week_change_pct=-2))})")["phrase"] == "about the same as last week"
    assert js(f"L.weekView({json.dumps(week(week_change_pct=3))})")["phrase"] == "up 3% on last week"
    more = js(f"L.weekView({json.dumps(week(words_this_week=140, words_last_week_same_days=38, week_change_pct=268))})")
    assert more["phrase"] == "102 more than last week"
    fewer = js(f"L.weekView({json.dumps(week(words_this_week=10, words_last_week_same_days=40, week_change_pct=-75))})")
    assert fewer["phrase"] == "30 fewer than last week"
    huge = js(f"L.weekView({json.dumps(week(words_this_week=900, words_last_week_same_days=100, week_change_pct=800))})")
    assert huge["phrase"] == "well up on last week"


@needs_node
def test_nothing_to_compare_hides_the_journal_line_and_says_so_in_settings():
    none = js(f"L.weekView({json.dumps(week(week_change_pct=None, words_last_week=0, words_last_week_same_days=0))})")
    assert none["rail"] == "" and none["phrase"] == ""
    assert none["line"] == "Once there is a week behind you, this shows how the two compare."
    assert none["last"] == ""
    # Nothing said yet this week: a calm line, and nothing in the Journal margin.
    fresh = js(f"L.weekView({json.dumps(week(words_this_week=0, week_change_pct=-100, weekday=0, words_last_week_same_days=300))})")
    assert fresh["rail"] == "" and fresh["phrase"] == ""
    assert fresh["line"] == "A fresh week. Last week you dictated 2,400 words."
    empty = js("L.weekView({})")
    assert empty["rail"] == "" and empty["line"] == "" and empty["words"] == "0"
    assert js("L.weekView(null)")["rail"] == ""
    # Last week had words, but none on the days that have come round yet.
    early = js(f"L.weekView({json.dumps(week(weekday=1, words_last_week_same_days=0, week_change_pct=None))})")
    assert early["rail"] == "" and early["line"] == "" and early["last"] == "Last week in full: 2,400 words."


@needs_node
def test_one_word_is_not_plural():
    v = js(f"L.weekView({json.dumps(week(words_this_week=1, words_last_week_same_days=1, words_last_week=1, week_change_pct=0))})")
    assert v["wordsLabel"] == "word" and v["rail"] == "This week: 1 word, about the same as last week."
    assert v["last"] == "Last week in full: 1 word."


@needs_node
def test_no_sentence_about_the_week_has_a_dash_or_a_judgement():
    seen = []
    for pct in (None, 0, 2, 12, -12, 250, 900, -100):
        for weekday in (0, 6):
            for base in (0, 20, 500):
                v = js(f"L.weekView({json.dumps(week(week_change_pct=pct, weekday=weekday, words_last_week_same_days=base))})")
                seen += [v["line"], v["rail"], v["last"], v["phrase"]]
    for text in seen:
        assert "—" not in text and "–" not in text and " - " not in text
        for word in ("worse", "bad", "lazy", "slack", "fail", "!", "only", "missed"):
            assert word not in text.lower(), (word, text)


# ── the week streak ──────────────────────────────────────────────────────────

@needs_node
def test_the_week_streak_says_what_keeps_it_going():
    assert js("L.weekStreakView({week_streak: 0})") == {
        "value": "0 weeks", "sub": "Dictate on any day this week to start one."}
    assert js("L.weekStreakView({week_streak: 1, week_count: 3})") == {
        "value": "1 week", "sub": "in a row, counting this week."}
    assert js("L.weekStreakView({week_streak: 5, week_count: 0})") == {
        "value": "5 weeks", "sub": "in a row. Dictate this week to keep it going."}
    assert js("L.weekStreakView(null)")["value"] == "0 weeks"
    assert js("L.weekStreakView({week_streak: 'x'})")["value"] == "0 weeks"


# ── words a minute ───────────────────────────────────────────────────────────

@needs_node
def test_words_a_minute_waits_until_there_is_a_measure():
    v = js("L.wpmView({wpm: null, wpm_samples: 4})")
    assert v == {"known": False, "value": "Not yet", "sub": "A few more dictations and your speed shows up here."}
    assert js("L.wpmView(null)")["known"] is False
    assert js("L.wpmView({wpm: 0})")["known"] is False


@needs_node
def test_words_a_minute_says_where_it_comes_from_and_how_it_compares_with_typing():
    v = js("L.wpmView({wpm: 148, wpm_samples: 100, minutes_saved_basis: {typing_wpm: 40}})")
    assert v == {"known": True, "value": "148",
                 "sub": "words a minute, from your last 100 dictations. About 3.7 times as fast as typing at 40."}
    v = js("L.wpmView({wpm: 120, wpm_samples: 12, minutes_saved_basis: {typing_wpm: 60}})")
    assert v["sub"] == "words a minute, from your last 12 dictations. About 2 times as fast as typing at 60."
    v = js("L.wpmView({wpm: 50, wpm_samples: 10, minutes_saved_basis: {typing_wpm: 60}})")
    assert v["sub"] == "words a minute, from your last 10 dictations."          # no comparison unless it is faster


# ── minutes saved ────────────────────────────────────────────────────────────

BASIS = {"typing_wpm": 40, "words": 2400, "typing_minutes": 60.0, "speaking_minutes": 16.0, "timed": 30, "estimated": 0}


@needs_node
def test_minutes_saved_is_labelled_an_estimate_and_shows_its_working():
    v = js(f"L.minutesSavedView({json.dumps({'minutes_saved': 44.0, 'minutes_saved_basis': BASIS})})")
    assert v["value"] == "44 min"
    assert v["sub"] == "an estimate, if you typed at 40 words a minute"
    assert v["how"][0] == "Typing 2,400 words at 40 words a minute would take about 1 h."
    assert v["how"][1] == "Saying them took about 16 min, counted from the length of each recording."
    tiny = js("L.minutesSavedView({minutes_saved: 0.3, minutes_saved_basis: {typing_wpm: 40, words: 15, "
              "typing_minutes: 0.4, speaking_minutes: 0.1, timed: 0, estimated: 3}})")
    assert tiny["how"][0] == "Typing 15 words at 40 words a minute would take under a minute."
    assert tiny["how"][1].startswith("Saying them took under a minute, counted from 150 words a minute")
    assert all("about under" not in line and "about 0 min" not in line for line in tiny["how"])
    assert "never below zero" in v["how"][2] and "estimate" in v["how"][2]
    assert "Set the typing speed below" in v["how"][2]


@needs_node
def test_the_working_says_how_the_speaking_time_was_counted():
    both = dict(BASIS, timed=5, estimated=7)
    v = js(f"L.minutesSavedView({json.dumps({'minutes_saved': 4, 'minutes_saved_basis': both})})")
    assert "counted from the length of each recording where it was kept, and 150 words a minute otherwise" in v["how"][1]
    guessed = dict(BASIS, timed=0, estimated=12)
    v = js(f"L.minutesSavedView({json.dumps({'minutes_saved': 4, 'minutes_saved_basis': guessed})})")
    assert "150 words a minute, because these dictations did not note how long they took" in v["how"][1]


@needs_node
def test_minutes_saved_for_nobody_yet_and_for_a_fast_typist():
    v = js("L.minutesSavedView({minutes_saved: 0, minutes_saved_basis: {typing_wpm: 40, words: 0}})")
    assert v["value"] == "0 min" and v["sub"] == "Dictate a little and an estimate of the time saved shows up here."
    fast = dict(BASIS, typing_wpm=180, words=300)
    v = js(f"L.minutesSavedView({json.dumps({'minutes_saved': 0, 'minutes_saved_basis': fast})})")
    assert v["sub"] == "At 180 words a minute, typing these words would have taken no longer than saying them."
    assert js("L.minutesSavedView(null)")["value"] == "0 min"


# ── the busiest hour ─────────────────────────────────────────────────────────

def hours(**by_hour):
    out = [0] * 24
    for h, c in by_hour.items():
        out[int(h[1:])] = c
    return out


@needs_node
def test_the_busiest_hour_is_named_and_the_day_drawn_as_24_bars():
    stats = {"peak_hour": 14, "hour_counts": hours(h14=30, h9=15, h22=5), "peak_hour_needs": 20}
    v = js(f"L.peakHourView({json.dumps(stats)})")
    assert v["known"] is True and v["title"] == "Your busiest hour is 2 pm to 3 pm"
    assert v["sub"] == "30 of 50 dictations, by the clock on this computer."
    assert len(v["bars"]) == 24 and v["total"] == 50
    assert [b["hour"] for b in v["bars"]] == list(range(24))
    assert v["bars"][14]["height"] == 100 and v["bars"][14]["peak"] is True
    assert v["bars"][9]["height"] == 50 and v["bars"][9]["peak"] is False
    assert v["bars"][22]["height"] == 17 and v["bars"][3]["height"] == 0
    assert v["bars"][14]["label"] == "2 pm to 3 pm: 30 dictations"


@needs_node
def test_a_single_dictation_in_an_hour_is_still_a_visible_bar():
    v = js(f"L.peakHourView({json.dumps({'peak_hour': 8, 'hour_counts': hours(h8=200, h9=1), 'peak_hour_needs': 20})})")
    assert v["bars"][9]["height"] == 6 and v["bars"][9]["label"] == "9 am to 10 am: 1 dictation"


@needs_node
def test_before_the_busiest_hour_is_known_the_page_teaches():
    none = js("L.peakHourView({peak_hour: null, hour_counts: new Array(24).fill(0), peak_hour_needs: 20})")
    assert none["known"] is False and none["total"] == 0
    assert none["teach"] == "Your busiest hour shows up here after about 20 dictations."
    some = js(f"L.peakHourView({json.dumps({'peak_hour': None, 'hour_counts': hours(h9=6), 'peak_hour_needs': 20})})")
    assert some["teach"] == "A few more dictations and your busiest hour shows up here."
    assert len(some["bars"]) == 24
    assert js("L.peakHourView(null)")["known"] is False
    assert js("L.peakHourView({peak_hour: 30, hour_counts: [1]})")["known"] is False


# ── top apps ─────────────────────────────────────────────────────────────────

@needs_node
def test_top_apps_are_ranked_with_bars_against_the_busiest():
    stats = {"top_apps": [{"name": "Mail", "count": 40, "words": 900}, {"name": "Slack", "count": 10, "words": 100},
                          {"name": "Notes", "count": 1, "words": 4}],
             "apps_recorded": 51, "apps_not_recorded": 1204, "apps_total": 8}
    v = js(f"L.topAppsView({json.dumps(stats)})")
    assert [(r["name"], r["countText"], r["pct"], r["top"]) for r in v["rows"]] == [
        ("Mail", "40", 100, True), ("Slack", "10", 25, False), ("Notes", "1", 4, False)]
    assert v["empty"] is False and v["more"] == "5 more apps not shown."
    assert v["missing"] == {"label": "Not recorded", "countText": "1,204",
                            "note": "Earlier dictations, and ones where Waffler could not tell, did not note the app."}


@needs_node
def test_top_apps_with_nothing_recorded_teach_and_still_count_the_rest():
    stats = {"top_apps": [], "apps_recorded": 0, "apps_not_recorded": 30, "apps_total": 0}
    v = js(f"L.topAppsView({json.dumps(stats)})")
    assert v["empty"] is True and v["rows"] == [] and v["more"] == ""
    assert v["teach"].startswith("Waffler notes which app you dictate into, on this computer only.")
    assert v["missing"]["countText"] == "30"
    clean = js("L.topAppsView({top_apps: [{name: 'Mail', count: 3, words: 9}], apps_recorded: 3, apps_not_recorded: 0, apps_total: 1})")
    assert clean["missing"] is None and clean["more"] == ""
    assert js("L.topAppsView(null)")["empty"] is True
    junk = js("L.topAppsView({top_apps: [null, {}, {name: 5, count: 3}, {name: 'x', count: 0}, {name: 'Ok', count: 2}], apps_recorded: 2})")
    assert [r["name"] for r in junk["rows"]] == ["Ok"]
    one = js("L.topAppsView({top_apps: [{name: 'Mail', count: 1, words: 9}], apps_recorded: 1, apps_total: 3})")
    assert one["more"] == "2 more apps not shown."
    assert js("L.topAppsView({top_apps: [{name: 'Mail', count: 1}], apps_recorded: 1, apps_total: 2})")["more"] == "1 more app not shown."


# ── the activity map ─────────────────────────────────────────────────────────

@needs_node
def test_the_map_has_five_steps_and_one_big_day_does_not_flatten_the_rest():
    assert js("L.HEAT_LEVELS") == 5
    assert [js(f"L.heatLevel({w}, 100)") for w in (0, 1, 6, 7, 25, 26, 56, 57, 100, 500)] == [0, 1, 1, 2, 2, 3, 3, 4, 4, 4]
    assert js("L.heatLevel(0, 0)") == 0 and js("L.heatLevel(5, 0)") == 0
    assert js("L.heatLevel(null, 10)") == 0 and js("L.heatLevel('x', 10)") == 0
    assert js("L.heatLevel(100, 100)") == 4
    # Every active day the same: all dark, not all pale.
    assert js("L.heatLevel(120, 120)") == 4
    # 10 percent of the busiest day is still a visible second step.
    assert js("L.heatLevel(100, 1000)") == 2


def heat(last_week=None, **kw):
    """Twenty-six weeks of nothing, 9 words on the first day, and a last week
    of a Friday today: 3 words on the Monday and 12 today."""
    weeks = [[0] * 7 for _ in range(26)]
    weeks[-1] = last_week or [3, 0, 0, 0, 12, None, None]
    weeks[0][0] = 9
    base = {"start": "2026-04-20", "weeks": 26, "days": weeks, "max": 40}
    base.update(kw)
    return base


@needs_node
def test_the_map_cells_have_their_dates_labels_and_levels():
    hm = heat()
    hm["days"][-2][0] = 40
    v = js(f"L.heatmapView({json.dumps(hm)}, '2026-10-16')")
    assert v["weeks"] == 26 and len(v["cells"]) == 182
    first, mon, mon_last, today, future = v["cells"][0], v["cells"][175], v["cells"][168], v["cells"][179], v["cells"][180]
    assert (first["iso"], first["name"], first["words"], first["level"]) == ("2026-04-20", "Mon 20 Apr", 9, 2)
    assert first["label"] == "Mon 20 Apr: 9 words"
    assert (mon_last["iso"], mon_last["words"], mon_last["level"]) == ("2026-10-05", 40, 4)
    assert (mon["iso"], mon["words"], mon["level"]) == ("2026-10-12", 3, 2)
    assert today["iso"] == "2026-10-16" and today["label"] == "Today, Fri 16 Oct: 12 words" and today["level"] == 3
    assert future["future"] is True and future["level"] == -1 and future["label"] == "" and future["iso"] == "2026-10-17"
    assert v["last"] == 179
    assert [c["i"] for c in v["cells"]] == list(range(182))
    assert [c["dow"] for c in v["cells"][:9]] == [0, 1, 2, 3, 4, 5, 6, 0, 1]
    assert v["cells"][1]["label"] == "Tue 21 Apr: no words"
    one = js(f"L.heatmapView({json.dumps(heat(last_week=[1] + [None] * 6))}, '2026-10-12')")
    assert one["cells"][175]["label"] == "Today, Mon 12 Oct: 1 word" and one["last"] == 175


@needs_node
def test_the_map_names_the_year_only_for_other_years():
    hm = {"start": "2025-12-01", "weeks": 26, "days": [[1] * 7 for _ in range(26)], "max": 1}
    v = js(f"L.heatmapView({json.dumps(hm)}, '2026-05-31')")
    assert v["cells"][0]["name"] == "Mon 1 Dec 2025"
    assert v["cells"][30]["name"] == "Wed 31 Dec 2025"
    assert v["cells"][31]["name"] == "Thu 1 Jan"
    assert v["cells"][-1]["iso"] == "2026-05-31" and v["cells"][-1]["label"] == "Today, Sun 31 May: 1 word"


@needs_node
def test_the_map_counts_the_calendar_in_a_zone_that_changes_its_clocks():
    from datetime import date, timedelta
    # 27 October 2025 to 26 April 2026 has both New York changes in it (2 November, 8 March).
    hm = {"start": "2025-10-27", "weeks": 26, "days": [[1] * 7 for _ in range(26)], "max": 1}
    for zone in ("America/New_York", "Europe/London", "Australia/Sydney", "UTC"):
        v = js(f"L.heatmapView({json.dumps(hm)}, '2026-04-26')", env={"TZ": zone})
        first = date(2025, 10, 27)
        assert [c["iso"] for c in v["cells"]] == [(first + timedelta(days=i)).isoformat() for i in range(182)], zone
        assert all(c["dow"] == (first + timedelta(days=c["i"])).weekday() for c in v["cells"]), zone


@needs_node
def test_the_map_labels_each_month_once_and_never_on_top_of_another():
    v = js(f"L.heatmapView({json.dumps(heat())}, '2026-10-16')")
    labels = [(m["col"], m["label"]) for m in v["months"]]
    # April is dropped: May starts two columns later and would sit on top of it.
    assert labels == [(2, "May"), (6, "Jun"), (11, "Jul"), (15, "Aug"), (20, "Sep"), (24, "Oct")]
    cols = [c for c, _l in labels]
    assert all(b - a >= 3 for a, b in zip(cols, cols[1:]))
    # A window that starts on the 27th: April is dropped for the May that begins next column.
    v = js(f"L.heatmapView({json.dumps(heat(start='2026-04-27'))}, '2026-10-16')")
    assert v["months"][0] == {"col": 1, "label": "May"}
    # One that starts early in a month keeps that month's label, since the next is far enough on.
    v = js(f"L.heatmapView({json.dumps(heat(start='2026-04-06'))}, '2026-10-16')")
    assert v["months"][0] == {"col": 0, "label": "Apr"} and v["months"][1]["label"] == "May"


@needs_node
def test_the_map_summary_says_how_many_days_and_which_was_busiest():
    hm = heat()
    hm["days"][10][3] = 40
    v = js(f"L.heatmapView({json.dumps(hm)}, '2026-10-16')")
    assert v["active"] == 4 and v["busiest"]["words"] == 40
    assert v["summary"].startswith("4 days with a dictation in the last 26 weeks. Busiest: ")
    assert v["summary"].endswith(", 40 words.")
    # The most recent of equally busy days.
    tie = heat()
    tie["days"][10][3] = 12
    t = js(f"L.heatmapView({json.dumps(tie)}, '2026-10-16')")
    assert t["busiest"]["name"] == "Fri 16 Oct"
    empty = js(f"L.heatmapView({json.dumps({'start': '2026-04-20', 'weeks': 26, 'days': [[0]*7 for _ in range(26)], 'max': 0})}, '2026-10-18')")
    assert empty["active"] == 0 and empty["busiest"] is None
    assert empty["summary"] == "No dictations in the last 26 weeks yet. Each day you dictate fills in a square here."
    one = js(f"L.heatmapView({json.dumps(heat(last_week=[0, 0, 0, 0, 1, None, None]))}, '2026-10-16')")
    assert one["active"] == 2 and "2 days" in one["summary"]
    nothing = js("L.heatmapView(null, '')")
    assert nothing["cells"] == [] and nothing["last"] == -1 and nothing["weeks"] == 0
    assert js("L.heatmapView({}, '')")["cells"] == []


@needs_node
def test_the_arrow_keys_move_a_week_or_a_day_and_stop_at_the_edges():
    last = 179                      # today is a Friday in the last week (175 is its Monday)
    moves = {("ArrowRight", 100): 107, ("ArrowLeft", 100): 93,
             ("ArrowDown", 100): 101, ("ArrowUp", 100): 99,          # Wednesday to Thursday and Tuesday
             ("ArrowDown", 6): 6, ("ArrowUp", 7): 7, ("ArrowUp", 0): 0,   # a week's Sunday, a week's Monday
             ("ArrowLeft", 3): 3,                                    # the first week has nothing before it
             ("ArrowRight", 175): 175,                               # next Monday is not here yet
             ("ArrowRight", 172): 179,                               # last week's Friday to today
             ("ArrowDown", 179): 179,                                # tomorrow is not here yet
             ("ArrowRight", 179): 179,
             ("Home", 150): 0, ("End", 20): 179}
    for (key, at), want in moves.items():
        assert js(f"L.heatMove({at}, '{key}', {last})") == want, (key, at)
    assert js(f"L.heatMove(5, 'Enter', {last})") is None and js(f"L.heatMove(5, 'a', {last})") is None
    assert js("L.heatMove(5, 'ArrowRight', -1)") == 5


# ── the pages are wired to these ────────────────────────────────────────────

def test_logic_has_no_lookbehind_and_no_dashes_in_the_new_sentences():
    src = (UI / "logic.js").read_text(encoding="utf-8")
    block = src[src.index("The numbers beside the totals"):src.index("  return {\n    vocabLastUsed,")]
    assert "(?<" not in block
    strings = re.findall(r"'([^'\n]{12,})'|`([^`\n]{12,})`", block)
    for a, b in strings:
        text = re.sub(r"\$\{[^}]*\}", "", a or b)          # the words, not the arithmetic inside ${...}
        assert "—" not in text and "–" not in text and " - " not in text, text
