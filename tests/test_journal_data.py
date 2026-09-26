"""The Journal's counts and pages (src/journal_data.py), offline.

SR4: the window drew every entry and rescanned history.json on every
dictation and search. These check the counts it now gets once per change,
and the pages it draws 50 at a time.
"""
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import journal_data as jd  # noqa: E402

TODAY = date(2026, 9, 25)          # a Friday; the week began Monday 21st


def e(ts, text="one two three", failed=False, styled=None):
    item = {"timestamp": ts, "text": text}
    if styled is not None:
        item["styled"] = styled
    if failed:
        item.update(failed=True, styled="Not sent: this recording hasn't been turned into text yet.")
    return item


HISTORY = [                         # oldest first, as history.json is
    e("2026-08-30T10:00:00", "last month words here"),        # 4 words, last month
    e("2026-09-20T09:00:00", "a b"),                          # Sunday: this month, last week
    e("2026-09-21T09:00:00", "monday words"),                 # Monday: this week
    e("2026-09-24T18:00:00", "yesterday", styled="Yesterday, cleaned."),
    e("2026-09-25T08:00:00", "", failed=True),                # Not sent today
    e("2026-09-25T09:30:00", "hello there world"),
    e("2026-09-25T16:31:00", "invoice for august please"),
]


def test_counts_for_today_week_month_and_all_time():
    s = jd.compute_stats(HISTORY, TODAY)
    assert s["today_words"] == 3 + 4
    assert s["today_count"] == 3            # every entry made today, as before
    assert (s["week_count"], s["week_words"]) == (4, 2 + 2 + 3 + 4)
    assert (s["month_count"], s["month_words"]) == (5, 2 + 2 + 2 + 3 + 4)
    assert (s["total_count"], s["total_words"]) == (6, 4 + 2 + 2 + 2 + 3 + 4)
    assert s["entries"] == 7


def test_a_not_sent_note_adds_no_words():
    s = jd.compute_stats([e("2026-09-25T08:00:00", "", failed=True)], TODAY)
    assert s["today_words"] == 0 and s["total_words"] == 0 and s["total_count"] == 0


def test_the_streak_counts_back_from_today_or_yesterday():
    assert jd.compute_stats(HISTORY, TODAY)["streak_days"] == 2           # 24th and 25th
    # Nothing yet today: yesterday still anchors the streak.
    assert jd.compute_stats(HISTORY[:4], TODAY)["streak_days"] == 1
    assert jd.compute_stats(HISTORY[:3], TODAY)["streak_days"] == 0
    assert jd.compute_stats([], TODAY)["streak_days"] == 0


def test_the_counts_match_what_get_stats_returned_before():
    """app.py get_stats() worked these out inline until 3.15; the same
    history must give the same four numbers."""
    old_today = [h for h in HISTORY if h["timestamp"].startswith("2026-09-25")]
    s = jd.compute_stats(HISTORY, TODAY)
    assert s["today_count"] == len(old_today)
    assert s["today_words"] == sum(len((h.get("styled") or h.get("text") or "").split())
                                   for h in old_today if not h.get("failed"))
    assert s["total_words"] == sum(len((h.get("styled") or h.get("text") or "").split())
                                   for h in HISTORY if not h.get("failed"))


def test_pages_are_newest_first_and_join_up():
    first = jd.page(HISTORY, limit=3)
    rest = jd.page(HISTORY, limit=3, offset=3)
    last = jd.page(HISTORY, limit=3, offset=6)
    assert [x["timestamp"] for x in first + rest + last] == [h["timestamp"] for h in reversed(HISTORY)]
    assert len(last) == 1
    assert jd.page(HISTORY) == list(reversed(HISTORY))
    assert jd.page(HISTORY, limit=0) == []
    assert jd.page(HISTORY, limit=5, offset=99) == []


def test_a_search_pages_through_matches_only():
    assert [x["timestamp"] for x in jd.page(HISTORY, query="  INVOICE ")] == ["2026-09-25T16:31:00"]
    # The clean text is searched too, not only the transcript.
    assert [x["timestamp"] for x in jd.page(HISTORY, query="cleaned")] == ["2026-09-24T18:00:00"]
    words = jd.page(HISTORY, query="words")
    assert [x["timestamp"] for x in words] == ["2026-09-21T09:00:00", "2026-08-30T10:00:00"]
    assert jd.page(HISTORY, limit=1, offset=1, query="words") == [words[1]]
    assert jd.page(HISTORY, query="quarterly budget") == []


def test_bad_limits_and_offsets_do_not_raise():
    assert len(jd.page(HISTORY, limit="2", offset="1")) == 2
    assert jd.page(HISTORY, limit="x", offset=None) == list(reversed(HISTORY))
    assert jd.page(HISTORY, offset=-4, limit=1) == [HISTORY[-1]]


def test_the_cache_reads_the_file_once_until_it_changes(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps(HISTORY), encoding="utf-8")
    cache = jd.HistoryCache(path, lambda: json.loads(path.read_text(encoding="utf-8")))
    for _ in range(5):
        assert len(cache.items()) == 7
        assert cache.stats(TODAY)["entries"] == 7
    assert cache.loads == 1

    # A dictation replaces the file (as write_json_atomic does).
    tmp = tmp_path / "history.tmp"
    tmp.write_text(json.dumps(HISTORY + [e("2026-09-25T17:00:00", "new one")]), encoding="utf-8")
    later = time.time() + 5
    os.utime(tmp, (later, later))
    os.replace(tmp, path)
    assert len(cache.items()) == 8
    s = cache.stats(TODAY)
    assert s["entries"] == 8 and s["today_words"] == 3 + 4 + 2
    assert cache.loads == 2

    # The stats are worked out again when the day changes, with no new read.
    assert cache.stats(date(2026, 9, 26))["today_count"] == 0
    assert cache.loads == 2


def test_the_cache_is_empty_without_a_file(tmp_path):
    cache = jd.HistoryCache(tmp_path / "missing.json", lambda: [{"never": "read"}])
    assert cache.items() == [] and cache.stats(TODAY)["entries"] == 0
