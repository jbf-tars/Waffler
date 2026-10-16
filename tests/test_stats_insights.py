"""Week streak, this week against last, busiest hour, activity map, minutes
saved, speaking speed and top apps (src/stats_insights.py, shown by
``journal_data.compute_stats``), worked out from synthetic histories.

Every number comes from the Journal on this computer. The stored times are
local wall-clock times ("2026-10-16T09:30:00"), so nothing here depends on the
machine's own zone; the tests that need a zone inject one with ``zoneinfo``
and skip where the system has no time zone data (some Windows setups).
"""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import journal_data as jd  # noqa: E402
import stats_insights as si  # noqa: E402

TODAY = date(2026, 10, 16)         # a Friday; this week began Monday 12th, last week Monday 5th


def tz_or_skip(name):
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        return zoneinfo.ZoneInfo(name)
    except Exception:                                   # no tz database on this system
        pytest.skip("no time zone data on this system")


def dic(ts, words=10, **extra):
    """A dictation of ``words`` words made at ``ts``."""
    item = {"timestamp": ts, "text": " ".join(["word"] * words)}
    item.update(extra)
    return item


def stats(history, today=TODAY, **kw):
    return jd.compute_stats(history, today, **kw)


def days_ago(n, base=TODAY, time="09:00:00"):
    return f"{(base - timedelta(days=n)).isoformat()}T{time}"


# ── every old key keeps its meaning ──────────────────────────────────────────

def test_the_old_keys_are_all_still_there_with_the_new_ones():
    s = stats([dic(days_ago(0))])
    for key in ("today_words", "today_count", "week_words", "week_count", "month_words",
                "month_count", "total_words", "total_count", "entries", "streak_days",
                "transform_count", "command_count", "daily_start", "daily_words",
                "longest_streak_days"):
        assert key in s, key
    for key in ("today", "weekday", "week_streak", "words_this_week", "words_last_week",
                "words_last_week_same_days", "week_change_pct", "words_this_month",
                "peak_hour", "hour_counts", "peak_hour_needs", "heatmap", "minutes_saved",
                "minutes_saved_basis", "wpm", "wpm_samples", "top_apps", "apps_recorded",
                "apps_not_recorded", "apps_total"):
        assert key in s, key


def test_the_whole_answer_can_be_sent_to_the_window_as_json():
    s = stats([dic(days_ago(i), app="Mail") for i in range(40)])
    assert json.loads(json.dumps(s)) == s


def test_an_empty_history_gives_zeros_and_nothing_to_report():
    s = stats([])
    assert s["week_streak"] == 0
    assert (s["words_this_week"], s["words_last_week"], s["words_last_week_same_days"]) == (0, 0, 0)
    assert s["week_change_pct"] is None and s["words_this_month"] == 0
    assert s["peak_hour"] is None and s["hour_counts"] == [0] * 24
    assert s["minutes_saved"] == 0.0 and s["wpm"] is None and s["wpm_samples"] == 0
    assert s["top_apps"] == [] and s["apps_not_recorded"] == 0 and s["apps_recorded"] == 0
    assert s["today"] == "2026-10-16" and s["weekday"] == 4


def test_an_entry_with_nothing_but_a_time_and_words_is_counted_everywhere_it_can_be():
    s = stats([{"timestamp": "2026-10-16T09:30:00", "text": "just a few words"}])
    assert s["week_streak"] == 1 and s["words_this_week"] == 4
    assert s["hour_counts"][9] == 1 and s["apps_not_recorded"] == 1 and s["top_apps"] == []
    # No speech length anywhere: no speed. No recording length: the estimate.
    assert s["wpm"] is None
    assert s["minutes_saved_basis"]["estimated"] == 1 and s["minutes_saved_basis"]["timed"] == 0


def test_garbage_in_the_history_never_raises():
    junk = [None, 5, "x", [], {}, {"timestamp": None}, {"timestamp": "soon", "text": "a b"},
            {"timestamp": "2026-13-45T00:00:00", "text": "a"},
            {"timestamp": "2026-10-16T25:99:00", "text": "a b c"},
            {"timestamp": "2026-10-16T10:00:00", "text": None, "styled": None, "app": 7,
             "duration_s": "long", "speech_s": [1]}]
    s = stats(junk)
    assert s["entries"] == 6                 # the dicts; None, 5, "x", [] are not entries
    assert s["words_this_week"] == 3         # only the one with a readable date and words


# ── the week streak ──────────────────────────────────────────────────────────

def monday_of(d):
    return d - timedelta(days=d.weekday())


def week_entries(weeks_back, weekday=2, base=TODAY):
    """One dictation in the week ``weeks_back`` weeks before base's week."""
    d = monday_of(base) - timedelta(weeks=weeks_back) + timedelta(days=weekday)
    return dic(f"{d.isoformat()}T10:00:00")


def test_the_week_streak_counts_consecutive_weeks_ending_this_week():
    h = [week_entries(0, 0), week_entries(1), week_entries(2), week_entries(3)]
    assert stats(h)["week_streak"] == 4


def test_the_week_streak_is_anchored_by_last_week_until_this_week_has_a_dictation():
    h = [week_entries(1), week_entries(2), week_entries(3)]
    assert stats(h)["week_streak"] == 3
    # And one dictation this week adds this week to it.
    assert stats(h + [dic(days_ago(0))])["week_streak"] == 4


def test_a_gap_of_a_whole_week_ends_the_streak():
    h = [week_entries(0), week_entries(1), week_entries(3), week_entries(4)]
    assert stats(h)["week_streak"] == 2
    assert stats([week_entries(2), week_entries(3)])["week_streak"] == 0     # a week of nothing since


