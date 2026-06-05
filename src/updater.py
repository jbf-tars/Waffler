"""In-app auto-updater: download, install, relaunch.

Cross-platform (Windows + macOS). The download runs in a background thread;
UI polls `get_progress()` to show the progress bar. When the download
finishes, `install_and_restart()` spawns the platform-specific installer
detached from the current process, then exits the current app.
"""

from __future__ import annotations

import os
import plistlib
import shutil
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


# ---------------------------------------------------------------------------
# Integrity / authenticity verification.
#
# SECURITY: the updater downloads an artifact from the network and then
# executes it with the user's privileges. Before *any* execution we verify
# the artifact's code signature and fail CLOSED — i.e. we raise, the caller
# (app.install_update_and_restart) catches it and surfaces the error, and the
# unverified artifact is NEVER run and the app is NEVER replaced.
#
# Verification happens synchronously, in-process, BEFORE the detached
# installer helper is spawned and BEFORE os._exit(). This is deliberate: the
# helper scripts run after this process is gone, so an error raised inside
# them could not propagate back to the caller. Doing the checks here is what
# makes "fail closed" actually hold.
#
# This project does not record an expected SHA-256 of the artifact anywhere,
# so we do not invent one; the platform code-signature checks below are the
# authoritative trust anchor (Developer ID / notarization on macOS,
# Authenticode on Windows).
# ---------------------------------------------------------------------------


def _verify_macos_app_signature(app_path: Path) -> None:
    """Verify a macOS .app is validly signed and accepted by Gatekeeper.

    Runs ``codesign --verify --deep --strict`` (signature integrity) and
    ``spctl --assess --type execute`` (Gatekeeper / notarization policy).
    Raises ``RuntimeError`` if either tool is missing, errors, or rejects the
    app. Never returns on failure — callers must treat a return as "verified".
    """
    checks = (
        (
            "codesign",
            ["/usr/bin/codesign", "--verify", "--deep", "--strict",
             "--verbose=2", str(app_path)],
        ),
        (
            "spctl",
            ["/usr/sbin/spctl", "--assess", "--type", "execute",
             "--verbose=2", str(app_path)],
        ),
    )
    for name, cmd in checks:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"signature verification unavailable ({name} not found): {e}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"signature verification timed out ({name})"
            ) from e
        if proc.returncode != 0:
            # codesign/spctl write their diagnostics to stderr.
            detail = (proc.stderr or proc.stdout or "").strip()[:300]
            raise RuntimeError(
                f"{name} verification failed (exit {proc.returncode}) "
                f"for {app_path.name}: {detail}"
            )
        _log(f"{name} verification ok for {app_path.name}")
    _log(f"signature verification passed: {app_path}")


def _verify_windows_exe_signature(exe_path: Path) -> None:
    """Verify a Windows installer's Authenticode signature is Valid.

    Uses PowerShell ``Get-AuthenticodeSignature`` and requires
    ``Status -eq 'Valid'``. If PowerShell is missing or the call errors,
    raises ``RuntimeError`` (fail closed — we do NOT run an unverified EXE).
    """
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise RuntimeError(
            "signature verification unavailable: PowerShell not found; "
            "refusing to run unverified installer"
        )
    # Print the Status; treat anything other than exactly 'Valid' as failure.
    # The path goes into a single-quoted PS literal; escape any single quote
    # by doubling it (PowerShell's escaping rule) so a crafted filename can't
    # break out of the literal. -LiteralPath also stops glob interpretation.
    ps_path = str(exe_path).replace("'", "''")
    ps_script = (
        "$ErrorActionPreference = 'Stop'; "
        "$sig = Get-AuthenticodeSignature -LiteralPath "
        f"'{ps_path}'; "
        "Write-Output $sig.Status"
    )
    try:
        proc = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            f"signature verification unavailable (PowerShell not found): {e}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("signature verification timed out (PowerShell)") from e

    status = (proc.stdout or "").strip()
    if proc.returncode != 0 or status != "Valid":
        detail = status or (proc.stderr or "").strip()[:300]
        raise RuntimeError(
            f"Authenticode verification failed for {exe_path.name}: "
            f"status={detail!r} (exit {proc.returncode})"
        )
    _log(f"Authenticode verification passed: {exe_path}")


