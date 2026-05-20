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
from pathlib import Path


# Module-level handle so the lock outlives ``acquire()``'s scope.
_HOLDER = None


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