def test_one_old_week_alone_is_no_streak():
    assert stats([week_entries(5)])["week_streak"] == 0


def test_a_week_runs_monday_to_sunday():
    monday = date(2026, 10, 12)
    sunday = date(2026, 10, 11)
    # Today is Monday; yesterday (Sunday) was last week, today this week.
    h = [dic(f"{sunday.isoformat()}T23:59:59"), dic(f"{monday.isoformat()}T00:00:00")]
    assert stats(h, today=monday)["week_streak"] == 2
    # Only Sunday's: still anchored by last week, one week long.
    assert stats(h[:1], today=monday)["week_streak"] == 1
    # The same two entries with today = Sunday are both in one week.
    h2 = [dic("2026-10-05T00:00:00"), dic("2026-10-11T23:59:59")]
    assert stats(h2, today=date(2026, 10, 11))["week_streak"] == 1


def test_two_dictations_more_than_a_week_apart_can_still_be_two_weeks_in_a_row():
    # Wednesday last week to Tuesday this week is six days apart, and two weeks.
    h = [dic("2026-10-07T10:00:00"), dic("2026-10-13T10:00:00")]
    assert stats(h)["week_streak"] == 2


def test_not_sent_notes_and_other_kinds_do_not_make_a_week():
    sent_nothing = {"timestamp": "2026-10-14T09:00:00", "failed": True, "styled": "Not sent: x"}
    rewrite = {"timestamp": "2026-10-14T09:00:00", "kind": "transform", "after": "a b c"}
    spoken = {"timestamp": "2026-10-14T09:00:00", "kind": "command", "after": "a b c"}
    assert stats([sent_nothing, rewrite, spoken])["week_streak"] == 0
    assert stats([sent_nothing, rewrite, spoken, week_entries(1)])["week_streak"] == 1


def test_dictations_dated_after_today_do_not_extend_the_streak():
    h = [dic("2026-10-27T10:00:00"), dic("2026-11-03T10:00:00")]
    assert stats(h)["week_streak"] == 0
    assert stats(h + [week_entries(1)])["week_streak"] == 1


def test_a_week_streak_runs_across_new_year_and_a_leap_day():
    # Mon 2025-12-29 starts the week with 1 January 2026 in it.
    h = [dic("2025-12-17T10:00:00"), dic("2025-12-24T10:00:00"), dic("2025-12-31T10:00:00"),
         dic("2026-01-05T10:00:00")]
    assert stats(h, today=date(2026, 1, 6))["week_streak"] == 4
    leap = [dic("2028-02-15T10:00:00"), dic("2028-02-29T10:00:00"), dic("2028-03-01T10:00:00")]
    # The 15th is in the week of Monday the 14th; the 29th and 1 March are in the week of
    # the 28th; the week of the 21st is empty, so the streak is only the last of them.
    assert stats(leap, today=date(2028, 3, 2))["week_streak"] == 1
    leap.append(dic("2028-02-22T10:00:00"))
    assert stats(leap, today=date(2028, 3, 2))["week_streak"] == 3


# ── this week against last week ──────────────────────────────────────────────

def test_this_week_is_monday_to_today_and_last_week_is_the_whole_week_before():
    h = [dic("2026-10-04T10:00:00", 99),      # Sunday before last week: not in either
         dic("2026-10-05T10:00:00", 100),     # last Monday
         dic("2026-10-08T10:00:00", 50),      # last Thursday
         dic("2026-10-09T10:00:00", 7),       # last Friday (today's weekday)
         dic("2026-10-11T23:59:59", 20),      # last Sunday
         dic("2026-10-12T08:00:00", 60),      # Monday
         dic("2026-10-16T10:00:00", 40)]      # today
    s = stats(h)
    assert s["words_this_week"] == 100 and s["week_words"] == 100
    assert s["words_last_week"] == 100 + 50 + 7 + 20 == 177
    assert s["words_last_week_same_days"] == 100 + 50 + 7        # Monday to Friday
    assert s["week_change_pct"] == -36                           # (100 - 157) / 157


def test_the_change_compares_the_same_days_so_a_new_week_is_not_called_a_collapse():
    # Monday morning: 30 words today against 40 on last Monday and 400 in all of last week.
    monday = date(2026, 10, 12)
    h = [dic("2026-10-05T10:00:00", 40), dic("2026-10-07T10:00:00", 360), dic("2026-10-12T09:00:00", 30)]
    s = stats(h, today=monday)
    assert s["words_this_week"] == 30 and s["words_last_week"] == 400
    assert s["words_last_week_same_days"] == 40 and s["week_change_pct"] == -25


def test_on_sunday_the_same_days_are_the_whole_week():
    sunday = date(2026, 10, 18)
    h = [dic("2026-10-06T10:00:00", 100), dic("2026-10-18T10:00:00", 150)]
    s = stats(h, today=sunday)
    assert s["words_last_week"] == s["words_last_week_same_days"] == 100
    assert s["week_change_pct"] == 50


def test_there_is_no_change_to_report_when_there_is_nothing_to_compare_with():
    this_week_only = [dic("2026-10-13T10:00:00", 80)]
    s = stats(this_week_only)
    assert s["words_this_week"] == 80 and s["week_change_pct"] is None
    # Last week had words, but none on the days that have come round so far.
    late_last_week = [dic("2026-10-11T10:00:00", 80), dic("2026-10-13T10:00:00", 80)]
    s = stats(late_last_week)
    assert s["words_last_week"] == 80 and s["words_last_week_same_days"] == 0
    assert s["week_change_pct"] is None
    # Nothing this week against something is a real change.
    s = stats([dic("2026-10-06T10:00:00", 80)])
    assert s["week_change_pct"] == -100


