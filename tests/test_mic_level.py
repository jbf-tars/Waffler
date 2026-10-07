"""Settings, General: the microphone meter (3.15), offline.

The meter shows the level of the stream the dictations already use (it is
always open, to keep the half-second before each press). These drive the
recorder's own callback with made-up samples; no microphone is opened.
"""
import ast
import sys
import time
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from audio import AudioRecorder, rms_level  # noqa: E402


class _Stream:
    active = True


def _feed(rec, amplitude, chunks=3):
    """What PortAudio does: call the recorder's callback with int16 blocks."""
    for _ in range(chunks):
        block = np.full((1024, 1), amplitude, dtype=np.int16)
        rec._callback(block, 1024, None, None)


def _live_recorder(device_index=None):
    rec = AudioRecorder(sample_rate=16000, channels=1, device_index=device_index)
    rec._stream = _Stream()          # as if _create_stream had run
    rec._callback_active = True
    return rec


def test_the_level_scale_is_the_overlays():
    assert rms_level(np.zeros((1024, 1), dtype=np.int16)) == 0.0
    assert rms_level(np.full((1024, 1), 400, dtype=np.int16)) == 0.5
    assert rms_level(np.full((1024, 1), 32000, dtype=np.int16)) == 1.0


def test_the_meter_hears_the_stream_while_nothing_is_recording():
    rec = _live_recorder()
    _feed(rec, 200)
    assert rec.is_recording is False and rec._buffer == []     # nothing kept
    assert rec.input_level() == {"live": True, "level": 0.25, "current": True}
    _feed(rec, 0)
    assert rec.input_level()["level"] == 0.0
    # get_level() is the recording's level and stays 0 outside one.
    assert rec.get_level() == 0.0


def test_no_stream_or_a_stalled_one_is_not_shown_as_live():
    rec = AudioRecorder(sample_rate=16000, channels=1)
    assert rec.input_level()["live"] is False
    rec = _live_recorder()
    assert rec.input_level()["live"] is False                  # nothing delivered yet
    _feed(rec, 300)
    rec._last_chunk_at = time.monotonic() - 2.0                 # the device went quiet
    assert rec.input_level() == {"live": False, "level": 0.0, "current": True}


def test_a_newly_picked_microphone_is_not_passed_off_as_the_current_one():
    rec = _live_recorder(device_index=1)
    _feed(rec, 100)
    assert rec.input_level()["current"] is True
    rec.set_device(3)               # applies on the next dictation, not now
    v = rec.input_level()
    assert v["live"] is True and v["current"] is False


def _lift_api(name):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    return next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _get_mic_level(pipeline):
    ns = {"_pipeline": pipeline, "_log_to_file": lambda *_: None}
    exec(compile(ast.Module([_lift_api("get_mic_level")], []), "<app.py>", "exec"), ns)
    return ns["get_mic_level"](None)


def test_the_bridge_reads_the_pipelines_stream():
    rec = _live_recorder()
    _feed(rec, 400)
    assert _get_mic_level(types.SimpleNamespace(audio=rec)) == {
        "ok": True, "live": True, "level": 0.5, "current": True}
    # Still starting (no pipeline): nothing live, and no error.
    assert _get_mic_level(None) == {"ok": True, "live": False, "level": 0.0, "current": True}


def test_a_failure_is_an_answer_not_an_exception():
    class Broken:
        def input_level(self):
            raise RuntimeError("device gone")
    v = _get_mic_level(types.SimpleNamespace(audio=Broken()))
    assert v["ok"] is False and v["live"] is False
