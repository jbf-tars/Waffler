"""Settings, Privacy and data (3.15), and the Journal's clean-up pause.

- Recent recordings (owner decision D9): Waffler keeps the last 10
  dictations' audio, as it did, but Settings says so, with a switch to stop
  and "Delete now" (src/recent_audio.py).
- When every clean-up provider is at its limit, the Journal says "Clean-up
  is paused until 14:32" at the top and tags the entry "As said: limit
  reached" (src/cleanup_pause.py).

Offline: the pipeline runs with fakes (tests/_pipeline_harness.py).
"""
import json
import os
import time
from datetime import datetime

import pytest

from _pipeline_harness import FakeStyler, fast_limits, history, make_pipeline, run_process
import cleanup_pause
import recent_audio


# ── recent recordings ────────────────────────────────────────────────────────

def _keep_n(tmp_path, n, settings=None):
    for i in range(n):
        p = recent_audio.keep(tmp_path, b"RIFF" + bytes([i]), settings,
                              now=datetime(2026, 9, 25, 16, 0, i))
        if p is not None:
            t = time.time() - 100 + i          # distinct times, oldest first
            os.utime(p, (t, t))


def test_the_last_ten_are_kept_and_older_ones_go(tmp_path):
    _keep_n(tmp_path, 13)
    kept = recent_audio.files(tmp_path)
    assert len(kept) == 10
    assert kept[0].name == "rec-20260925-160003.wav" and kept[-1].name == "rec-20260925-160012.wav"
    assert recent_audio.summary(tmp_path, {}) == {"enabled": True, "count": 10, "keep": 10}


def test_switched_off_nothing_new_is_kept(tmp_path):
    off = {recent_audio.SETTING: False}
    assert recent_audio.keep(tmp_path, b"RIFF", off) is None
    assert recent_audio.files(tmp_path) == []
    assert recent_audio.summary(tmp_path, off)["enabled"] is False
    # Only an explicit False switches it off.
    assert recent_audio.enabled({}) and recent_audio.enabled(None)
    assert recent_audio.enabled({recent_audio.SETTING: True})


def test_delete_now_removes_every_kept_recording_and_nothing_else(tmp_path):
    _keep_n(tmp_path, 4)
    other = recent_audio.folder(tmp_path) / "notes.txt"
    other.write_text("keep me", encoding="utf-8")
    assert recent_audio.delete_all(tmp_path) == 4
    assert recent_audio.files(tmp_path) == [] and other.exists()
    assert recent_audio.delete_all(tmp_path / "nowhere") == 0


# ── the clean-up pause ───────────────────────────────────────────────────────

NOW = datetime(2026, 9, 25, 14, 12, 0)


def test_a_limit_with_a_wait_pauses_until_that_time():
    p = cleanup_pause.from_reason("RATE_LIMIT|tokens per day (TPD)|20m0s|Groq: Rate limit reached", NOW)
    assert p == {"provider": "Groq", "until": "2026-09-25T14:32:00", "daily": True}
    v = cleanup_pause.view(p, NOW)
    assert v["title"] == "Clean-up is paused until 14:32"
    assert v["detail"] == ("Groq says you've reached your limit for now. Dictation still works: "
                           "you get your words as you said them.")
    assert v["seconds_left"] == 1200
    # Over once the time has passed.
    assert cleanup_pause.view(p, datetime(2026, 9, 25, 14, 32, 0)) is None


def test_the_styler_s_own_cooldown_names_the_provider():
    p = cleanup_pause.from_reason("RATE_LIMIT|cooldown|95s|Groq: Groq still in cooldown from previous limit", NOW)
    assert p["provider"] == "Groq" and p["until"] == "2026-09-25T14:13:35"


def test_a_daily_limit_with_no_wait_lasts_until_midnight():
    p = cleanup_pause.from_reason("RATE_LIMIT|requests per day (RPD)||Groq: limit", NOW)
    assert p["until"] == "2026-09-26T00:00:00"
    assert cleanup_pause.view(p, NOW)["title"] == "Clean-up is paused until tomorrow"


def test_other_reasons_are_not_a_pause():
    for reason in ("", None, "Groq: CONNECTION: Groq connection failed",
                   "TIMEOUT|clean-up took longer than 20s - pasted raw", "AUTH: Groq 401"):
        assert cleanup_pause.from_reason(reason, NOW) is None
    assert cleanup_pause.view(None, NOW) is None
    assert cleanup_pause.view({"until": "not a time"}, NOW) is None


# ── in the pipeline ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=5.0, TRANSCRIBE_DEADLINE_BASE_S=5.0,
                TRANSCRIBE_DEADLINE_MAX_S=5.0, STYLE_DEADLINE_S=5.0)


def _dictate(p):
    worker = run_process(p)
    worker.join(5)
    assert not worker.is_alive()
    return history(p)[-1]


def test_a_limited_clean_up_tags_the_entry_and_tells_the_journal(tmp_path):
    reason = "RATE_LIMIT|tokens per day (TPD)|16m12s|Groq: Rate limit reached for model"
    p = make_pipeline(tmp_path, styler=FakeStyler(fallback_reason=reason))
    entry = _dictate(p)
    assert entry["as_said"] == cleanup_pause.LIMIT_TAG
    [pause] = p.page.pauses
    assert pause["provider"] == "Groq" and pause["daily"] is True


def test_a_clean_up_that_worked_or_failed_otherwise_is_not_tagged(tmp_path):
    p = make_pipeline(tmp_path)
    assert "as_said" not in _dictate(p)
    p = make_pipeline(tmp_path / "b", styler=FakeStyler(fallback_reason="Groq: CONNECTION: failed"))
    assert "as_said" not in _dictate(p)
    assert p.page.pauses == []


def test_the_switch_decides_whether_the_dictation_audio_is_kept(tmp_path):
    p = make_pipeline(tmp_path)
    _dictate(p)
    assert len(recent_audio.files(tmp_path)) == 1
    (tmp_path / "settings.json").write_text(json.dumps({recent_audio.SETTING: False}), encoding="utf-8")
    recent_audio.delete_all(tmp_path)
    _dictate(p)
    assert recent_audio.files(tmp_path) == []