def test_the_change_rounds_half_away_from_zero():
    assert si.change_pct(9, 8) == 13           # +12.5
    assert si.change_pct(7, 8) == -13          # -12.5
    assert si.change_pct(8, 8) == 0
    assert si.change_pct(3, 0) is None
    assert si.change_pct(0, 0) is None
    assert si.change_pct(30, 10) == 200


def test_not_sent_notes_and_other_kinds_add_no_words_to_either_week():
    h = [dic("2026-10-13T10:00:00", 10),
         {"timestamp": "2026-10-13T11:00:00", "failed": True, "styled": "Not sent: a note of many words"},
         {"timestamp": "2026-10-13T12:00:00", "kind": "transform", "after": "w " * 50},
         {"timestamp": "2026-10-06T12:00:00", "kind": "command", "action": "draft", "after": "w " * 50},
         dic("2026-10-06T13:00:00", 5)]
    s = stats(h)
    assert s["words_this_week"] == 10 and s["words_last_week"] == 5


def test_the_month_is_the_calendar_month_and_agrees_with_the_old_count():
    h = [dic("2026-09-30T23:59:59", 5), dic("2026-10-01T00:00:00", 7), dic("2026-10-16T10:00:00", 11)]
    s = stats(h)
    assert s["words_this_month"] == 18 == s["month_words"]
    # On the first of a month it is only that day.
    s = stats(h[:2], today=date(2026, 10, 1))
    assert s["words_this_month"] == 7
    # A leap day is in its month and in the week of the 28th.
    s = stats([dic("2028-02-29T09:00:00", 6), dic("2028-03-01T09:00:00", 2)], today=date(2028, 2, 29))
    assert s["words_this_month"] == 6 and s["words_this_week"] == 6


# ── the busiest hour ─────────────────────────────────────────────────────────

def test_the_busiest_hour_is_only_reported_after_enough_dictations():
    needs = si.PEAK_HOUR_MIN_ENTRIES
    few = [dic(days_ago(i, time="14:10:00")) for i in range(needs - 1)]
    s = stats(few)
    assert s["peak_hour"] is None and s["peak_hour_needs"] == needs
    assert s["hour_counts"][14] == needs - 1            # the counts are there to see
    s = stats(few + [dic(days_ago(30, time="14:59:59"))])
    assert s["peak_hour"] == 14 and s["hour_counts"][14] == needs


def test_the_busiest_hour_is_the_one_with_most_dictations_and_the_earlier_wins_a_tie():
    h = ([dic(days_ago(i, time="09:30:00")) for i in range(12)]
         + [dic(days_ago(i, time="17:05:00")) for i in range(12)]
         + [dic(days_ago(i, time="23:59:59")) for i in range(3)]
         + [dic(days_ago(i, time="00:00:00")) for i in range(3)])
    s = stats(h)
    assert s["peak_hour"] == 9
    assert s["hour_counts"][9] == 12 and s["hour_counts"][17] == 12
    assert s["hour_counts"][23] == 3 and s["hour_counts"][0] == 3
    assert sum(s["hour_counts"]) == 30
    h += [dic(days_ago(40 + i, time="17:00:00")) for i in range(2)]
    assert stats(h)["peak_hour"] == 17


def test_hours_leave_out_not_sent_notes_other_kinds_and_times_that_cannot_be_read():
    h = [dic(days_ago(i, time="08:00:00")) for i in range(25)]
    h += [{"timestamp": days_ago(1, time="03:00:00"), "failed": True, "styled": "Not sent: x"},
          {"timestamp": days_ago(1, time="03:00:00"), "kind": "transform", "after": "a b"},
          {"timestamp": days_ago(1, time="03:00:00"), "kind": "command", "after": "a b"},
          {"timestamp": days_ago(1, time="99:00:00"), "text": "a b"},
          {"timestamp": days_ago(1)[:10], "text": "a b"},              # a date and no time
          {"timestamp": days_ago(1)[:10] + "Tnoon", "text": "a b"}]
    s = stats(h)
    assert s["hour_counts"][3] == 0 and sum(s["hour_counts"]) == 25
    assert s["peak_hour"] == 8


def test_hours_are_the_local_wall_clock_across_both_daylight_saving_changes():
    ny = tz_or_skip("America/New_York")

    def local_stamps(start_utc, step_minutes, n):
        out = []
        t = start_utc
        for _ in range(n):
            out.append(t.astimezone(ny).replace(tzinfo=None).isoformat(timespec="seconds"))
            t += timedelta(minutes=step_minutes)
        return out

    # Spring forward, Sunday 8 March 2026: 02:00 jumps to 03:00 at 07:00 UTC, so a dictation
    # every half hour from midnight has none in the hour that did not happen.
    spring = local_stamps(datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc), 30, 8)
    assert spring[0] == "2026-03-08T00:00:00" and not any(t[11:13] == "02" for t in spring)
    s = stats([dic(t) for t in spring], today=date(2026, 3, 8))
    assert s["hour_counts"][:5] == [2, 2, 0, 2, 2]
    assert s["words_this_week"] == 80 and s["today_words"] == 80
    # Fall back, Sunday 1 November 2026: 01:00 to 02:00 happens twice, and both are hour 1.
    fall = local_stamps(datetime(2026, 11, 1, 4, 30, tzinfo=timezone.utc), 30, 8)
    assert fall[0] == "2026-11-01T00:30:00" and fall.count("2026-11-01T01:30:00") == 2
    s = stats([dic(t) for t in fall], today=date(2026, 11, 1))
    assert s["hour_counts"][:4] == [1, 4, 2, 1]
    assert s["today_words"] == 80 and s["today_count"] == 8


