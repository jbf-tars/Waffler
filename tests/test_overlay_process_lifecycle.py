#!/usr/bin/env python3
"""Two callers must not be able to spawn two overlay children.

`_start_process` had no serialisation and no liveness re-check, and the reader
threads were started with `target=self._read_stdout` with no argument, so they
consulted the mutable `self._process` field rather than the child they were
created for. Two consequences:

  * `prestart()` racing `show()`, or two restarts arriving together, each
    launched a child. The second assignment to `self._process` orphaned the
    first, leaving a stray overlay process running with nobody managing it.
  * After any restart, an older reader thread drained the NEW child's pipes.

Starting is now serialised and skipped when a live child exists, and each
reader is handed its own process.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overlay import RecordingOverlay  # noqa: E402


class _Proc:
    def __init__(self, pid, alive=True):
        self.pid = pid
        self._alive = alive
        self.stdout = None
        self.stderr = None
    def poll(self):
        return None if self._alive else 0


def _overlay():
    o = object.__new__(RecordingOverlay)
    o._process = None
    o._send_lock = threading.Lock()
    o._start_lock = threading.RLock()
    o._log = lambda *a, **k: None
    o._visible = False
    return o


def test_start_is_skipped_when_a_child_is_already_alive(monkeypatch):
    o = _overlay()
    o._process = _Proc(pid=111)
    launched = []
    monkeypatch.setattr(RecordingOverlay, "_start_process_locked",
                        lambda self: launched.append(1))
    o._start_process()
    assert launched == [], "spawned a second child while one was alive"
    assert o._process.pid == 111, "the live child was replaced"


def test_start_proceeds_when_no_child_exists(monkeypatch):
    o = _overlay()
    launched = []
    monkeypatch.setattr(RecordingOverlay, "_start_process_locked",
                        lambda self: launched.append(1))
    o._start_process()
    assert launched == [1]


def test_start_proceeds_when_the_child_is_dead(monkeypatch):
    o = _overlay()
    o._process = _Proc(pid=222, alive=False)
    launched = []
    monkeypatch.setattr(RecordingOverlay, "_start_process_locked",
                        lambda self: launched.append(1))
    o._start_process()
    assert launched == [1], "a dead child must be replaced"


def test_concurrent_starts_launch_exactly_one_child(monkeypatch):
    """The reported race: prestart() against show(), or two restarts."""
    o = _overlay()
    launched = []

    def slow_launch(self):
        time.sleep(0.05)              # widen the window
        launched.append(1)
        self._process = _Proc(pid=100 + len(launched))

    monkeypatch.setattr(RecordingOverlay, "_start_process_locked", slow_launch)
    threads = [threading.Thread(target=o._start_process) for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join(5)
    assert len(launched) == 1, f"spawned {len(launched)} children, orphaning {len(launched)-1}"


# ── readers are bound to their own child ────────────────────────────────────

def test_stdout_reader_drains_the_child_it_was_given():
    o = _overlay()
    old, new = _Proc(1), _Proc(2)
    old.stdout = iter([])
    new.stdout = iter(['{"event":"wrong-child"}\n'])
    o._process = new                       # simulate a restart having happened
    o._read_stdout(old)                    # the OLD reader must read the OLD child
    # reaching here without touching new.stdout is the assertion
    assert list(new.stdout) == ['{"event":"wrong-child"}\n'], \
        "the old reader consumed the new child's output"


def test_stderr_reader_drains_the_child_it_was_given():
    o = _overlay()
    old, new = _Proc(1), _Proc(2)
    old.stderr = iter([])
    new.stderr = iter(["boom\n"])
    o._process = new
    o._read_stderr(old)
    assert list(new.stderr) == ["boom\n"], \
        "the old reader consumed the new child's stderr"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
