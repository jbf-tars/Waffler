"""Settings, Privacy and data (3.15, plan SR9; owner decisions D5 and D9).

- History retention: keep everything unless the user chooses 30, 90 or 365
  days. Not sent recordings are never pruned by it.
- "Delete all my data": history, usage, recordings (recent and not sent) and
  the logs go; keys, the words list and settings stay.
- The transcript lines that versions before the redaction wrote to app.log
  are removed once on the first start of 3.15, and a large log is rotated.

Offline, in temporary folders: no real data, no network.
"""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from _pipeline_harness import fast_limits, history, make_pipeline, run_process
import privacy_data as P

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 26, 12, 0, 0)


def _entry(days_ago, **extra):
    t = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {"timestamp": t, "text": f"said {days_ago}", "styled": f"wrote {days_ago}", **extra}


# ── history retention ────────────────────────────────────────────────────────

def test_the_default_keeps_everything_and_a_stray_value_deletes_nothing():
    assert P.history_keep_days(None) == 0
    assert P.history_keep_days({}) == 0
    for bad in (7, -1, "90x", None, 3.5, "abc"):
        assert P.history_keep_days({P.HISTORY_SETTING: bad}) == 0, bad
    assert P.history_keep_days({P.HISTORY_SETTING: 90}) == 90
    assert P.history_keep_days({P.HISTORY_SETTING: "30"}) == 30
    old = [_entry(1000), _entry(5)]
    assert P.prune_history(old, 0, NOW) == (old, 0)
    assert P.prune_history(old, 7, NOW) == (old, 0)     # not a choice: keep all


def test_retention_removes_older_dictations_but_never_not_sent_ones():
    h = [_entry(400), _entry(100), _entry(95, failed=True, unsent_id="x"),
         {"timestamp": "not a time", "text": "?"}, "not a dict", _entry(10), _entry(0)]
    kept, removed = P.prune_history(h, 90, NOW)
    assert removed == 2
    assert [e.get("text") if isinstance(e, dict) else e for e in kept] == [
        "said 95", "?", "not a dict", "said 10", "said 0"]
    assert P.count_older(h, 365, NOW) == 1
    assert P.count_older(h, 30, NOW) == 2
    # Exactly on the edge stays.
    assert P.prune_history([_entry(30)], 30, NOW)[1] == 0


@pytest.fixture
def _limits(monkeypatch):
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=5.0, TRANSCRIBE_DEADLINE_BASE_S=5.0,
                TRANSCRIBE_DEADLINE_MAX_S=5.0, STYLE_DEADLINE_S=5.0)


def _dictate(p):
    w = run_process(p)
    w.join(5)
    assert not w.is_alive()


def test_a_dictation_applies_the_chosen_retention_once_a_day(tmp_path, _limits):
    now = datetime.now()
    old = {"timestamp": (now - timedelta(days=40)).isoformat(timespec="seconds"), "text": "old"}
    waiting = {"timestamp": (now - timedelta(days=40)).isoformat(timespec="seconds"),
               "text": "", "failed": True}
    recent = {"timestamp": (now - timedelta(days=2)).isoformat(timespec="seconds"), "text": "recent"}
    (tmp_path / "history.json").write_text(json.dumps([old, waiting, recent]), encoding="utf-8")

    p = make_pipeline(tmp_path)
    _dictate(p)                                   # no choice made: nothing goes
    assert len(history(p)) == 4

    (tmp_path / "settings.json").write_text(json.dumps({P.HISTORY_SETTING: 30}), encoding="utf-8")
    p.ns["_history_retention_day"] = None         # a new day
    _dictate(p)
    texts = [e.get("text") for e in history(p)]
    assert "old" not in texts and "recent" in texts and len(history(p)) == 4
    assert any(e.get("failed") for e in history(p))
    assert any("older than 30 days removed" in m for m in p.logged)


# ── delete all my data ───────────────────────────────────────────────────────

def _fill(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    for name in ("history.json", "usage.json"):
        (d / name).write_text('[{"x": 1}]', encoding="utf-8")
    for name in ("quality.jsonl", "app.log", "app.log.1", "crash.log", "hotkey.log",
                 "usage.backup-20260909-144603.json"):
        (d / name).write_text("data\n", encoding="utf-8")
    for name in ("debug_audio", "unsent"):
        (d / name).mkdir()
        (d / name / "a.wav").write_bytes(b"RIFF")
    kept = {".env": "GROQ_API_KEY=gsk_test", "settings.json": '{"theme": "dark"}',
            "config.json": "{}", "setup_complete.json": "{}", "vocab.json": '["Isobel"]'}
    for name, text in kept.items():
        (d / name).write_text(text, encoding="utf-8")
    return kept


def test_delete_all_my_data_keeps_keys_words_and_settings(tmp_path):
    kept = _fill(tmp_path)
    assert P.delete_my_data(tmp_path) == {"ok": True, "failed": []}
    for name, text in kept.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == text, name
    assert json.loads((tmp_path / "history.json").read_text(encoding="utf-8")) == []
    assert json.loads((tmp_path / "usage.json").read_text(encoding="utf-8")) == []
    for gone in ("quality.jsonl", "app.log", "app.log.1", "crash.log", "hotkey.log",
                 "usage.backup-20260909-144603.json", "debug_audio", "unsent"):
        assert not (tmp_path / gone).exists(), gone
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(list(kept) + ["history.json", "usage.json"])
    # Nothing there is fine too.
    assert P.delete_my_data(tmp_path / "nowhere")["ok"] is True


def test_a_log_held_open_is_emptied_instead(tmp_path, monkeypatch):
    _fill(tmp_path)
    real_unlink = Path.unlink

    def locked(self, *a, **k):
        if self.name == "crash.log":
            raise PermissionError("[WinError 32] in use")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked)
    assert P.delete_my_data(tmp_path)["ok"] is True
    assert (tmp_path / "crash.log").read_text(encoding="utf-8") == ""