# ── what day it is where the person is ───────────────────────────────────────

def test_local_today_follows_the_zone_it_is_given_not_utc():
    ny = tz_or_skip("America/New_York")
    # 03:30 UTC on 9 March is 23:30 on the 8th in New York, after the clocks went forward.
    assert si.local_today(datetime(2026, 3, 9, 3, 30, tzinfo=timezone.utc), ny) == date(2026, 3, 8)
    # 05:30 and 06:30 UTC on 1 November are both 01:30, before and after the clocks go back.
    assert si.local_today(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), ny) == date(2026, 11, 1)
    assert si.local_today(datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), ny) == date(2026, 11, 1)
    # 03:59 UTC on 1 November is still 23:59 on 31 October.
    assert si.local_today(datetime(2026, 11, 1, 3, 59, tzinfo=timezone.utc), ny) == date(2026, 10, 31)
    tokyo = tz_or_skip("Asia/Tokyo")
    assert si.local_today(datetime(2026, 10, 16, 16, 0, tzinfo=timezone.utc), tokyo) == date(2026, 10, 17)


def test_local_today_reads_a_plain_clock_as_local_time_and_defaults_to_now():
    assert si.local_today(datetime(2026, 10, 16, 23, 59, 59)) == date(2026, 10, 16)
    assert si.local_today(datetime(2026, 10, 17, 0, 0, 0)) == date(2026, 10, 17)
    assert isinstance(si.local_today(), date)


# ── the activity map ─────────────────────────────────────────────────────────

def test_the_map_is_26_weeks_of_7_days_ending_with_this_week():
    s = stats([dic(days_ago(0), 5)])
    hm = s["heatmap"]
    assert hm["weeks"] == 26 == len(hm["days"]) and all(len(w) == 7 for w in hm["days"])
    assert hm["start"] == (monday_of(TODAY) - timedelta(weeks=25)).isoformat() == "2026-04-20"
    assert date.fromisoformat(hm["start"]).weekday() == 0
    last = hm["days"][-1]
    assert last[:5] == [0, 0, 0, 0, 5] and last[5:] == [None, None]    # Friday today
    assert hm["max"] == 5


def test_the_map_has_the_words_of_each_day_and_only_those():
    h = [dic("2026-10-16T09:00:00", 5), dic("2026-10-16T18:00:00", 7),    # today, two dictations
         dic("2026-10-12T09:00:00", 3),                                    # Monday
         dic("2026-10-05T09:00:00", 40),                                   # last week's Monday
         dic("2026-04-20T09:00:00", 9),                                    # the first day shown
         dic("2026-04-19T09:00:00", 99),                                   # the day before it: off the map
         {"timestamp": "2026-10-14T09:00:00", "failed": True, "styled": "Not sent: note"},
         {"timestamp": "2026-10-14T09:00:00", "kind": "transform", "after": "w " * 30}]
    hm = stats(h)["heatmap"]
    assert hm["days"][0][0] == 9
    assert hm["days"][-1] == [3, 0, 0, 0, 12, None, None]
    assert hm["days"][-2][0] == 40
    assert hm["max"] == 40
    assert sum(v or 0 for w in hm["days"] for v in w) == 9 + 12 + 3 + 40


def test_a_dictation_dated_after_today_is_not_on_the_map_or_in_this_week():
    h = [dic("2026-10-16T10:00:00", 5), dic("2026-10-17T10:00:00", 99), dic("2026-12-25T10:00:00", 99)]
    s = stats(h)
    assert s["words_this_week"] == 5 and s["heatmap"]["days"][-1][5:] == [None, None]
    assert s["heatmap"]["max"] == 5 and sum(v or 0 for w in s["heatmap"]["days"] for v in w) == 5


def test_the_map_on_a_monday_and_on_a_sunday():
    mon = stats([dic("2026-10-12T09:00:00", 4)], today=date(2026, 10, 12))["heatmap"]
    assert mon["days"][-1] == [4] + [None] * 6
    assert mon["start"] == "2026-04-20"
    sun = stats([dic("2026-10-18T09:00:00", 4)], today=date(2026, 10, 18))["heatmap"]
    assert sun["days"][-1] == [0, 0, 0, 0, 0, 0, 4]
    assert sun["start"] == "2026-04-20"


def test_the_map_has_the_leap_day_and_the_month_ends():
    today = date(2028, 3, 5)                                     # a Sunday
    h = [dic("2028-02-29T09:00:00", 8), dic("2028-02-28T09:00:00", 2), dic("2028-03-01T09:00:00", 3)]
    hm = stats(h, today=today)["heatmap"]
    assert hm["days"][-1] == [2, 8, 3, 0, 0, 0, 0]               # Mon 28 Feb .. Sun 5 Mar
    assert date.fromisoformat(hm["start"]) + timedelta(days=7 * 25) == date(2028, 2, 28)


