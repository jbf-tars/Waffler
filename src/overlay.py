"""
Waffler - Floating Recording Overlay
Spawns overlay subprocess using frozen app's --overlay flag.
Uses bundled Python and PyObjC - fully self-contained.

Main API: RecordingOverlay().show() / .hide() / .update_level(0..1)
"""

import subprocess
import sys
import json
import time
import threading
import platform

_PLATFORM = platform.system()  # "Darwin", "Windows", "Linux"


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

    # ── Public API ────────────────────────────────────────────────────

    def prestart(self):
        """Pre-start the overlay subprocess so first show() is instant."""
        if self._is_alive():
            return
        self._log("[overlay] Pre-starting subprocess...")
        self._start_process()
        if self._is_alive():
            self._log(f"[overlay] ✓ Pre-started, PID={self._process.pid}")
        else:
            self._log("[overlay] Pre-start failed (will retry on first show)")

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

    def _start_process(self):
        """Launch the overlay subprocess using frozen app's --overlay mode."""
        self._log(f"[overlay] _start_process called")

        # Use CREATE_NO_WINDOW on Windows to avoid a console flash
        kwargs = {}
        if _PLATFORM == "Windows":
            CREATE_NO_WINDOW = 0x08000000
            kwargs["creationflags"] = CREATE_NO_WINDOW

        self._log(f"[overlay] Starting subprocess: {sys.executable} --overlay")

        try:
            self._process = subprocess.Popen(
                [sys.executable, '--overlay'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
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

            # First attempt: no delay. Retries: exponential backoff 0.5s, 1s
            if self._restart_count > 1:
                delay = 0.5 * (2 ** (self._restart_count - 2))
                self._log(f"[overlay] Restarting subprocess in {delay}s (attempt {self._restart_count}/3)")
                time.sleep(delay)
            else:
                self._log(f"[overlay] Starting subprocess (attempt 1/3)")

            self._start_process()
            success = self._is_alive()

            if success:
                self._log(f"[overlay] ✓ Subprocess restarted successfully, PID={self._process.pid}")
            else:
                self._log(f"[overlay] ✗ Subprocess restart failed")

            return success

    def _send(self, data: dict) -> bool:
        """Write a JSON command to the subprocess stdin (thread-safe).

        Auto-restarts the subprocess on broken pipe (e.g. after sleep/wake).

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
                self._log(f"[overlay] ⚠️  Broken pipe during send: {e} — restarting subprocess")
                # Kill the frozen process and restart
                try:
                    self._process.kill()
                except Exception:
                    pass
                self._process = None

        # Restart outside the send_lock to avoid deadlock
        self._log("[overlay] Auto-restarting after broken pipe...")
        self._start_process()
        if self._is_alive():
            self._log("[overlay] ✓ Auto-restart successful")
            # Retry the original command
            with self._send_lock:
                try:
                    self._process.stdin.write(json.dumps(data) + "\n")
                    self._process.stdin.flush()
                    return True
                except Exception:
                    return False
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
