"""The numbers beside the totals: week streak, this week against last, busiest
hour, an activity map, minutes saved, speaking speed and top apps.

Everything is worked out on this computer from history.json (and, for the
speed of older entries, quality.jsonl), as ``journal_data.compute_stats``
walks the Journal once: it hands each dictation to ``Insights.add`` and merges
``Insights.result`` into its answer. Nothing here touches the network, the
window or a file other than the log ``read_speech_log`` is asked to read.

Which entries count. A dictation that was sent: not a Not sent note (it holds
no words yet) and not a rewrite of selected text or a spoken command
(``kind`` "transform" or "command", which are in the Journal and in no number
built from what was dictated). The Journal's own day, week and month counts
leave out the same entries.

Time. History stamps are local wall-clock times ("2026-10-16T09:30:00"), so a
dictation's day and hour are read straight from its stamp, and "today" is the
date where the person is (``local_today``). Every step between days is a step
on the calendar (``date`` plus ``timedelta(days=n)``), never a count of 24
hours, so a day of 23 or 25 hours when the clocks change is still one day and
a week is still seven of them. Weeks start on Monday.

What each number means
----------------------
``week_streak``      consecutive calendar weeks (Monday to Sunday) with at least
                     one dictation, ending this week. While this week has none
                     yet, last week anchors it, as yesterday anchors the day
                     streak, so it does not drop to 0 on Monday morning.
``words_this_week``  Monday to today. ``words_last_week`` is the whole week
                     before. ``week_change_pct`` compares this week so far with
                     the same days last week (``words_last_week_same_days``),
                     so a Monday is not called a collapse for not having a
                     whole week of words. None when those days had no words.
``peak_hour``        the local hour (0 to 23) with the most dictations, once
                     there are ``PEAK_HOUR_MIN_ENTRIES`` dictations with a time;
                     ``hour_counts`` is every hour's count.
``heatmap``          the last 26 weeks as 26 lists of 7 (Monday first) with the
                     words of each day; days after today are None.
``minutes_saved``    an estimate. The words, typed at ``typing_wpm`` (a
                     setting), take ``typing_minutes``; speaking them took
                     ``speaking_minutes`` (the recording's length, or 150 words
                     a minute for an entry that did not note one). The
                     difference over all dictations, never below zero. It
                     ignores the seconds Waffler takes to answer and the time
                     typing would spend on corrections, so it is a guide.
``wpm``              words per minute of speech: the words said over the seconds
                     of speech, for the latest ``WPM_WINDOW`` dictations that
                     have a usable measurement (``speech_s``, or the quality
                     log's for entries from before it was kept here). None until
                     ``WPM_MIN_SAMPLES`` have one.
``top_apps``         the five apps with the most dictations, from ``app`` (or
                     ``app_bundle`` when that is all there is); the dictations
                     with neither are counted in ``apps_not_recorded``, so older
                     history is not read as if it had said something.
"""

from __future__ import annotations

import collections
import json
import math
from datetime import date, datetime, timedelta

try:
    import user_messages
except ImportError:  # imported as src.stats_insights
    from src import user_messages


# ── the typing speed setting (settings.json "typing_wpm") ─────────────────────
TYPING_WPM_DEFAULT = 40
TYPING_WPM_MIN = 10
TYPING_WPM_MAX = 200

# Talking, for an entry that did not note how long its recording was, and the
# fastest that counts when it did (a recording is never an instant).
SPEAKING_WPM = 150
FASTEST_SPEECH_WPM = 300

PEAK_HOUR_MIN_ENTRIES = 20
HEATMAP_WEEKS = 26
TOP_APPS = 5

WPM_WINDOW = 100
WPM_MIN_SAMPLES = 10
WPM_MIN_WORDS = 3
WPM_MIN_SPEECH_S = 1.0
# A reading outside this is a measurement or a word count that went wrong (a
# language written without spaces, a clip that was mostly silence).
WPM_PLAUSIBLE = (20.0, 400.0)


