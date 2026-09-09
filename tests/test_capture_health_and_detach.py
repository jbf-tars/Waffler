#!/usr/bin/env python3
"""Two silent failures made visible.

1. Capture problems left no evidence. PortAudio signals dropped input via the
   callback's `status`, and the callback swallowed every exception so it could
   never crash the audio thread. Both were discarded, so a recording that had
   genuinely lost chunks was indistinguishable from a clean one and went on to
   be transcribed as though nothing had happened.

2. `hdiutil detach` can return non-zero WITHOUT raising, usually because the
   volume is still busy. The macOS updater discarded the exit status, so a
   failed detach was treated as success and left a volume mounted with nothing
   in the log to explain it.
"""

import os
import sys
import types

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import audio as audio_mod  # noqa: E402
from audio import AudioRecorder  # noqa: E402


def _chunk():
    return np.zeros((1024, 1), dtype=np.int16)


# ── 1. Capture health ───────────────────────────────────────────────────────

def test_overflow_status_is_counted():
    r = AudioRecorder()
    r._callback_active = True
    r._callback(_chunk(), 1024, None, "input overflow")
    assert r._capture_overflows == 1, "a dropped-input warning was discarded"


def test_clean_callback_records_no_problem():
    r = AudioRecorder()
    r._callback_active = True
    r._callback(_chunk(), 1024, None, None)
    assert r._capture_overflows == 0 and r._capture_errors == 0


def test_callback_exception_is_counted_not_swallowed_silently():
    """It must still not raise into the audio thread, but it must be recorded."""
    r = AudioRecorder()
    r._callback_active = True
    bad = types.SimpleNamespace(copy=lambda: (_ for _ in ()).throw(ValueError("boom")))
    r._callback(bad, 1024, None, None)      # must not raise
    assert r._capture_errors == 1, "a failed chunk append left no trace"


def test_counters_reset_per_recording(monkeypatch):
    r = AudioRecorder()
    r._callback_active = True
    r._capture_overflows = 5
    r._capture_errors = 3
    monkeypatch.setattr(r, "_create_stream", lambda: setattr(r, "_stream", object()))
    monkeypatch.setattr(audio_mod.time, "sleep", lambda _s: None)
    r.start()
    assert r._capture_overflows == 0 and r._capture_errors == 0


# ── 2. DMG detach reports failure ───────────────────────────────────────────

def _fake_proc(code, err=b""):
    return types.SimpleNamespace(returncode=code, stderr=err, stdout=b"")


def test_detach_success_reports_true(monkeypatch):
    import updater
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: _fake_proc(0))
    assert updater._hdiutil_detach("/Volumes/Waffler") is True


def test_detach_failure_reports_false(monkeypatch):
    """The reviewed bug: non-zero exit with no exception was called success."""
    import updater
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda *a, **k: _fake_proc(16, b"Resource busy"))
    monkeypatch.setattr(updater.time, "sleep", lambda _s: None)
    assert updater._hdiutil_detach("/Volumes/Waffler") is False


def test_detach_retries_then_succeeds(monkeypatch):
    """'Busy' is usually transient, so one retry is worth it."""
    import updater
    calls = {"n": 0}
    def run(*a, **k):
        calls["n"] += 1
        return _fake_proc(0) if calls["n"] > 1 else _fake_proc(16, b"busy")
    monkeypatch.setattr(updater.subprocess, "run", run)
    monkeypatch.setattr(updater.time, "sleep", lambda _s: None)
    assert updater._hdiutil_detach("/Volumes/Waffler") is True
    assert calls["n"] == 2


def test_detach_never_raises(monkeypatch):
    """By this point the update has already happened or already failed;
    raising here would help nobody."""
    import updater
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("gone")))
    monkeypatch.setattr(updater.time, "sleep", lambda _s: None)
    assert updater._hdiutil_detach("/Volumes/Waffler") is False


def test_detach_empty_target_is_noop(monkeypatch):
    import updater
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda *a, **k: pytest.fail("should not run"))
    assert updater._hdiutil_detach("") is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
