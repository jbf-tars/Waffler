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
from pathlib import Path

import requests

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
    try:
        # Filename from URL; fall back to a generic name.
        name = url.rsplit("/", 1)[-1] or "waffler-update"
        dest = Path(tempfile.gettempdir()) / f"waffler-update-{os.getpid()}-{name}"

        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0) or 0)
            with _state_lock:
                _state["total_bytes"] = total

            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    f.write(chunk)
                    with _state_lock:
                        _state["bytes_downloaded"] += len(chunk)

        with _state_lock:
            _state["done"] = True
            _state["active"] = False
            _state["path"] = str(dest)
    except Exception as e:
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
