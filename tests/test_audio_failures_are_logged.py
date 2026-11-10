"""The recorder's own failures reach app.log, not only the console.

The app points the recorder's ``log`` at app.log, but nine of its lines were
still ``print``: a packaged app has no console, so "CAPTURE INCOMPLETE" (audio
dropped during a take), a PortAudio that would not start again, a stream that
would not stop or close, a reopen that failed and a microphone preference that
could not be applied were written nowhere. A report of words missing from a
take could not be checked against any of them.

``audio.sd`` is a fake (tests/test_audio_wedge.py); no microphone is opened.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import audio as audio_mod  # noqa: E402
from audio import AudioRecorder  # noqa: E402
from test_audio_wedge import WedgeSD  # noqa: E402
from test_mic_capture_health import feed  # noqa: E402


@pytest.fixture
def sd(monkeypatch):
    fake = WedgeSD()
    monkeypatch.setattr(audio_mod, "sd", fake)
    monkeypatch.setattr(audio_mod, "_HAL_DRAIN_S", 0)
    monkeypatch.setattr(audio_mod, "_POSTROLL_MS", 0)
    monkeypatch.setattr(audio_mod, "_recycles_when_idle", lambda: False)
    yield fake
    fake.never.set()


def make(sd, **kw):
    rec = AudioRecorder(**kw)
    rec.lines = []
    rec.log = rec.lines.append
    rec.start_monitoring()
    feed(rec, 300, 10)
    return rec


def test_audio_dropped_during_a_take_is_logged(sd):
    rec = make(sd)
    rec.start()
    rec._callback(audio_mod.np.full((1024, 1), 300, dtype="int16"), 1024, None, "input overflow")
    rec.stop()
    assert any("CAPTURE INCOMPLETE" in line and "1 overflow" in line for line in rec.lines)


def test_a_clean_take_logs_nothing_about_it(sd):
    rec = make(sd)
    rec.start()
    feed(rec, 300, 5)
    rec.stop()
    assert not any("CAPTURE INCOMPLETE" in line for line in rec.lines)


def test_a_portaudio_that_will_not_start_again_is_logged(sd, monkeypatch):
    rec = make(sd)

    def broken():
        raise RuntimeError("PortAudio not initialized")
    monkeypatch.setattr(sd, "_initialize", broken)
    rec.note_devices_changed()
    try:
        rec.rebuild_if_idle()
    except Exception:
        pass
    assert any("PortAudio reinit failed" in line for line in rec.lines)


def test_a_preference_that_cannot_be_applied_is_logged(sd):
    def resolver():
        raise KeyError("ranking")
    rec = make(sd, device_resolver=resolver)
    assert any("preference not applied (KeyError)" in line for line in rec.lines)


def test_a_reopen_that_fails_in_a_take_is_logged(sd, monkeypatch):
    rec = make(sd)
    rec.start()
    monkeypatch.setattr(rec, "_create_stream", lambda: (_ for _ in ()).throw(OSError("gone")))
    assert rec.reopen_stream() is False
    assert any("reopening the stream failed (OSError)" in line for line in rec.lines)


def test_no_failure_line_of_the_recorder_is_only_printed():
    """Every line a method of the recorder writes about something going wrong
    goes through ``_say`` (the app's log). What may stay a print: the lists for
    a terminal, and the lines of the module's own functions, which have no
    recorder to ask."""
    src = (ROOT / "src" / "audio.py").read_text(encoding="utf-8")
    body = src[src.index("class AudioRecorder:"):]
    printed = re.findall(r'print\(\s*f?"([^"]*)', body)
    wrong = [p for p in printed if re.search(
        r"fail|INCOMPLETE|superseded|not applied|abandon", p)]
    assert wrong == [], wrong


def test_a_pill_that_could_not_be_shown_or_hidden_is_logged_too():
    """app.py printed "[overlay] show failed" and "hide failed": a recording
    pill that did not appear left no trace in app.log."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'print(f"[overlay]' not in src
    assert src.count('_log_to_file(f"[overlay] show failed') == 1
    assert src.count('_log_to_file(f"[overlay] hide failed') == 2
