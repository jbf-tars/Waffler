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
_state_lock = threading.Lock()
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
    # Strip query string for filename (GitHub's signed-redirect URLs include one)
    name = url.rsplit("/", 1)[-1].split("?", 1)[0] or "waffler-update"
    dest = Path(tempfile.gettempdir()) / f"waffler-update-{os.getpid()}-{name}"
    partial = dest.with_suffix(dest.suffix + ".partial")
    _log(f"download start: {url[:80]}... -> {partial}")

    try:
        # (connect_timeout, read_timeout). Read timeout caps each socket read
        # while streaming — crucial protection against silent stalls.
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

        # Sanity check: if we got a content-length, make sure we got it all.
        actual = partial.stat().st_size
        if total and actual != total:
            raise IOError(f"download truncated: got {actual}, expected {total}")

        # Atomic move — UI never sees a half-written file at the final path.
        partial.replace(dest)
        _log(f"download done: {actual} bytes -> {dest}")
        with _state_lock:
            _state["done"] = True
            _state["active"] = False
            _state["path"] = str(dest)
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
    # Inno Setup silent upgrade:
    #   /SILENT              — hide most dialogs, show progress only
    #   /CLOSEAPPLICATIONS   — close running Waffler via Restart Manager
    #   /RESTARTAPPLICATIONS — relaunch Waffler once install completes
    #   /NORESTART           — never reboot the machine
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        [str(exe_path), "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS", "/NORESTART"],
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )
    time.sleep(0.5)
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