def clean_typing_wpm(value):
    """``value`` as a whole number of words a minute, or None when it is not
    one in range. A float that is a whole number (55.0) is accepted."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        value = int(value)
    return value if TYPING_WPM_MIN <= value <= TYPING_WPM_MAX else None


def typing_wpm_problem(value):
    """A plain sentence when ``value`` cannot be the typing speed, else None."""
    return None if clean_typing_wpm(value) is not None else user_messages.TYPING_SPEED_UNKNOWN


def typing_wpm_of(stored) -> int:
    """The typing speed in settings.json; the default when it is missing or
    unreadable."""
    value = stored.get("typing_wpm") if isinstance(stored, dict) else None
    clean = clean_typing_wpm(value)
    return TYPING_WPM_DEFAULT if clean is None else clean


def settings_problem(payload):
    """A plain sentence when ``payload`` (the dict a save is called with)
    holds a typing speed that is refused, else None. A refused value saves
    none of the payload."""
    if isinstance(payload, dict) and "typing_wpm" in payload:
        return typing_wpm_problem(payload["typing_wpm"])
    return None


# ── dates ─────────────────────────────────────────────────────────────────────

def local_today(now=None, tz=None) -> date:
    """The calendar day where the person is. ``now`` is the clock (a datetime,
    for tests; the current time when None); an aware one is read in ``tz``
    (the computer's own zone when None), a plain one is already local."""
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is not None:
        now = now.astimezone(tz)
    return now.date()


def week_of(d: date) -> int:
    """A number for the Monday-to-Sunday week ``d`` is in: the same for the
    seven days, one more for the next week. (Ordinal 1, 1 January of year 1,
    was a Monday.)"""
    return (d.toordinal() - 1) // 7


def change_pct(this, base):
    """The change from ``base`` to ``this`` as a whole percent, half away from
    zero; None when there is nothing to compare with."""
    if not base or base < 0:
        return None
    pct = (this - base) * 100.0 / base
    return int(math.copysign(math.floor(abs(pct) + 0.5), pct))


# ── readers ───────────────────────────────────────────────────────────────────

def _seconds(value):
    """``value`` as a positive finite number of seconds, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 and math.isfinite(value) else None


def _hour(ts):
    """The hour (0 to 23) of a stamp like "2026-10-16T09:30:00", else None."""
    s = ts if isinstance(ts, str) else ""
    if len(s) >= 13 and s[10] in "T " and s[11:13].isdigit():
        h = int(s[11:13])
        if h < 24:
            return h
    return None


def _app_name(item):
    """The app an entry was dictated in: its name, else its identifier, else
    ""."""
    for key in ("app", "app_bundle"):
        v = item.get(key)
        if isinstance(v, str):
            shown = " ".join(v.split())
            if shown:
                return shown
    return ""


def read_speech_log(path) -> dict:
    """{timestamp: seconds of speech} from quality.jsonl, for the entries from
    before the Journal noted ``speech_s``. The log holds metadata only, never
    words. A line that is not usable is skipped; no file is no entries."""
    out = {}
    if not path:
        return out
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts, secs = row.get("timestamp"), _seconds(row.get("speech_s"))
                if isinstance(ts, str) and ts and secs is not None:
                    out[ts] = secs
    except OSError:
        return {}
    return out


# ── the walk ──────────────────────────────────────────────────────────────────

class Insights:
    """Collects the numbers above while ``compute_stats`` walks the history.

    ``today`` is the date where the person is; ``typing_wpm`` the setting
    (anything out of range is the default); ``speech_log`` is {timestamp:
    seconds} from the quality log, or a function that returns it (called once,
    and only if an entry needs it), or None.
    """

    def __init__(self, today: date, typing_wpm=TYPING_WPM_DEFAULT, speech_log=None):
        self.today = today
        self.typing_wpm = clean_typing_wpm(typing_wpm) or TYPING_WPM_DEFAULT
        self._speech_log = speech_log
        self._monday = today - timedelta(days=today.weekday())
        self._first = self._monday - timedelta(weeks=HEATMAP_WEEKS - 1)
        self.weeks = set()
        self.by_day = {}
        self.hour_counts = [0] * 24
        self.apps = {}                  # key -> [shown name, dictations, words]
        self.apps_not_recorded = 0
        self.typing_min = 0.0
        self.speaking_min = 0.0
        self.basis_words = 0
        self.timed = 0
        self.estimated = 0
        self.samples = collections.deque(maxlen=WPM_WINDOW)     # (words said, seconds)

    def _logged(self, ts):
        log = self._speech_log
        if callable(log):
            log = self._speech_log = log() or {}
        return log.get(ts) if log and isinstance(ts, str) else None

    def add(self, item: dict, day, words: int):
        """One dictation that was sent. ``day`` is its date (None when its
        stamp cannot be read) and ``words`` its words, as the Journal counts
        them."""
        if day is not None:
            self.weeks.add(week_of(day))
            if self._first <= day <= self.today:
                self.by_day[day] = self.by_day.get(day, 0) + words
        hour = _hour(item.get("timestamp"))
        if hour is not None:
            self.hour_counts[hour] += 1

        name = _app_name(item)
        if name:
            row = self.apps.setdefault(name.casefold(), [name, 0, 0])
            row[0] = name               # the newest spelling
            row[1] += 1
            row[2] += words
        else:
            self.apps_not_recorded += 1

        if words <= 0:
            return
        secs = _seconds(item.get("duration_s"))
        if secs is None:
            speaking = words / SPEAKING_WPM
            self.estimated += 1
        else:
            speaking = max(secs / 60.0, words / FASTEST_SPEECH_WPM)
            self.timed += 1
        self.typing_min += words / self.typing_wpm
        self.speaking_min += speaking
        self.basis_words += words

        speech = _seconds(item.get("speech_s"))
        if speech is None:
            speech = self._logged(item.get("timestamp"))
        if speech is not None and speech >= WPM_MIN_SPEECH_S:
            # What was said: the heard words, not a snippet's expansion.
            heard = item.get("text")
            said = len(heard.split()) if isinstance(heard, str) and heard.strip() else words
            if said >= WPM_MIN_WORDS:
                rate = said * 60.0 / speech
                if WPM_PLAUSIBLE[0] <= rate <= WPM_PLAUSIBLE[1]:
                    self.samples.append((said, speech))

    # ── the answer ────────────────────────────────────────────────────────────

    def _words_between(self, first: date, n_days: int) -> int:
        return sum(self.by_day.get(first + timedelta(days=i), 0) for i in range(n_days))

    def _week_streak(self) -> int:
        week = week_of(self.today)
        cursor = week if week in self.weeks else week - 1
        n = 0
        while cursor in self.weeks:
            n += 1
            cursor -= 1
        return n

    def _heatmap(self) -> dict:
        weeks = []
        for w in range(HEATMAP_WEEKS):
            row = []
            for i in range(7):
                d = self._first + timedelta(days=7 * w + i)
                row.append(None if d > self.today else self.by_day.get(d, 0))
            weeks.append(row)
        return {"start": self._first.isoformat(), "weeks": HEATMAP_WEEKS, "days": weeks,
                "max": max((v for row in weeks for v in row if v), default=0)}

    def result(self) -> dict:
        today = self.today
        n_days = today.weekday() + 1
        this_week = self._words_between(self._monday, n_days)
        last_monday = self._monday - timedelta(days=7)
        same_days = self._words_between(last_monday, n_days)

        timed_hours = sum(self.hour_counts)
        peak = None
        if timed_hours >= PEAK_HOUR_MIN_ENTRIES:
            peak = max(range(24), key=lambda h: (self.hour_counts[h], -h))

        samples = list(self.samples)
        wpm = None
        if len(samples) >= WPM_MIN_SAMPLES:
            minutes = sum(s for _w, s in samples) / 60.0
            wpm = int(math.floor(sum(w for w, _s in samples) / minutes + 0.5))

        ranked = sorted(self.apps.values(), key=lambda r: (-r[1], -r[2], r[0].casefold()))

        return {
            "today": today.isoformat(),
            "weekday": today.weekday(),
            "week_streak": self._week_streak(),
            "words_this_week": this_week,
            "words_last_week": self._words_between(last_monday, 7),
            "words_last_week_same_days": same_days,
            "week_change_pct": change_pct(this_week, same_days),
            "peak_hour": peak,
            "hour_counts": list(self.hour_counts),
            "peak_hour_needs": PEAK_HOUR_MIN_ENTRIES,
            "heatmap": self._heatmap(),
            "minutes_saved": round(max(0.0, self.typing_min - self.speaking_min), 1),
            "minutes_saved_basis": {
                "typing_wpm": self.typing_wpm,
                "words": self.basis_words,
                "typing_minutes": round(self.typing_min, 1),
                "speaking_minutes": round(self.speaking_min, 1),
                "timed": self.timed,
                "estimated": self.estimated,
            },
            "wpm": wpm,
            "wpm_samples": len(samples),
            "top_apps": [{"name": r[0], "count": r[1], "words": r[2]} for r in ranked[:TOP_APPS]],
            "apps_recorded": sum(r[1] for r in ranked),
            "apps_not_recorded": self.apps_not_recorded,
            "apps_total": len(ranked),
        }