def test_every_cell_is_the_next_calendar_day_across_both_daylight_saving_changes():
    ny = tz_or_skip("America/New_York")
    # The window of the week of 8 March 2026 (clocks forward) and of 1 November (back):
    # one dictation a day at local noon, built the way the app stamps them.
    for first, today in ((date(2026, 2, 23), date(2026, 3, 15)), (date(2026, 10, 19), date(2026, 11, 8))):
        h = []
        d = first
        while d <= today:
            noon = datetime(d.year, d.month, d.day, 12, tzinfo=ny)
            h.append(dic(noon.replace(tzinfo=None).isoformat(timespec="seconds"), d.day))
            d += timedelta(days=1)
        hm = stats(h, today=today)["heatmap"]
        flat = [v for w in hm["days"] for v in w]
        start = date.fromisoformat(hm["start"])
        # The word count of a cell is its own date's day of the month: no cell is empty
        # or doubled in the hour of the change.
        for i, v in enumerate(flat):
            day = start + timedelta(days=i)
            if first <= day <= today:
                assert v == day.day, (day, v)
            elif day > today:
                assert v is None
        assert start.weekday() == 0


def test_an_empty_history_has_an_empty_map_of_the_right_shape():
    hm = stats([])["heatmap"]
    assert hm["max"] == 0 and len(hm["days"]) == 26
    assert all(v == 0 for w in hm["days"][:-1] for v in w)


# ── minutes saved ────────────────────────────────────────────────────────────

def test_minutes_saved_is_the_time_typing_would_take_less_the_time_speaking():
    # 80 words, no recording length: typing at 40 a minute is 2 minutes; speaking them at
    # 150 a minute is 32 seconds. 1.5 minutes saved.
    s = stats([dic(days_ago(0), 80)])
    b = s["minutes_saved_basis"]
    assert b["typing_wpm"] == 40 and b["words"] == 80
    assert b["typing_minutes"] == 2.0 and b["speaking_minutes"] == 0.5
    assert s["minutes_saved"] == 1.5
    # With the recording's own length (30 s) the speaking time is that.
    s = stats([dic(days_ago(0), 80, duration_s=30.0)])
    assert s["minutes_saved"] == 1.5
    assert s["minutes_saved_basis"]["timed"] == 1 and s["minutes_saved_basis"]["estimated"] == 0
    s = stats([dic(days_ago(0), 80, duration_s=60.0)])
    assert s["minutes_saved"] == 1.0


def test_a_slower_typist_saves_more_and_a_faster_one_less_and_none_is_never_negative():
    h = [dic(days_ago(i), 100, duration_s=40.0) for i in range(10)]
    saved = {w: stats(h, typing_wpm=w)["minutes_saved"] for w in (10, 25, 40, 80, 120, 200)}
    assert list(saved.values()) == sorted(saved.values(), reverse=True)
    assert saved[40] == round(10 * (100 / 40 - 40 / 60), 1) == 18.3
    assert saved[200] == 0.0           # 100 words typed in half a minute: nothing saved, never below 0
    assert all(v >= 0 for v in saved.values())
    assert stats(h, typing_wpm=40)["minutes_saved_basis"]["typing_wpm"] == 40


def test_minutes_saved_never_goes_below_zero_when_recordings_run_long():
    h = [dic(days_ago(0), 20, duration_s=900.0)]            # a quarter of an hour of silence
    s = stats(h)
    assert s["minutes_saved"] == 0.0 and s["minutes_saved"] >= 0
    # The long one is counted against the short ones (the estimate is not flattered).
    mixed = [dic(days_ago(1), 80, duration_s=30.0), dic(days_ago(0), 20, duration_s=90.0)]
    assert stats(mixed)["minutes_saved"] == round((2.0 - 0.5) + (0.5 - 1.5), 1) == 0.5


def test_a_recording_cannot_make_speaking_faster_than_anyone_speaks():
    s = stats([dic(days_ago(0), 300, duration_s=1.0)])
    assert s["minutes_saved_basis"]["speaking_minutes"] == 1.0         # 300 words at 300 a minute
    assert s["minutes_saved"] == round(300 / 40 - 1.0, 1) == 6.5


def test_unreadable_recording_lengths_fall_back_to_the_estimate():
    for bad in (None, 0, -5, "30", float("nan"), float("inf"), True, [3]):
        s = stats([dic(days_ago(0), 80, duration_s=bad)])
        assert s["minutes_saved_basis"]["estimated"] == 1, bad
        assert s["minutes_saved"] == 1.5, bad


def test_minutes_saved_counts_dictations_with_words_and_nothing_else():
    h = [dic(days_ago(0), 80),
         dic(days_ago(0), 0, duration_s=600.0),                                  # no words: costs nothing
         {"timestamp": days_ago(0), "failed": True, "styled": "Not sent: a note"},
         {"timestamp": days_ago(0), "kind": "transform", "after": "w " * 400, "duration_s": 5.0},
         {"timestamp": days_ago(0), "kind": "command", "after": "w " * 400, "duration_s": 5.0}]
    s = stats(h)
    assert s["minutes_saved"] == 1.5 and s["minutes_saved_basis"]["words"] == 80


def test_minutes_saved_covers_all_your_history_not_just_the_chart():
    old = dic("2025-01-02T09:00:00", 80)
    assert stats([old])["minutes_saved"] == 1.5