def install_and_restart(installer_path: str) -> None:
    """Spawn the installer detached, then exit the current app.

    Before spawning anything, the downloaded artifact's code signature is
    verified (see ``_verify_*_signature``). On verification failure this
    raises and the artifact is never executed — the caller keeps the current
    app running and surfaces the error to the user.
    """
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

    v3.14.73 — fix "update installs but the version never changes." The
    v3.14.49 batch waited only for the *main* Waffler PID to exit before
    running the installer. But Waffler also spawns an **overlay subprocess**
    (a second ``Waffler.exe --overlay``) which kept the ``_internal`` Python
    DLLs locked. Inno Setup then silently skipped the locked files (with
    ``/NORESTART`` it just leaves them), so the install "succeeded" without
    replacing anything and the user stayed on the old version forever — the
    in-app updater literally could not update itself. The leftover
    ``waffler_update_*.bat`` scripts piling up in %TEMP% (never reaching
    their ``del``) were the tell.

    New batch:
      1. Force-kill ALL ``Waffler.exe`` (main + overlay child + any zombie),
         looping until none remain, so NOTHING holds a file handle.
      2. Settle, then run the installer ``/VERYSILENT /SUPPRESSMSGBOXES
         /NORESTART`` — VERYSILENT shows no UI and SUPPRESSMSGBOXES
         auto-answers any prompt, so a hidden "files in use" dialog can never
         hang the headless (CREATE_NO_WINDOW) batch (another way the old one
         wedged).
      3. Write an install log to %TEMP%\\waffler_install.log so a future
         failure is diagnosable.
      4. Relaunch the freshly installed Waffler exactly once, then self-delete.

    Sleeps use ``ping`` rather than ``timeout`` because ``timeout`` needs a
    console handle and we spawn with ``CREATE_NO_WINDOW``.

    SECURITY: before generating/spawning the relaunch batch, the downloaded
    EXE's Authenticode signature is verified. If it is not ``Valid`` (or
    verification can't run), we raise here — fail closed — so the installer
    is never executed.
    """
    # Verify authenticity BEFORE doing anything else. Raises on failure; the
    # caller catches it and the unverified installer is never run.
    _verify_windows_exe_signature(exe_path)

    waffler_exe = Path(sys.executable)  # current Waffler.exe; same path post-install
    log_path = Path(tempfile.gettempdir()) / "waffler_install.log"

    bat = (
        "@echo off\r\n"
        "REM Give the parent a moment to exit on its own.\r\n"
        "ping -n 2 127.0.0.1 >NUL\r\n"
        "REM Force-kill EVERY Waffler.exe (main + overlay subprocess) so no\r\n"
        "REM _internal\\ file is locked when the installer overwrites it. The\r\n"
        "REM overlay child kept the DLLs locked, which is why updates silently\r\n"
        "REM did nothing before v3.14.73.\r\n"
        ":kill_loop\r\n"
        "taskkill /F /IM Waffler.exe >NUL 2>&1\r\n"
        'tasklist /FI "IMAGENAME eq Waffler.exe" /NH 2>NUL | find /I "Waffler.exe" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >NUL\r\n"
        "  goto kill_loop\r\n"
        ")\r\n"
        "REM Settle so the OS releases all file handles.\r\n"
        "ping -n 4 127.0.0.1 >NUL\r\n"
        "REM No UI, auto-dismiss any prompt, log for diagnosis.\r\n"
        f'"{exe_path}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="{log_path}"\r\n'
        "ping -n 2 127.0.0.1 >NUL\r\n"
        "REM Launch the freshly installed Waffler exactly once.\r\n"
        f'start "" "{waffler_exe}"\r\n'
        'del "%~f0"\r\n'
    )
    bat_path = Path(tempfile.gettempdir()) / f"waffler_update_{os.getpid()}.bat"
    bat_path.write_text(bat, encoding="utf-8")

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
    )
    # Exit promptly so the batch's kill_loop finds nothing to wait on and the
    # installer runs against fully-unlocked files.
    time.sleep(0.2)
    os._exit(0)


def _hdiutil_attach(dmg_path: Path) -> tuple[str, str]:
    """Attach a DMG and return ``(mount_point, dev_entry)``.

    Uses ``hdiutil attach -plist`` and parses the property list with
    ``plistlib`` rather than scraping ``tail``/``awk`` output — the textual
    form breaks on volume names containing spaces and on DMGs with multiple
    partition rows. Returns the first mounted filesystem's mount point and a
    dev-entry suitable for ``hdiutil detach``.

    Raises ``RuntimeError`` if attach fails or no mount point can be found.
    """
    proc = subprocess.run(
        ["/usr/bin/hdiutil", "attach", str(dmg_path),
         "-nobrowse", "-noautoopen", "-readonly", "-plist"],
        capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise RuntimeError(f"hdiutil attach failed (exit {proc.returncode}): {detail}")

    try:
        info = plistlib.loads(proc.stdout)
    except Exception as e:
        raise RuntimeError(f"could not parse hdiutil plist output: {e}") from e

    mount_point = ""
    dev_entry = ""
    for entity in info.get("system-entities", []):
        # The first entity carrying a mount point is the mounted volume; keep
        # its dev-entry so we can detach precisely.
        mp = entity.get("mount-point")
        if mp:
            mount_point = mp
            dev_entry = entity.get("dev-entry", "") or dev_entry
            break
        # Fall back to the topmost dev-entry for detaching even if no volume
        # mounted (so cleanup can still run).
        if not dev_entry:
            dev_entry = entity.get("dev-entry", "") or ""

    if not mount_point:
        # Nothing usable mounted; detach whatever attached, then fail.
        if dev_entry:
            _hdiutil_detach(dev_entry)
        raise RuntimeError("hdiutil attach produced no mount point")

    return mount_point, dev_entry


def _hdiutil_detach(target: str) -> None:
    """Best-effort ``hdiutil detach`` of a mount point or dev-entry."""
    if not target:
        return
    try:
        subprocess.run(
            ["/usr/bin/hdiutil", "detach", target, "-force"],
            capture_output=True, timeout=60,
        )
    except Exception as e:
        _log(f"hdiutil detach failed for {target!r} (ignored): {e}")


def _install_macos(dmg_path: Path) -> None:
    """Mount the DMG, verify+swap the app atomically, then relaunch.

    Order of operations (all destructive/verification work done HERE,
    synchronously, so failures fail closed and never leave the user without
    an app):

      1. Attach the DMG (robust plist parse) and locate the .app inside it;
         confirm it EXISTS before touching the installed copy.
      2. Verify the in-DMG app's code signature (codesign + spctl). Abort on
         failure.
      3. Stage: copy the new app to a temp dir *inside* /Applications, then
         re-verify the staged copy's signature.
      4. Atomic swap: move the old app aside, move the staged app into place;
         on any error, restore the old app. Only then remove the old copy.
      5. Detach the DMG (always, via finally).

    Finally, spawn a tiny detached helper that waits for this process to exit
    and relaunches the installed app (the app can't `open` itself and survive
    its own os._exit), then cleans up the DMG and leftover staging.
    """
    pid = os.getpid()
    apps_dir = Path("/Applications")
    installed = apps_dir / "Waffler.app"

    mount_point = ""
    dev_entry = ""
    staged = apps_dir / f".waffler-update-staged-{pid}.app"
    backup = apps_dir / f".waffler-update-old-{pid}.app"
    # When a swap rollback fails, `backup` may hold the ONLY copy of the app.
    # In that case we must not delete it during cleanup — surface it instead.
    preserve_backup = False

    def _cleanup_paths() -> None:
        targets = [staged]
        if not preserve_backup:
            targets.append(backup)
        for p in targets:
            try:
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass

    try:
        mount_point, dev_entry = _hdiutil_attach(dmg_path)

        # Locate the .app in the DMG and confirm it exists BEFORE we touch
        # the installed copy.
        app_in_dmg = None
        mount_root = Path(mount_point)
        # Search up to 2 levels deep (matches the prior `find -maxdepth 2`).
        candidates = list(mount_root.glob("*.app")) + list(mount_root.glob("*/*.app"))
        for cand in candidates:
            if cand.is_dir():
                app_in_dmg = cand
                break
        if app_in_dmg is None or not app_in_dmg.exists():
            raise RuntimeError(f"no .app bundle found in mounted DMG at {mount_point}")
        _log(f"located app in DMG: {app_in_dmg}")

        # (1) Verify the in-DMG app's signature. Fail closed.
        _verify_macos_app_signature(app_in_dmg)

        # (2) Stage a copy inside /Applications (same filesystem → fast,
        # atomic rename later). Clean any stale staging first.
        _cleanup_paths()
        shutil.copytree(app_in_dmg, staged, symlinks=True)
        if not staged.exists():
            raise RuntimeError("failed to stage new app into /Applications")

        # (3) Re-verify the staged copy actually on disk before swapping.
        _verify_macos_app_signature(staged)

        # (4) Atomic-ish swap: move old aside, move new in. Restore on error.
        old_moved = False
        try:
            if installed.exists():
                os.rename(installed, backup)
                old_moved = True
            os.rename(staged, installed)
        except Exception as swap_err:
            # Roll back: ensure the original app is back in place.
            try:
                if installed.exists() and old_moved:
                    # New partially in place but old saved — remove partial,
                    # restore old.
                    shutil.rmtree(installed, ignore_errors=True)
                if old_moved and backup.exists() and not installed.exists():
                    os.rename(backup, installed)
            except Exception as restore_err:
                # The backup may now be the only intact copy — keep it.
                preserve_backup = True
                _log(f"CRITICAL: rollback failed: {restore_err}")
                raise RuntimeError(
                    f"install swap failed ({swap_err}) and rollback failed "
                    f"({restore_err}); app may be in {backup}"
                ) from swap_err
            raise RuntimeError(f"install swap failed, old app restored: {swap_err}") from swap_err

        # Success — remove the old copy.
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        _log(f"swapped in new app at {installed}")

    except Exception:
        # Make sure we don't leave staging/backup litter on failure.
        _cleanup_paths()
        raise
    finally:
        # Always detach the DMG, even on failure.
        _hdiutil_detach(dev_entry or mount_point)

    # At this point the new, verified app is installed. Spawn a detached
    # helper that relaunches it after we exit and cleans up the DMG. This
    # helper does NO install work and NO unverified execution — it only opens
    # the already-installed-and-verified app.
    #
    # The app/DMG paths are passed as positional args ("$1"/"$2") rather than
    # interpolated into the script body, so a path containing shell
    # metacharacters can't be evaluated. Only the integer PID is interpolated.
    script = f"""#!/bin/bash
APP="$1"
DMG="$2"
while kill -0 {pid} 2>/dev/null; do sleep 0.3; done
sleep 0.5
open "$APP"
rm -f "$DMG"
rm -f "$0"
"""
    script_path = Path(tempfile.gettempdir()) / f"waffler_update_{pid}.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)

    subprocess.Popen(
        ["/bin/bash", str(script_path), str(installed), str(dmg_path)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    os._exit(0)
