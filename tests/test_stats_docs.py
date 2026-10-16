"""The roadmap's S9 appendix says what the new stats keys and the typing speed
setting are; this keeps it true to the code (src/stats_insights.py)."""
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import journal_data  # noqa: E402
import stats_insights as si  # noqa: E402

DOC = ROOT / "docs" / "dev-notes" / "specs" / "2026-10-16-polished-dictation-roadmap.md"


def section():
    text = DOC.read_text(encoding="utf-8")
    return text[text.index("### S9 Stats"):]


# The keys compute_stats had before S9 (3.15.1 and the transform and command counts).
OLD_KEYS = {"today_words", "today_count", "week_words", "week_count", "month_words", "month_count",
            "total_words", "total_count", "entries", "streak_days", "transform_count", "command_count",
            "daily_start", "daily_words", "longest_streak_days"}


def test_every_new_stats_key_is_in_the_appendix_and_nothing_old_was_taken_away():
    keys = set(journal_data.compute_stats([], date(2026, 10, 16)))
    assert OLD_KEYS <= keys
    doc = section()
    spans = set(re.findall(r"`([a-z_]+)`", doc))
    missing = sorted(k for k in keys - OLD_KEYS if k not in spans)
    assert not missing, missing


def test_the_appendix_gives_the_setting_and_the_numbers_the_code_uses():
    doc = section()
    assert "`typing_wpm`" in doc and "from 10 to 200" in doc and "40 until set" in doc
    assert (si.TYPING_WPM_MIN, si.TYPING_WPM_MAX, si.TYPING_WPM_DEFAULT) == (10, 200, 40)
    assert "TYPING_SPEED_UNKNOWN" in doc
    assert "until 20 dictations" in doc and si.PEAK_HOUR_MIN_ENTRIES == 20
    assert "latest 100" in doc and si.WPM_WINDOW == 100
    assert "`None` until 10" in doc and si.WPM_MIN_SAMPLES == 10
    assert "weeks: 26" in doc and si.HEATMAP_WEEKS == 26
    assert "300 words a minute" in doc and si.FASTEST_SPEECH_WPM == 300
    assert "150 words a minute" in doc and si.SPEAKING_WPM == 150
    assert "five apps" in doc and si.TOP_APPS == 5
    assert "3 words or more, 1 s or more, 20 to 400 a minute" in doc
    assert (si.WPM_MIN_WORDS, si.WPM_MIN_SPEECH_S, si.WPM_PLAUSIBLE) == (3, 1.0, (20.0, 400.0))


def test_the_appendix_names_the_helpers_and_adds_no_data_file():
    doc = section()
    for name in ("local_today", "typing_wpm_of", "_typing_wpm_setting", "HistoryCache", "weekView", "heatmapView",
                 "heatMove", "typingSpeedCheck", "minutesSavedView"):
        assert name in doc, name
    assert "No new data file, nav page, network call or log line." in doc
    for name in ("local_today", "typing_wpm_of", "clean_typing_wpm", "read_speech_log", "change_pct"):
        assert callable(getattr(si, name)), name
    assert "—" not in doc and "–" not in doc


def test_the_privacy_lists_already_cover_the_files_the_stats_read():
    import privacy_data
    assert "history.json" in privacy_data.DATA_FILES and "quality.jsonl" in privacy_data.DATA_FILES
