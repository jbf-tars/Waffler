"""Single-instance lock to prevent multiple main-mode Waffler processes.

Background
----------
v3.14.45 — diagnosed a "pasting loads of times" repro where the user's
``~/.waffler-hosted/app.log`` showed three ``=== Waffler starting ===``
banners in the same second (08:31:54), each with its own pipeline, its
own ``WindowsHotkeyListener`` (each installing its own low-level
keyboard hook), and its own audio recorder. One Win+Ctrl press then
fired ``on_release`` three times — three transcription threads ran in
parallel, three styled outputs, three clipboard+paste calls.

Root cause: the in-app updater (v3.14.34+) finishes by spawning the
Inno Setup installer with ``/RESTARTAPPLICATIONS``, which uses Windows
Restart Manager to relaunch closed Waffler.exe instances. With Waffler's
multi-process layout (main + overlay subprocess) RM ends up relaunching
more processes than it killed, and there's no defence at the app layer
against this. The same risk exists on macOS if a stale process survives
a restart (``open -n`` race).

This module is that defence. ``acquire()`` claims a platform-appropriate
lock the moment ``main()`` starts. If another main-mode instance
already holds the lock, ``acquire()`` returns False and ``main()`` must
exit immediately — before any pipeline / hotkey listener / audio
recorder is constructed.

Implementation
--------------
Windows: named mutex via ``CreateMutexW`` against a fixed name. If the
mutex already exists, ``GetLastError() == ERROR_ALREADY_EXISTS`` and we
treat that as "another instance is running". The mutex handle is held
for the lifetime of the process; Windows releases it automatically on
exit (even on crash), so a hard kill can't leave the lock orphaned.

macOS / Linux: ``fcntl.flock`` on a sentinel file under
``~/.waffler-hosted/``. ``LOCK_EX | LOCK_NB`` is the non-blocking
exclusive variant; if it raises ``BlockingIOError``, another instance
holds the lock. The kernel releases the lock when the holding process
exits — same crash-safety as Windows.

Public surface: ``acquire() -> bool`` returns True on success, False if
another instance already holds the lock. The lock object is kept alive
on the module level so it's not GC'd mid-run.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path


# Module-level handle so the lock outlives ``acquire()``'s scope.
_HOLDER = None

# v3.14.46 — Slack-style focus-existing-window UX. When a second main-
# mode instance detects the lock is held, it touches FOCUS_SIGNAL_PATH
# and exits. The first (lock-holding) instance runs a daemon thread
# polling for that file, and brings its window to the front when it
# appears. ~200 ms polling is well below the human "instant" threshold
# and costs nothing measurable on a modern CPU. A file is the simplest
# cross-platform IPC channel — works the same on Windows, macOS, Linux,
# and survives any signal/named-pipe/socket nuance per OS.
_FOCUS_SIGNAL_PATH = Path.home() / ".waffler-hosted" / "focus.signal"
_FOCUS_POLL_INTERVAL_S = 0.2


def acquire() -> bool:
    """Acquire the single-instance lock. Returns True if this is the
    first main-mode instance, False if another instance already holds
    the lock and we should exit.

    Safe to call multiple times — subsequent calls return True without
    touching the OS handle if we already hold the lock.
    """
    global _HOLDER
    if _HOLDER is not None:
        return True

    if sys.platform.startswith("win"):
        return _acquire_windows()
    return _acquire_posix()


def _acquire_windows() -> bool:
    """Windows: named mutex via ``CreateMutexW``."""
    import ctypes
    import ctypes.wintypes

    global _HOLDER

    # Mutex name. Global\ prefix would make this user-wide; without it
    # the mutex is local to the current session. Local is correct here —
    # two different Windows users could each run their own Waffler.
    MUTEX_NAME = "Waffler-Single-Instance-Mutex-v1"
    ERROR_ALREADY_EXISTS = 0xB7

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR
    ]
    kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_err = kernel32.GetLastError()

    if not handle:
        # Couldn't even create the mutex — let the app start anyway
        # rather than blocking on a Win32 oddity.
        print(f"[single-instance] CreateMutexW failed (err={last_err}); proceeding without lock")
        return True

    if last_err == ERROR_ALREADY_EXISTS:
        # Lock is already taken by another Waffler.exe. Close our
        # handle to the existing mutex — releasing it would be wrong
        # because we don't own it.
        kernel32.CloseHandle(handle)
        return False

    # Fresh acquisition. Hold the handle for process lifetime.
    _HOLDER = handle
    return True


def _acquire_posix() -> bool:
    """macOS / Linux: ``fcntl.flock`` on a sentinel file."""
    import fcntl

    global _HOLDER

    lock_dir = Path.home() / ".waffler-hosted"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "single-instance.lock"

    try:
        # Open in read-write append mode and keep the file descriptor
        # alive on the module-level _HOLDER so the kernel keeps the
        # lock attributed to us.
        fp = open(lock_path, "a+")
    except Exception as e:
        print(f"[single-instance] could not open lock file ({e}); proceeding without lock")
        return True

    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another instance holds the lock.
        fp.close()
        return False
    except OSError as e:
        # flock may not be supported on some FS (network mounts, exotic
        # macOS sandbox setups). Best-effort: proceed without the lock.
        print(f"[single-instance] flock failed ({e}); proceeding without lock")
        fp.close()
        return True

    _HOLDER = fp
    # Write our PID into the file so an external tool / postmortem can
    # see who actually held the lock.
    try:
        import os
        fp.seek(0)
        fp.truncate()
        fp.write(f"{os.getpid()}\n")
        fp.flush()
    except Exception:
        pass
    return True


# ── v3.14.46 focus-existing-window signal ──────────────────────────────


def signal_focus_to_existing() -> None:
    """Touch the focus-signal file so the already-running Waffler brings
    its window to the front. Call from the second instance immediately
    before ``sys.exit(0)`` when the lock is already held.

    Best-effort — any error is swallowed because a failed signal just
    falls back to the v3.14.45 "silent exit" behaviour, which is still
    acceptable (no extra processes spawn, no data lost; the user just
    has to find the existing window themselves).
    """
    try:
        _FOCUS_SIGNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FOCUS_SIGNAL_PATH.touch(exist_ok=True)
        # Update mtime even if the file already existed (the watcher
        # uses mtime as the trigger so back-to-back duplicate launches
        # all bring the window forward).
        _FOCUS_SIGNAL_PATH.write_text(f"{time.time():.6f}\n")
    except Exception:
        pass


def start_focus_watcher(window_ref, log_fn=None) -> None:
    """Start a daemon thread that polls ``focus.signal`` and brings the
    window to the front when a second instance touches it.

    Call from the FIRST (lock-holding) instance ONCE, AFTER the pywebview
    window has been created. The thread runs for the lifetime of the
    process and is a no-op if no second instance ever fires.

    Args:
        window_ref: the pywebview window object. Must expose ``show()``;
            ``restore()`` is called too if present (some pywebview
            versions don't have it).
        log_fn: optional logger callable; defaults to ``print``.

    The watcher consumes the signal file on every trigger so multiple
    back-to-back duplicate launches each get a fresh focus.
    """
    log = log_fn or print

    # Track the last mtime we processed so the same signal file isn't
    # re-fired forever if the unlink races us. (On Windows particularly,
    # unlink can fail briefly if another process has the handle open.)
    last_processed_mtime = [0.0]

    def _watch():
        while True:
            try:
                if _FOCUS_SIGNAL_PATH.exists():
                    try:
                        mtime = _FOCUS_SIGNAL_PATH.stat().st_mtime
                    except Exception:
                        mtime = 0.0
                    if mtime > last_processed_mtime[0]:
                        last_processed_mtime[0] = mtime
                        log("[single-instance] focus signal received — bringing window to front")
                        try:
                            window_ref.show()
                        except Exception as e:
                            log(f"[single-instance] window.show() failed: {e}")
                        try:
                            if hasattr(window_ref, "restore"):
                                window_ref.restore()
                        except Exception:
                            pass
                        # Consume the file so it doesn't repeatedly
                        # trigger if mtime polling skips the next tick.
                        try:
                            _FOCUS_SIGNAL_PATH.unlink()
                        except Exception:
                            pass
            except Exception:
                # Never let the watcher die — swallow and keep polling.
                pass
            time.sleep(_FOCUS_POLL_INTERVAL_S)

    t = threading.Thread(target=_watch, daemon=True, name="FocusWatcher")
    t.start()