def test_the_typing_speed_setting_is_a_whole_number_from_10_to_200():
    assert si.TYPING_WPM_DEFAULT == 40 and (si.TYPING_WPM_MIN, si.TYPING_WPM_MAX) == (10, 200)
    for ok in (10, 40, 200, 55, 120.0):
        assert si.typing_wpm_problem(ok) is None, ok
    for bad in (9, 201, 0, -40, 40.5, "40", None, True, False, float("nan"), [40], {"a": 1}):
        assert si.typing_wpm_problem(bad), bad
    assert si.typing_wpm_of({}) == 40
    assert si.typing_wpm_of({"typing_wpm": 65}) == 65
    assert si.typing_wpm_of({"typing_wpm": 65.0}) == 65 and isinstance(si.typing_wpm_of({"typing_wpm": 65.0}), int)
    for bad in ("fast", 9, 1000, None, True, [], {}):
        assert si.typing_wpm_of({"typing_wpm": bad}) == 40, bad
    assert si.typing_wpm_of(None) == 40 and si.typing_wpm_of([1]) == 40
    assert si.clean_typing_wpm(65.0) == 65


def test_the_estimate_uses_a_setting_that_is_out_of_range_as_the_default():
    h = [dic(days_ago(0), 80)]
    assert stats(h, typing_wpm=5)["minutes_saved"] == stats(h)["minutes_saved"]
    assert stats(h, typing_wpm="x")["minutes_saved_basis"]["typing_wpm"] == 40
    assert stats(h, typing_wpm=True)["minutes_saved_basis"]["typing_wpm"] == 40


# ── words a minute ───────────────────────────────────────────────────────────

def spoken(i, words=30, speech=15.0, **extra):
    return dic(days_ago(i % 60, time=f"{9 + i % 8:02d}:{i % 60:02d}:00"), words, speech_s=speech, **extra)


def test_speed_is_words_over_speech_time_and_waits_for_ten_dictations():
    h = [spoken(i) for i in range(9)]                           # 30 words in 15 s = 120 a minute
    s = stats(h)
    assert s["wpm"] is None and s["wpm_samples"] == 9
    s = stats(h + [spoken(9)])
    assert s["wpm"] == 120 and s["wpm_samples"] == 10


def test_speed_is_the_total_words_over_the_total_time_not_an_average_of_averages():
    h = [spoken(i, words=10, speech=10.0) for i in range(9)]                  # 60 a minute, small
    h.append(spoken(9, words=600, speech=120.0))                              # 300 a minute, big
    s = stats(h)
    assert s["wpm"] == round((9 * 10 + 600) / ((9 * 10 + 120) / 60)) == 197


def test_speed_uses_the_latest_hundred_dictations():
    old = [spoken(i, words=30, speech=30.0) for i in range(150)]               # 60 a minute
    new = [spoken(150 + i, words=60, speech=15.0) for i in range(100)]         # 240 a minute
    s = stats(old + new)
    assert s["wpm"] == 240 and s["wpm_samples"] == 100


def test_older_dictations_borrow_their_speech_time_from_the_quality_log():
    h = [dic(days_ago(i, time="10:00:00"), 30) for i in range(10)]
    log = {h[i]["timestamp"]: 15.0 for i in range(10)}
    assert stats(h)["wpm"] is None
    s = stats(h, speech_log=log)
    assert s["wpm"] == 120 and s["wpm_samples"] == 10
    # An entry's own measurement wins over the log's.
    own = [dict(x, speech_s=30.0) for x in h]
    assert stats(own, speech_log=log)["wpm"] == 60
    # A lazy log is read once, and only when an entry needs it.
    calls = []
    assert stats(own, speech_log=lambda: calls.append(1) or log)["wpm"] == 60 and calls == []
    assert stats(h, speech_log=lambda: calls.append(1) or log)["wpm"] == 120 and calls == [1]


def test_dictations_without_a_usable_measurement_are_left_out():
    good = [spoken(i) for i in range(10)]
    skip = [dic(days_ago(1), 30),                                              # no speech time at all
            spoken(11, speech=0.0), spoken(12, speech=-3.0), spoken(13, speech="long"),
            spoken(14, speech=0.4),                                            # too short to time
            spoken(15, words=2),                                               # too few words
            spoken(16, words=30, speech=1.0),                                  # 1,800 a minute: a bad measurement
            spoken(17, words=5, speech=120.0),                                 # 2.5 a minute: not words per minute
            {"timestamp": days_ago(1), "failed": True, "styled": "Not sent", "speech_s": 10.0},
            {"timestamp": days_ago(1), "kind": "command", "after": "a b c d e f", "speech_s": 2.0}]
    s = stats(good + skip)
    assert s["wpm"] == 120 and s["wpm_samples"] == 10


def test_the_speed_counts_the_words_said_not_what_a_snippet_expanded_to():
    items = [dic(days_ago(i, time="10:00:00"), 30, speech_s=15.0, styled="Hello. " * 90) for i in range(10)]
    assert stats(items)["wpm"] == 120


def test_a_quality_log_file_is_read_for_timestamps_and_speech_and_nothing_else(tmp_path):
    p = tmp_path / "quality.jsonl"
    lines = [json.dumps({"timestamp": "2026-10-15T10:00:00", "speech_s": 12.5, "level": "ok"}),
             json.dumps({"timestamp": "2026-10-15T10:01:00", "speech_s": 0.0}),
             json.dumps({"timestamp": "2026-10-15T10:02:00"}),
             json.dumps({"speech_s": 4.0}),
             "not json at all",
             json.dumps([1, 2]),
             json.dumps({"timestamp": "2026-10-15T10:03:00", "speech_s": "9"}),
             json.dumps({"timestamp": "2026-10-15T10:04:00", "speech_s": 3.0}),
             ""]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert si.read_speech_log(p) == {"2026-10-15T10:00:00": 12.5, "2026-10-15T10:04:00": 3.0}
    assert si.read_speech_log(tmp_path / "missing.jsonl") == {}
    assert si.read_speech_log(None) == {}
    p.write_bytes(b"\xef\xbb\xbf" + b'{"timestamp": "t", "speech_s": 2}\n\xff\xfe\n')
    assert si.read_speech_log(p) == {"t": 2.0}


