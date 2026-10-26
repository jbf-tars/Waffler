"""Bounded calls into PortAudio and CoreAudio, and the record of a wedge.

Why this exists
---------------
A live capture (``sample``) of a Mac whose audio had stopped answering showed a
deadlock inside CoreAudio, not in Waffler: one Python thread (through cffi,
that is sounddevice) in ``Pa_StopStream`` -> ``AudioOutputUnitStop`` ->
``HALB_Mutex::Lock``, CoreAudio's own I/O thread in PortAudio's
``startStopCallback`` -> ``AudioUnitGetProperty`` on a lock the first thread
held, and a third (the device monitor's listing) blocked in
``AudioObjectGetPropertyDataSize``. After that every CoreAudio call in the
process blocks for ever, and nothing inside the process can free it
(``coreaudiod`` itself was healthy). The calls were made while holding the
recorder's locks, so the press of the hotkey, the stop of a take and the
recording pill all waited behind them.

What it does
------------
``run_bounded(fn, timeout_s, name)`` runs ``fn`` on a daemon thread and waits
up to ``timeout_s``. It returns ``(done, value)``: ``(True, result)``, or
``(True, exception)`` when ``fn`` raised, or ``(False, WEDGED)`` when it did
not come back in time. That thread is then abandoned: it is left to finish (or
not) on its own, and the caller carries on, letting go of its locks.

While an abandoned call is still stuck, the next bounded call is not started
(``(False, SKIPPED)``): it would block too, and the device monitor asks every
two seconds, so starting a thread for each would pile them up for ever. A call
that was only slow and finishes late clears the mark, and calls run again.

``report_wedge`` is the one place a wedge is announced: one line in the log
for the whole process, and the handler Waffler registered
(src/wedge_recovery.py) told once. Nothing here imports Waffler code.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, List, Optional, Tuple

# What the second item of ``run_bounded``'s answer is when the call did not
# finish: it timed out and was abandoned (WEDGED), or an earlier call is still
# stuck so this one was not made (SKIPPED).
WEDGED = object()
SKIPPED = object()

_lock = threading.Lock()
# Calls given up on: (name, the thread still running it).
_abandoned: List[Tuple[str, threading.Thread]] = []
# Objects an abandoned call may still touch (a stream whose stop did not
# return). Never released: freeing a stream whose callback CoreAudio could
# still call is the crash that audio.py's teardown order exists to prevent.
_kept: List[Any] = []

_logger: Callable[[str], None] = print
_handler: Optional[Callable[[str], None]] = None
_reported: Optional[Tuple[str, float]] = None


def set_logger(fn: Optional[Callable[[str], None]]) -> None:
    """Where the one wedge line goes (app.log). ``None`` is ``print``."""
    global _logger
    _logger = fn or print


def set_handler(fn: Optional[Callable[[str], None]]) -> None:
    """Who is told, once, that audio has wedged (src/wedge_recovery.py)."""
    global _handler
    _handler = fn


def reset() -> None:
    """Forget everything. For tests: a stuck call of one test must not make
    the next one's calls skip."""
    global _reported, _handler, _logger
    with _lock:
        _abandoned.clear()
        _kept.clear()
    _reported = None
    _handler = None
    _logger = print


def wedged_op() -> Optional[str]:
    """The name of the earliest abandoned call that is still stuck, or None."""
    with _lock:
        _abandoned[:] = [(n, t) for n, t in _abandoned if t.is_alive()]
        return _abandoned[0][0] if _abandoned else None


def kept_alive() -> list:
    return list(_kept)


def forget_report() -> None:
    """The wedge that was announced turned out to be a call that was only slow
    and came back: the next one is a new wedge, logged and told again."""
    global _reported
    with _lock:
        _reported = None


def reported() -> Optional[Tuple[str, float]]:
    """``(call, wall time)`` of the wedge that was announced, or None."""
    return _reported


def run_bounded(fn: Callable[[], Any], timeout_s: float, name: str, *,
                skip_if_wedged: bool = True, keep: Any = None) -> Tuple[bool, Any]:
    """Run ``fn()`` and wait at most ``timeout_s`` seconds for it.

    Returns ``(True, result)``; ``(True, exception)`` if ``fn`` raised;
    ``(False, WEDGED)`` if it did not finish in time (its thread is abandoned,
    and ``keep`` is held for ever so nothing it may still use is freed); or
    ``(False, SKIPPED)`` without calling ``fn`` when an earlier call is still
    stuck (unless ``skip_if_wedged`` is False).
    """
    if skip_if_wedged and wedged_op() is not None:
        return False, SKIPPED
    box: dict = {}
    finished = threading.Event()

    def _work():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - handed to the caller
            box["value"] = e
        finally:
            finished.set()

    thread = threading.Thread(target=_work, daemon=True, name=f"Bounded-{name}")
    thread.start()
    if finished.wait(max(0.001, float(timeout_s))):
        return True, box.get("value")
    with _lock:
        _abandoned.append((name, thread))
        if keep is not None:
            _kept.append(keep)
    return False, WEDGED


def _seconds(value: float) -> str:
    return f"{float(value):g}s"


def report_wedge(op: str, timeout_s: float, *, notify: bool = True,
                 log: Optional[Callable[[str], None]] = None) -> bool:
    """Announce that CoreAudio did not answer. True the first time in this
    process, False after: one line in the log (``log``, else the module's), and
    the registered handler told once (``notify=False`` for a caller that tells
    its own owner)."""
    global _reported
    with _lock:
        if _reported is not None:
            return False
        _reported = (op, time.time())
    try:
        (log or _logger)(f"[audio] CoreAudio did not answer within {_seconds(timeout_s)} in {op}")
    except Exception:
        pass
    handler = _handler
    if notify and handler is not None:
        # Off the caller's thread: the handler shows a message and may save a
        # recording, and the caller is holding up a stream lock.
        threading.Thread(target=_tell, args=(handler, op), daemon=True,
                         name="AudioWedgeNotify").start()
    return True


def _tell(handler: Callable[[str], None], op: str) -> None:
    try:
        handler(op)
    except Exception:
        pass
