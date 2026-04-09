"""
Waffler - Floating Recording Overlay
Spawns a platform-appropriate overlay subprocess:
  • macOS   → overlay_process.py    (PyObjC NSPanel)
  • Windows → overlay_process_windows.py (tkinter)

Main API: RecordingOverlay().show() / .hide() / .update_level(0..1)
"""

import subprocess
import sys
import json
import time
import threading
import platform
from pathlib import Path

# ── Pick the right subprocess script ──────────────────────────────────
_PLATFORM = platform.system()  # "Darwin", "Windows", "Linux"

if _PLATFORM == "Windows":
    _OVERLAY_SCRIPT_NAME = "overlay_process_windows.py"
else:
    # macOS (Darwin) - the original PyObjC NSPanel overlay
    _OVERLAY_SCRIPT_NAME = "overlay_process.py"


class RecordingOverlay:
    """
    Floating pill-shaped recording overlay.
    Runs as a subprocess so its UI thread does not block the main app.

    Public API:
        show()                - make overlay visible
        hide()                - hide overlay (subprocess kept alive)
        update_level(float)   - push RMS level 0.0-1.0 for VU animation
        show_toast(...)       - show a floating toast popup above the pill
        hide_toast()          - dismiss the toast popup
        stop()                - terminate subprocess
    """

    def _log(self, msg: str):
        """Centralized logging to app.log with timestamp."""
        from pathlib import Path
        from datetime import datetime
        try:
            log_file = Path.home() / ".waffler-hosted" / "app.log"
            with open(log_file, "a") as f:
                ts = datetime.now().strftime("%H:%M:%S")
                f.write(f"{ts}  {msg}\n")
        except Exception:
            pass  # Don't crash if logging fails

    def __init__(self, on_cancel=None, on_stop=None, on_cancel_request=None,
                 on_toast_action=None):
        """
        Args:
            on_cancel:         Callback when user clicks X (discard recording)
            on_stop:           Callback when user clicks ■ (process recording)
            on_cancel_request: Callback when user clicks X (confirmation needed first)
            on_toast_action:   Callback(action: str) when user clicks a toast button
        """
        self._on_cancel = on_cancel
        self._on_stop   = on_stop
        self._on_cancel_request = on_cancel_request
        self._on_toast_action = on_toast_action
        self._process   = None
        self._reader_thread = None
        self._visible   = False

        # Thread safety and restart tracking
        self._send_lock = threading.Lock()  # Protect stdin writes
        self._restart_lock = threading.Lock()  # Protect restart state
        self._restart_count = 0             # Track restart attempts
        self._last_restart_time = 0         # For exponential backoff
        self._restart_window = 60           # Reset counter after 60s

        # Resolve the subprocess script path
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            self._script = Path(sys._MEIPASS) / "src" / _OVERLAY_SCRIPT_NAME
        else:
            self._script = Path(__file__).parent / _OVERLAY_SCRIPT_NAME

    # ── Public API ────────────────────────────────────────────────────

    def show(self):
        """Show the recording overlay."""
        self._log("[overlay] show() called")

        if self._is_alive():
            self._log("[overlay] Subprocess already alive, sending show command")
            self._send({"type": "show"})
            self._visible = True
            return

        # Subprocess not running, start it
        self._log("[overlay] Starting new subprocess...")
        if self._attempt_restart():
            self._send({"type": "show"})
            self._visible = True
        else:
            self._log("[overlay] ✗ ERROR: Failed to start subprocess after retries")
            print("[overlay] ERROR: Subprocess failed to start!", flush=True)

    def hide(self):
        """Hide the overlay (subprocess stays alive for reuse)."""
        if self._is_alive():
            self._send({"type": "hide"})
        self._visible = False

    def update_level(self, level: float):
        """
        Push an audio RMS level (0.0-1.0) to animate the VU bars.
        Safe to call from any thread at any rate.
        Auto-restarts subprocess if it died during recording.
        """
        # Detect subprocess death during recording
        if not self._is_alive():
            if self._visible:
                # Process died while recording — attempt auto-restart
                self._log("[overlay] Subprocess died during recording, attempting auto-restart...")

                if self._attempt_restart():
                    # Restart successful, resume showing overlay
                    self._send({"type": "show"})
                else:
                    # Max restarts exceeded, give up gracefully
                    self._visible = False
                    self._log("[overlay] Recording continues without overlay (subprocess unrecoverable)")
            return

        # Send level update (thread-safe)
        level = max(0.0, min(1.0, float(level)))
        self._send({"type": "level", "value": level})

    def update_state(self, state: str):
        """
        Update overlay state: "recording" or "paused".
        Shows visual feedback for paused state.
        """
        if self._is_alive():
            self._send({"type": "state", "value": state})

    def show_toast(self, style: str, heading: str, body: str):
        """
        Show a floating toast popup above the pill overlay.
        style: "cancel" or "error"
        """
        self._log(f"[overlay.py] show_toast: style={style}, heading='{heading}'")
        if self._is_alive():
            self._log("[overlay.py] Subprocess ALIVE, sending show_toast command")
            self._send({
                "type": "show_toast",
                "style": style,
                "heading": heading,
                "body": body,
            })
            self._log("[overlay.py] show_toast command SENT successfully")
        else:
            self._log("[overlay.py] ERROR: Subprocess is DEAD, cannot show toast!")

    def hide_toast(self):
        """Dismiss the toast popup."""
        if self._is_alive():
            self._send({"type": "hide_toast"})

    def stop(self):
        """Terminate the overlay subprocess."""
        if self._is_alive():
            self._send({"type": "quit"})
            time.sleep(0.15)
            try:
                self._process.terminate()
            except Exception:
                pass
        self._process = None
        self._visible = False

    @property
    def visible(self):
        return self._visible and self._is_alive()

    # ── Context manager support ───────────────────────────────────────

    def __enter__(self):
        self.show()
        return self

    def __exit__(self, *_):
        self.stop()

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass

    # ── Internals ─────────────────────────────────────────────────────

    def _find_python(self):
        """Find a usable Python interpreter for the overlay subprocess."""
        import os

        self._log(f"[overlay] _find_python called, sys.frozen={getattr(sys, 'frozen', False)}")
        self._log(f"[overlay] sys.executable={sys.executable}")

        if getattr(sys, 'frozen', False):
            import shutil

            self._log(f"[overlay] App is frozen, searching for Python interpreter...")

            # macOS-specific: Try common Python locations on macOS
            # Most Macs have Python 3 pre-installed
            mac_python_paths = [
                '/usr/bin/python3',  # macOS system Python (most common)
                '/usr/local/bin/python3',  # Homebrew Python
                '/opt/homebrew/bin/python3',  # Homebrew on Apple Silicon
                '/Library/Frameworks/Python.framework/Versions/3.11/bin/python3',
                '/Library/Frameworks/Python.framework/Versions/3.10/bin/python3',
                '/Library/Frameworks/Python.framework/Versions/3.9/bin/python3',
            ]

            # First try specific macOS paths
            for path_str in mac_python_paths:
                if os.path.exists(path_str):
                    self._log(f"[overlay] ✓ Found macOS Python: {path_str}")
                    print(f"[overlay] Found Python: {path_str}")
                    return path_str

            # Fallback: Search PATH
            self._log("[overlay] Checking PATH for Python...")
            for name in ('python3', 'python'):
                path = shutil.which(name)
                if path:
                    self._log(f"[overlay] ✓ Found Python in PATH: {path}")
                    print(f"[overlay] Found Python: {path}")
                    return path

            # No Python found — return None
            self._log("[overlay] ✗ ERROR: No Python interpreter found")
            self._log("[overlay] Checked: macOS system paths + PATH")
            print("[overlay] WARNING: No Python interpreter found")
            return None

        self._log(f"[overlay] Using sys.executable: {sys.executable}")
        return sys.executable

    def _start_process(self):
        """Launch the overlay subprocess."""
        self._log(f"[overlay] _start_process called")
        self._log(f"[overlay] Script path: {self._script}")
        self._log(f"[overlay] Script exists: {self._script.exists()}")

        if not self._script.exists():
            self._log(f"[overlay] ✗ ERROR: script not found: {self._script}")
            print(f"[overlay] WARNING: script not found: {self._script}", flush=True)
            return

        python = self._find_python()
        if python is None:
            self._log("[overlay] ✗ ERROR: Cannot start overlay: no Python interpreter available")
            self._log("[overlay] App will continue without overlay (transcription still works)")
            print("[overlay] Cannot start overlay: no Python interpreter available", flush=True)
            print("[overlay] Note: App will work without visual overlay", flush=True)
            return

        try:
            # Use CREATE_NO_WINDOW on Windows to avoid a console flash
            kwargs = {}
            if _PLATFORM == "Windows":
                CREATE_NO_WINDOW = 0x08000000
                kwargs["creationflags"] = CREATE_NO_WINDOW
        except Exception as e:
            self._log(f"[overlay] Exception setting kwargs: {e}")
            kwargs = {}

        self._log(f"[overlay] Starting subprocess: {python} {self._script}")

        # CRITICAL FIX: When frozen, add bundled libraries to PYTHONPATH
        # so the subprocess can import PyObjC from the .app bundle
        import os
        env = os.environ.copy()

        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            meipass = sys._MEIPASS
            self._log(f"[overlay] App is frozen, MEIPASS={meipass}")

            if _PLATFORM != "Windows":
                # macOS only: add MEIPASS to PYTHONPATH so subprocess finds bundled PyObjC.
                # On Windows, the overlay uses system Python's own tkinter — setting
                # PYTHONPATH to the bundle causes a DLL version conflict (_tkinter.pyd
                # built for a different Python version).
                pythonpath = env.get('PYTHONPATH', '')
                if pythonpath:
                    env['PYTHONPATH'] = f"{meipass}:{pythonpath}"
                else:
                    env['PYTHONPATH'] = meipass
                self._log(f"[overlay] Set PYTHONPATH={env['PYTHONPATH']}")
            else:
                # Windows: remove bundled Tcl/Tk env vars so system Python uses
                # its own Tcl/Tk instead of the bundled version (version mismatch).
                for var in ('TCL_LIBRARY', 'TK_LIBRARY', 'PYTHONPATH'):
                    env.pop(var, None)
                self._log("[overlay] Windows: cleared TCL_LIBRARY/TK_LIBRARY/PYTHONPATH (using system tkinter)")

        try:
            self._process = subprocess.Popen(
                [python, str(self._script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # CHANGED: Capture stderr to see errors
                text=True,
                bufsize=1,
                env=env,  # Pass modified environment with PYTHONPATH
                **kwargs,
            )
            self._log(f"[overlay] ✓ Subprocess started, PID={self._process.pid}")

            # Start stderr reader thread to log any errors
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                daemon=True,
                name="OverlayStderr",
            )
            self._stderr_thread.start()

            self._reader_thread = threading.Thread(
                target=self._read_stdout,
                daemon=True,
                name="OverlayReader",
            )
            self._reader_thread.start()
            self._log(f"[overlay] Reader threads started")

        except Exception as e:
            self._log(f"[overlay] ✗ EXCEPTION starting subprocess: {e}")
            print(f"[overlay] EXCEPTION: {e}", flush=True)
            import traceback
            self._log(f"[overlay] Traceback: {traceback.format_exc()}")

    def _is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _attempt_restart(self) -> bool:
        """Restart subprocess with exponential backoff.

        Strategy:
        - Track restart attempts within 60-second window
        - Reset counter if last restart was >60s ago
        - Apply exponential backoff: 0.5s, 1s, 2s
        - Give up after 3 attempts in window

        Returns:
            bool: True if restart successful, False if max attempts exceeded
        """
        with self._restart_lock:  # Ensure atomic restart logic
            current_time = time.time()

            # Reset counter if last restart was >60s ago (stable period)
            if current_time - self._last_restart_time > self._restart_window:
                self._restart_count = 0

            self._restart_count += 1
            self._last_restart_time = current_time

            if self._restart_count > 3:
                self._log("[overlay] ✗ Max restart attempts (3) exceeded in 60s window, giving up")
                print("[overlay] ERROR: Max restart attempts exceeded", flush=True)
                return False

            # Exponential backoff: 0.5s, 1s, 2s (max 3 attempts)
            delay = 0.5 * (2 ** (self._restart_count - 1))
            self._log(f"[overlay] Restarting subprocess in {delay}s (attempt {self._restart_count}/3)")
            time.sleep(delay)

            self._start_process()
            success = self._is_alive()

            if success:
                self._log(f"[overlay] ✓ Subprocess restarted successfully, PID={self._process.pid}")
            else:
                self._log(f"[overlay] ✗ Subprocess restart failed")

            return success

    def _send(self, data: dict) -> bool:
        """Write a JSON command to the subprocess stdin (thread-safe).

        Returns:
            bool: True if command sent successfully, False otherwise
        """
        if not self._is_alive():
            return False

        with self._send_lock:  # Ensure atomic write
            try:
                self._process.stdin.write(json.dumps(data) + "\n")
                self._process.stdin.flush()
                return True
            except (BrokenPipeError, OSError) as e:
                # Log instead of silently swallowing
                self._log(f"[overlay] ⚠️  Broken pipe during send: {e}")
                return False

    def _read_stdout(self):
        """Read event callbacks from subprocess stdout."""
        if not self._process or not self._process.stdout:
            self._log("[overlay] _read_stdout: no process or stdout")
            return

        self._log("[overlay] _read_stdout: started reading")

        for line in self._process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                data  = json.loads(line)
                event = data.get("event")
                self._log(f"[overlay] Received event: {event}")

                if event == "cancel" and self._on_cancel:
                    self._on_cancel()
                elif event == "cancel_request":
                    if self._on_cancel_request:
                        self._on_cancel_request()
                    elif self._on_cancel:
                        self._on_cancel()
                elif event == "stop" and self._on_stop:
                    self._on_stop()
                elif event == "toast_action" and self._on_toast_action:
                    self._on_toast_action(data.get("action", ""))
            except json.JSONDecodeError:
                pass

        self._log("[overlay] _read_stdout: ended (subprocess stdout closed)")

    def _read_stderr(self):
        """Read and log errors from subprocess stderr."""
        if not self._process or not self._process.stderr:
            return

        for line in self._process.stderr:
            line = line.strip()
            if line:
                self._log(f"[overlay STDERR] {line}")