# ── top apps ─────────────────────────────────────────────────────────────────

def test_top_apps_are_ranked_by_dictations_with_the_rest_called_not_recorded():
    h = ([dic(days_ago(i), 5, app="Mail") for i in range(5)]
         + [dic(days_ago(i), 50, app="Slack") for i in range(3)]
         + [dic(days_ago(i), 5, app="Notes") for i in range(3)]       # ties Slack: more words wins
         + [dic(days_ago(i), 5) for i in range(4)])                    # before the app was recorded
    s = stats(h)
    assert [(a["name"], a["count"], a["words"]) for a in s["top_apps"]] == [
        ("Mail", 5, 25), ("Slack", 3, 150), ("Notes", 3, 15)]
    assert s["apps_recorded"] == 11 and s["apps_not_recorded"] == 4 and s["apps_total"] == 3


def test_only_the_top_five_are_listed_but_all_are_counted():
    h = []
    for n, name in enumerate(["A", "B", "C", "D", "E", "F", "G"]):
        h += [dic(days_ago(i), 5, app=name) for i in range(10 - n)]
    s = stats(h)
    assert [a["name"] for a in s["top_apps"]] == ["A", "B", "C", "D", "E"]
    assert s["apps_total"] == 7 and s["apps_recorded"] == sum(range(10, 3, -1))


def test_an_app_is_one_app_whatever_its_case_and_spacing():
    h = [dic(days_ago(0), 5, app="Google Chrome"), dic(days_ago(1), 5, app=" google  chrome "),
         dic(days_ago(2), 5, app="GOOGLE CHROME")]
    s = stats(h)
    assert len(s["top_apps"]) == 1 and s["top_apps"][0]["count"] == 3 and s["apps_total"] == 1
    assert s["top_apps"][0]["name"] == "GOOGLE CHROME"        # the latest spelling


def test_an_app_known_only_by_its_identifier_is_named_by_it_and_a_blank_is_not_recorded():
    h = [dic(days_ago(0), 5, app_bundle="com.example.notes"), dic(days_ago(1), 5, app="   "),
         dic(days_ago(2), 5, app=None), dic(days_ago(3), 5, app=12)]
    s = stats(h)
    assert [a["name"] for a in s["top_apps"]] == ["com.example.notes"]
    assert s["apps_not_recorded"] == 3


def test_top_apps_leave_out_not_sent_notes_and_other_kinds():
    h = [dic(days_ago(0), 5, app="Mail"),
         {"timestamp": days_ago(0), "failed": True, "styled": "Not sent", "app": "Mail"},
         {"timestamp": days_ago(0), "kind": "transform", "after": "a b", "app": "Notes"},
         {"timestamp": days_ago(0), "kind": "command", "after": "a b", "app": "Notes"}]
    s = stats(h)
    assert [(a["name"], a["count"]) for a in s["top_apps"]] == [("Mail", 1)]
    assert s["apps_not_recorded"] == 0


# ── cached with the history, no second parse ─────────────────────────────────

def cache_for(tmp_path, history, **kw):
    p = tmp_path / "history.json"
    p.write_text(json.dumps(history), encoding="utf-8")
    return p, jd.HistoryCache(p, lambda: json.loads(p.read_text(encoding="utf-8")), **kw)


def test_stats_are_worked_out_once_per_change_and_the_file_is_read_once(tmp_path):
    h = [dic(days_ago(i, time="10:00:00"), 20, app="Mail", speech_s=10.0) for i in range(30)]
    _p, cache = cache_for(tmp_path, h)
    first = cache.stats(TODAY)
    for _ in range(5):
        assert cache.stats(TODAY) == first
    assert cache.loads == 1 and cache.computes == 1
    assert cache.stats(TODAY + timedelta(days=1))["today"] == "2026-10-17"
    assert cache.loads == 1 and cache.computes == 2


def test_a_caller_cannot_change_the_cached_answer(tmp_path):
    h = [dic(days_ago(i), 20, app="Mail") for i in range(30)]
    _p, cache = cache_for(tmp_path, h)
    first = cache.stats(TODAY)
    first["heatmap"]["days"][0][0] = 999
    first["top_apps"][0]["count"] = 999
    first["hour_counts"][0] = 999
    first["minutes_saved_basis"]["words"] = 999
    again = cache.stats(TODAY)
    assert again["heatmap"]["days"][0][0] != 999 and again["top_apps"][0]["count"] == 30
    assert again["hour_counts"][0] == 0 and again["minutes_saved_basis"]["words"] == 600


def test_the_typing_speed_is_part_of_the_cache_key(tmp_path):
    speed = {"v": 40}
    h = [dic(days_ago(0), 80)]
    _p, cache = cache_for(tmp_path, h, typing_wpm=lambda: speed["v"])
    assert cache.stats(TODAY)["minutes_saved"] == 1.5
    speed["v"] = 20
    assert cache.stats(TODAY)["minutes_saved"] == round(80 / 20 - 0.5, 1) == 3.5
    assert cache.loads == 1 and cache.computes == 2
    assert cache.stats(TODAY, typing_wpm=40)["minutes_saved"] == 1.5     # an explicit one wins
    # A setting that cannot be read is the default, and never an error.
    def broken():
        raise OSError("settings.json is locked")
    cache._typing_wpm = broken
    assert cache.stats(TODAY)["minutes_saved"] == 1.5


