"""The recorder and the recovery together, with a CoreAudio that stops answering.

Each half is tested alone (tests/test_audio_wedge.py, tests/test_wedge_recovery.py).
Here the recorder's own report is what starts the recovery, as in the app: a
take is being recorded, the stream is opened again (what the silence watch does),
the call to stop the old one never returns, and the recovery must keep what was
recorded, say so, and restart, with a recorder that is still stuck while it does.
Nothing restarts: the helper and the exit are fakes.
"""
import io
import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import wedge_recovery as wr  # noqa: E402
from audio import AudioRecorder  # noqa: E402
from test_audio_wedge import sd, wait_for, within  # noqa: E402,F401  (sd is the fixture the tests ask for)
from test_mic_capture_health import feed  # noqa: E402

EXE = "/Applications/Waffler.app/Contents/MacOS/Waffler"


class World:
    def __init__(self, tmp_path, rec, frozen=True):
        self.events = []
        self.lines = []
        self.kept = []
        self.rec = rec
        self.done = threading.Event()
        self.recovery = wr.WedgeRecovery(
            platform="darwin", frozen=frozen, executable=EXE, state_path=tmp_path / wr.FILE,
            take_in_progress=lambda: rec.is_recording, busy=lambda: False,
            salvage=rec.salvage_wav, keep=self._keep,
            show=lambda h, b: self.events.append(("show", h)),
            spawn=lambda argv: self.events.append(("spawn", argv[-4:-2])),
            exit_now=self._exit,
            still_stuck=rec.is_stuck, on_cleared=rec.clear_wedge,
            sleep=lambda s: None, log=self.lines.append, pid=777)
        rec.on_wedge = self.recovery.handle

    def _keep(self, wav):
        self.kept.append(wav)
        return True

    def _exit(self, code):
        self.events.append(("exit", code))
        self.done.set()


def frames(wav):
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes()


def take(sd):
    rec = AudioRecorder()
    rec.start_monitoring()
    rec.start()
    assert rec.is_recording
    feed(rec, 500, 20)
    return rec


def test_a_stream_that_will_not_stop_during_a_take_keeps_the_take_and_restarts(sd, tmp_path):
    rec = take(sd)
    recorded = sum(len(c) for c in rec._buffer)
    world = World(tmp_path, rec)
    sd.hang.add("stop")
    within(3.0, lambda: rec.reopen_stream())            # the silence watch opens the stream again
    assert world.done.wait(5), "the process was ended"
    assert [e[0] for e in world.events] == ["show", "spawn", "exit"]
    assert len(world.kept) == 1 and frames(world.kept[0]) == recorded, "the whole take, from the buffer"
    assert rec.is_recording, "reading the buffer did not end the take"
    assert world.events[1] == ("spawn", ["777", "/Applications/Waffler.app"])
    assert (tmp_path / wr.FILE).exists()


def test_the_same_wedge_in_a_development_run_keeps_nothing_and_ends_nothing(sd, tmp_path):
    rec = take(sd)
    world = World(tmp_path, rec, frozen=False)
    sd.hang.add("stop")
    within(3.0, lambda: rec.reopen_stream())
    assert wait_for(lambda: any("not restarting" in line for line in world.lines))
    assert not world.done.is_set() and world.kept == []
    # ...and the person can still end the take and get what was said.
    data = within(6.0, rec.stop)
    assert frames(data) > 20 * 1024


def test_a_slow_stop_that_comes_back_in_time_costs_no_restart(sd, tmp_path, monkeypatch):
    """A Bluetooth headset that takes 'a few seconds' to stop is slow, not stuck."""
    rec = take(sd)
    world = World(tmp_path, rec)
    release = threading.Event()
    real = sd.maybe_hang

    def maybe_hang(op):
        if op == "stop":
            release.wait(10)
        else:
            real(op)
    sd.maybe_hang = maybe_hang
    calls = {"n": 0}
    real_stuck = rec.is_stuck

    def stuck():
        calls["n"] += 1
        if calls["n"] == 3:
            release.set()                              # CoreAudio answers during the wait
            deadline = time.monotonic() + 3
            while real_stuck() and time.monotonic() < deadline:
                time.sleep(0.01)
        return real_stuck()
    world.recovery._still_stuck = stuck
    within(3.0, lambda: rec.reopen_stream())
    assert wait_for(lambda: any("slow, not stuck" in line for line in world.lines))
    assert not world.done.is_set() and world.kept == []
    assert rec._wedged is False, "the recorder forgot it, so the next wedge is a new one"
    sd.maybe_hang = real
    assert rec.is_recording
    assert frames(within(3.0, rec.stop)) > 20 * 1024
