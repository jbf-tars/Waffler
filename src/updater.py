"""In-app auto-updater: download, install, relaunch.

Cross-platform (Windows + macOS). The download runs in a background thread;
UI polls `get_progress()` to show the progress bar. When the download
finishes, `install_and_restart()` spawns the platform-specific installer
detached from the current process, then exits the current app.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

# No-progress stall threshold: the download worker fails out if no bytes
# arrive for this many seconds. Without this the request can wedge silently
# and the UI sits at 0% forever (the symptom users actually report).
_STALL_TIMEOUT_S = 45

# A real-browser UA — GitHub's release-assets CDN sometimes throttles or
# 403s unidentified python-requests clients on signed-redirect URLs.
_USER_AGENT = "Waffler-Updater/1.0 (+https://github.com/jbf-tars/waffler)"

# Module-level state — one download at a time is all this app needs.
# Must be RLock so start_download() can call _reset_state() from inside
# its own `with _state_lock:` block. A plain Lock self-deadlocks here,
# which is the long-standing "stuck at 0%" bug users have been hitting
# since v3.13.0 — the worker thread never started because the main
# thread was deadlocked acquiring the same lock twice.
_state_lock = threading.RLock()
_state = {
    "active": False,
    "bytes_downloaded": 0,
    "total_bytes": 0,
    "done": False,
    "error": None,
    "path": None,
}


def _log(msg: str) -> None:
    """Append to ~/.waffler-hosted/app.log. Mirrors app._log_to_file but local
    to avoid an import cycle. Silent on any failure."""
    try:
        log_path = Path.home() / ".waffler-hosted" / "app.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts}  [updater] {msg}\n")
    except Exception:
        pass


def get_progress() -> dict:
    with _state_lock:
        return dict(_state)


def _reset_state():
    with _state_lock:
        _state.update(
            active=False,
            bytes_downloaded=0,
            total_bytes=0,
            done=False,
            error=None,
            path=None,
        )


def start_download(url: str) -> None:
    """Kick off a background download. Safe to call once at a time."""
    with _state_lock:
        if _state["active"]:
            return
        _reset_state()
        _state["active"] = True

    t = threading.Thread(target=_download_worker, args=(url,), daemon=True)
    t.start()


def _download_worker(url: str) -> None:
    """Download the installer to a temp file.

    On macOS we shell out to /usr/bin/curl rather than using Python's
    requests library. The PyInstaller-bundled requests/SSL stack on Mac
    has a long-standing issue where the connection establishes and the
    content-length header arrives but body chunks never reach
    iter_content(), leaving the UI's progress bar wedged at 0%. curl
    is shipped with macOS, uses the system's SSL trust store, and Just
    Works on signed-redirect URLs from GitHub Releases.

    On Windows we keep the existing requests-based path: it works fine
    there and the streaming progress is granular enough for the UX.
    """
    # Strip query string for filename (GitHub's signed-redirect URLs include one)
    name = url.rsplit("/", 1)[-1].split("?", 1)[0] or "waffler-update"
    dest = Path(tempfile.gettempdir()) / f"waffler-update-{os.getpid()}-{name}"
    partial = dest.with_suffix(dest.suffix + ".partial")
    _log(f"download start: {url[:80]}... -> {partial}")

    try:
        if sys.platform == "darwin":
            _download_with_curl(url, partial, dest)
        else:
            _download_with_requests(url, partial, dest)
    except Exception as e:
        _log(f"download failed: {type(e).__name__}: {e}")
        try:
            if partial.exists():
                partial.unlink()
        except Exception:
            pass
        with _state_lock:
            _state["error"] = str(e)
            _state["active"] = False


def _download_with_curl(url: str, partial: Path, dest: Path) -> None:
    """macOS download path. Uses /usr/bin/curl + size polling for progress."""
    # 1) HEAD request to learn total size (so the UI's progress bar isn't blind)
    try:
        head = subprocess.run(
            ["/usr/bin/curl", "-sI", "-L", "--max-time", "15", url],
            capture_output=True, text=True, timeout=20,
        )
        total = 0
        for line in head.stdout.splitlines():
            if line.lower().startswith("content-length:"):
                try:
                    total = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        _log(f"curl HEAD ok, content-length={total}")
    except Exception as e:
        _log(f"curl HEAD failed (continuing without total): {e}")
        total = 0
    with _state_lock:
        _state["total_bytes"] = total

    # 2) Stream the body via curl, polling the partial file size for progress.
    proc = subprocess.Popen(
        ["/usr/bin/curl", "-L", "-s", "-o", str(partial), "--connect-timeout", "15", url],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    last_progress = time.monotonic()
    last_logged_pct = -10
    last_size = 0
    while proc.poll() is None:
        if partial.exists():
            cur = partial.stat().st_size
            if cur != last_size:
                last_progress = time.monotonic()
                last_size = cur
                with _state_lock:
                    _state["bytes_downloaded"] = cur
                if total:
                    pct = int(cur * 100 / total)
                    if pct >= last_logged_pct + 25:
                        _log(f"progress {pct}% ({cur}/{total})")
                        last_logged_pct = pct
        if time.monotonic() - last_progress > _STALL_TIMEOUT_S:
            proc.kill()
            raise TimeoutError(f"download stalled (no bytes for {_STALL_TIMEOUT_S}s)")
        time.sleep(0.4)

    if proc.returncode != 0:
        err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        raise IOError(f"curl exited {proc.returncode}: {err.strip()[:200]}")

    actual = partial.stat().st_size
    if total and actual != total:
        raise IOError(f"download truncated: got {actual}, expected {total}")

    partial.replace(dest)
    _log(f"curl download done: {actual} bytes -> {dest}")
    with _state_lock:
        _state["bytes_downloaded"] = actual
        _state["done"] = True
        _state["active"] = False
        _state["path"] = str(dest)


def _download_with_requests(url: str, partial: Path, dest: Path) -> None:
    """Windows download path. Streams via requests; works reliably there."""
    with requests.get(
        url,
        stream=True,
        timeout=(15, 30),
        headers={"User-Agent": _USER_AGENT, "Accept": "application/octet-stream"},
        allow_redirects=True,
    ) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0) or 0)
        _log(f"HTTP {r.status_code}, content-length={total}")
        with _state_lock:
            _state["total_bytes"] = total

        last_progress = time.monotonic()
        last_logged_pct = -10
        with open(partial, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)
                    with _state_lock:
                        _state["bytes_downloaded"] += len(chunk)
                        done_bytes = _state["bytes_downloaded"]
                    last_progress = time.monotonic()
                    if total:
                        pct = int(done_bytes * 100 / total)
                        if pct >= last_logged_pct + 25:
                            _log(f"progress {pct}% ({done_bytes}/{total})")
                            last_logged_pct = pct
                elif time.monotonic() - last_progress > _STALL_TIMEOUT_S:
                    raise TimeoutError(
                        f"download stalled (no bytes for {_STALL_TIMEOUT_S}s)"
                    )

    actual = partial.stat().st_size
    if total and actual != total:
        raise IOError(f"download truncated: got {actual}, expected {total}")

    partial.replace(dest)
    _log(f"requests download done: {actual} bytes -> {dest}")
    with _state_lock:
        _state["done"] = True
        _state["active"] = False
        _state["path"] = str(dest)


def install_and_restart(installer_path: str) -> None:
    """Spawn the installer detached, then exit the current app."""
    path = Path(installer_path)
    if not path.exists():
        raise FileNotFoundError(f"Installer not found: {installer_path}")

    if sys.platform.startswith("win"):
        _install_windows(path)
    elif sys.platform == "darwin":
        _install_macos(path)
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")


def _install_windows(exe_path: Path) -> None:
    """Install the update and relaunch Waffler.

    v3.14.49 — switched from Inno Setup's ``/RESTARTAPPLICATIONS`` flag to
    an explicit batch-script relaunch. The old flag relied on Windows
    Restart Manager re-spawning the processes it closed, which had two
    failure modes the user actually hit:

      1. **"Installed but didn't reopen."** We called ``os._exit(0)`` just
         500 ms after launching the installer. Restart Manager registers a
         process for restart only if it's still alive when RM enumerates
         it; our early exit killed Waffler first, so RM had nothing to
         relaunch. Reported on the v3.14.45 update.
      2. **Triple-instance multi-paste.** When RM *did* fire, it sometimes
         relaunched more processes than it closed (main + overlay
         subprocess), producing the duplicate-instance bug that the
         v3.14.45 single-instance lock now defends against.

    New approach — a detached batch script that:
      1. Waits for the running Waffler.exe (our PID) to fully exit, so the
         single-instance lock is released and all files are unlocked.
      2. Runs the installer ``/SILENT /NORESTART`` (no RM restart — *we*
         own the relaunch now).
      3. Launches the freshly installed Waffler.exe exactly once.
      4. Deletes itself.

    Sleeps use ``ping`` rather than ``timeout`` because ``timeout`` needs a
    console handle, and we spawn with ``CREATE_NO_WINDOW``. PID-liveness is
    checked by image name ("Waffler") in the ``tasklist`` row so a digit
    collision in the memory column can't false-match.
    """
    pid = os.getpid()
    waffler_exe = Path(sys.executable)  # current Waffler.exe; same path post-install

    bat = (
        "@echo off\r\n"
        f"REM Wait for Waffler (PID {pid}) to exit so the single-instance\r\n"
        "REM lock is released and the installer can replace locked files.\r\n"
        ":wait_loop\r\n"
        f'tasklist /FI "PID eq {pid}" /NH 2>NUL | find /I "Waffler" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >NUL\r\n"
        "  goto wait_loop\r\n"
        ")\r\n"
        "REM Settle so Restart Manager fully releases handles.\r\n"
        "ping -n 3 127.0.0.1 >NUL\r\n"
        "REM Install silently; we relaunch ourselves, so NO /RESTARTAPPLICATIONS.\r\n"
        f'"{exe_path}" /SILENT /NORESTART\r\n'
        "ping -n 2 127.0.0.1 >NUL\r\n"
        "REM Launch the freshly installed Waffler exactly once.\r\n"
        f'start "" "{waffler_exe}"\r\n'
        'del "%~f0"\r\n'
    )
    bat_path = Path(tempfile.gettempdir()) / f"waffler_update_{pid}.bat"
    bat_path.write_text(bat, encoding="utf-8")

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
    )
    # Exit promptly so the batch's wait_loop sees us die and proceeds.
    time.sleep(0.2)
    os._exit(0)


def _install_macos(dmg_path: Path) -> None:
    pid = os.getpid()
    # Wait for parent, mount DMG, swap .app in /Applications, relaunch.
    script = f"""#!/bin/bash
set -e
while kill -0 {pid} 2>/dev/null; do sleep 0.3; done
sleep 0.5

MOUNT=$(hdiutil attach "{dmg_path}" -nobrowse -noautoopen -readonly | tail -1 | awk '{{for (i=3; i<=NF; i++) printf "%s%s", $i, (i<NF?FS:"")}}')
if [ -z "$MOUNT" ]; then exit 1; fi

APP_IN_DMG=$(find "$MOUNT" -maxdepth 2 -name "*.app" -type d | head -1)
if [ -z "$APP_IN_DMG" ]; then hdiutil detach "$MOUNT" >/dev/null 2>&1 || true; exit 1; fi

rm -rf "/Applications/Waffler.app"
cp -R "$APP_IN_DMG" "/Applications/Waffler.app"

hdiutil detach "$MOUNT" >/dev/null 2>&1 || true
open "/Applications/Waffler.app"
rm -f "{dmg_path}"
"""
    script_path = Path(tempfile.gettempdir()) / f"waffler_update_{pid}.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)

    subprocess.Popen(
        ["/bin/bash", str(script_path)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    os._exit(0)