def test_the_quality_log_backfills_speed_and_is_read_again_only_when_it_changes(tmp_path):
    h = [dic(days_ago(i, time="10:00:00"), 30) for i in range(10)]
    q = tmp_path / "quality.jsonl"
    q.write_text("\n".join(json.dumps({"timestamp": x["timestamp"], "speech_s": 15.0}) for x in h) + "\n",
                 encoding="utf-8")
    _p, cache = cache_for(tmp_path, h, quality_path=lambda: q)
    assert cache.stats(TODAY)["wpm"] == 120 and cache.stats(TODAY)["wpm"] == 120
    assert cache.computes == 1
    # A new line in the log is seen on the next call, with no new read of the history.
    with open(q, "a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": "2020-01-01T00:00:00", "speech_s": 5.0}) + "\n")
    assert cache.stats(TODAY)["wpm"] == 120
    assert cache.computes == 2 and cache.loads == 1


def test_a_missing_quality_log_is_no_problem(tmp_path):
    h = [dic(days_ago(i), 30) for i in range(10)]
    _p, cache = cache_for(tmp_path, h, quality_path=lambda: tmp_path / "nothing-here.jsonl")
    assert cache.stats(TODAY)["wpm"] is None
    _p, cache = cache_for(tmp_path, h, quality_path=lambda: None)
    assert cache.stats(TODAY)["wpm"] is None


def test_the_cache_takes_its_day_from_an_injected_clock_in_the_zone_given(tmp_path):
    ny = tz_or_skip("America/New_York")
    h = [dic("2026-03-08T22:00:00", 7)]
    now = {"t": datetime(2026, 3, 9, 3, 30, tzinfo=timezone.utc)}              # still the 8th in New York
    _p, cache = cache_for(tmp_path, h, clock=lambda: now["t"], tz=ny)
    s = cache.stats()
    assert s["today"] == "2026-03-08" and s["today_words"] == 7
    now["t"] = datetime(2026, 3, 9, 4, 30, tzinfo=timezone.utc)                # midnight has passed
    s = cache.stats()
    assert s["today"] == "2026-03-09" and s["today_words"] == 0 and s["words_last_week"] == 7
    assert cache.loads == 1


def test_a_changed_history_is_recounted(tmp_path):
    h = [dic(days_ago(0), 20)]
    p, cache = cache_for(tmp_path, h)
    assert cache.stats(TODAY)["words_this_week"] == 20
    cache.invalidate()
    p.write_text(json.dumps(h + [dic(days_ago(0), 5)]), encoding="utf-8")
    assert cache.stats(TODAY)["words_this_week"] == 25
    assert cache.loads == 2 and cache.computes == 2


# ── how long it takes ────────────────────────────────────────────────────────

def synthetic(n, base=TODAY, days=400):
    apps = ["Mail", "Slack", "Notes", "Chrome", "Code", "Messages", "Pages", ""]
    out = []
    for i in range(n):
        d = base - timedelta(days=(i * days) // n)
        item = {"timestamp": f"{d.isoformat()}T{(i * 7) % 24:02d}:{(i * 13) % 60:02d}:{i % 60:02d}",
                "text": " ".join(["alpha"] * (20 + i % 40)),
                "styled": " ".join(["Alpha"] * (20 + i % 40)), "word_count": 20 + i % 40}
        if i % 3:
            item.update(duration_s=8.0 + i % 20, speech_s=5.0 + i % 15)
        if apps[i % len(apps)]:
            item["app"] = apps[i % len(apps)]
        out.append(item)
    out.sort(key=lambda x: x["timestamp"])
    return out


def best_of(fn, n=3):
    import time
    best = float("inf")
    for _ in range(n):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


def test_5000_entries_are_worked_out_in_under_50_ms():
    h = synthetic(5000)
    assert best_of(lambda: stats(h, speech_log={})) < 0.05


def test_the_numbers_for_the_synthetic_history_are_sane():
    s = stats(synthetic(5000))
    assert s["peak_hour"] is not None and sum(s["hour_counts"]) == 5000
    assert s["wpm"] is not None and 50 < s["wpm"] < 400
    assert s["minutes_saved"] > 0 and len(s["top_apps"]) == 5
    assert s["apps_not_recorded"] > 0


def test_20000_entries_on_disk_are_counted_once_and_a_refresh_does_not_recount(tmp_path):
    import time
    h = synthetic(20000)
    p = tmp_path / "history.json"
    p.write_text(json.dumps(h), encoding="utf-8")
    (tmp_path / "quality.jsonl").write_text(
        "\n".join(json.dumps({"timestamp": x["timestamp"], "speech_s": 6.0}) for x in h[:8000]) + "\n",
        encoding="utf-8")
    cache = jd.HistoryCache(p, lambda: json.loads(p.read_text(encoding="utf-8")),
                            quality_path=lambda: tmp_path / "quality.jsonl")
    t = time.perf_counter()
    first = cache.stats(TODAY)
    cold = time.perf_counter() - t
    assert cold < 2.0                                   # parse, log and count: seconds would be a bug
    assert cache.loads == 1 and cache.computes == 1
    t = time.perf_counter()
    for _ in range(50):
        assert cache.stats(TODAY) == first
    warm = (time.perf_counter() - t) / 50
    assert warm < 0.05                                  # a Journal refresh: a copy, not a recount
    assert cache.loads == 1 and cache.computes == 1
    # The count alone, on a history already in memory.
    assert best_of(lambda: stats(h, speech_log={})) < 0.5