# ── old transcript lines in app.log ──────────────────────────────────────────

OLD_LOG = [
    "09:00:00  === Waffler starting === (v3.14.10, PROJECT_ROOT=x)",
    "09:00:05  Recording started",
    "09:00:08  Done: send it to John by Wednesday at three",
    "09:00:09  Wizard transcription: testing one two three",
    "09:00:10  Done: first line of a note",
    "second line of the note",
    "09:00:11  [pipeline] TOTAL: 1800ms",
    "09:00:12  Done: 12 words, 64 chars",
    "09:00:13  Wizard transcription: 31 chars",
    "09:00:14  Done: " + "x" * 60,
    "tail of it",
    "Traceback (most recent call last):",
    '  File "app.py", line 1',
    "09:00:15  [whisper] Done: nothing here to remove",
    "09:00:16  Styled text: opted in by the user",
]


def test_only_the_old_transcript_lines_go():
    kept, removed = P.scrub_transcript_lines(OLD_LOG)
    assert removed == 6
    assert kept == [
        "09:00:00  === Waffler starting === (v3.14.10, PROJECT_ROOT=x)",
        "09:00:05  Recording started",
        "09:00:11  [pipeline] TOTAL: 1800ms",
        "09:00:12  Done: 12 words, 64 chars",
        "09:00:13  Wizard transcription: 31 chars",
        "Traceback (most recent call last):",
        '  File "app.py", line 1',
        "09:00:15  [whisper] Done: nothing here to remove",
        "09:00:16  Styled text: opted in by the user",
    ]
    assert P.scrub_transcript_lines(kept) == (kept, 0)


def test_windows_line_endings_do_not_hide_todays_lines():
    # app.log is written in text mode, so on Windows every line ends "\r\n".
    crlf = [line + "\r" for line in OLD_LOG]
    kept, removed = P.scrub_transcript_lines(crlf)
    assert removed == 6
    assert "09:00:12  Done: 12 words, 64 chars\r" in kept
    assert "09:00:13  Wizard transcription: 31 chars\r" in kept


def test_the_file_is_rewritten_once_and_everything_else_is_byte_for_byte(tmp_path):
    log = tmp_path / "app.log"
    other = "09:00:20  café → ok\r\n".encode("utf-8") + b"09:00:21  bad byte \xff\r\n"
    log.write_bytes(("\r\n".join(OLD_LOG) + "\r\n").encode("utf-8") + other)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")

    r = P.tidy_logs_at_start(tmp_path, settings)
    assert r == {"scrubbed": 6, "rotated": False}
    data = log.read_bytes()
    assert data.endswith(other)
    assert b"John" not in data and b"testing one two" not in data and b"note" not in data
    assert b"Done: 12 words, 64 chars\r\n" in data
    assert json.loads(settings.read_text(encoding="utf-8")) == {
        "theme": "dark", P.LOG_SCRUB_SETTING: True}

    # Marked done: a later old-looking line is not looked for again.
    with open(log, "ab") as f:
        f.write(b"09:00:30  Done: opted-in style line\r\n")
    assert P.tidy_logs_at_start(tmp_path, settings)["scrubbed"] is None
    assert log.read_bytes().endswith(b"opted-in style line\r\n")


def test_an_unreadable_settings_file_is_left_alone_and_nothing_is_scrubbed(tmp_path):
    (tmp_path / "app.log").write_text("\n".join(OLD_LOG) + "\n", encoding="utf-8")
    settings = tmp_path / "settings.json"
    settings.write_text("{not json", encoding="utf-8")
    assert P.tidy_logs_at_start(tmp_path, settings)["scrubbed"] is None
    assert settings.read_text(encoding="utf-8") == "{not json"
    assert "John" in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_a_log_with_nothing_old_is_not_rewritten(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("09:00:12  Done: 12 words, 64 chars\n", encoding="utf-8")
    before = log.stat().st_mtime_ns
    os.utime(log, ns=(before - 10_000_000_000, before - 10_000_000_000))
    stamp = log.stat().st_mtime_ns
    assert P.scrub_log_file(log) == 0
    assert log.stat().st_mtime_ns == stamp
    assert P.scrub_log_file(tmp_path / "missing.log") == 0


def test_a_big_log_is_rotated(tmp_path):
    log = tmp_path / "app.log"
    log.write_bytes(b"a" * 50)
    assert P.rotate_log(log, max_bytes=100) is False
    (tmp_path / "app.log.1").write_bytes(b"older")
    log.write_bytes(b"b" * 150)
    assert P.rotate_log(log, max_bytes=100) is True
    assert not log.exists() and (tmp_path / "app.log.1").read_bytes() == b"b" * 150
    assert P.rotate_log(tmp_path / "none.log") is False


# ── wiring ───────────────────────────────────────────────────────────────────

def _app():
    return (ROOT / "app.py").read_text(encoding="utf-8")


def test_start_up_tidies_the_log_before_the_banner_and_applies_retention():
    main = _app()[_app().index("def main():"):]
    tidy = main.index("_privacy.tidy_logs_at_start(DATA_DIR")
    assert main.index("if not _acquire_lock():") < tidy < main.index("=== Waffler starting ===")
    assert main.index("_retain_history(_h, force=True)") < main.index("=== Waffler starting ===")
    # Counts only: never the removed text.
    line = main[tidy:main.index("_retain_history(_h, force=True)")]
    assert "_tidy['scrubbed']" in line and "lines" not in line.replace("line(s)", "")
