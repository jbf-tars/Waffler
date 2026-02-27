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

        # Resolve the subprocess script path
        self._script = Path(__file__).parent / _OVERLAY_SCRIPT_NAME

    # ── Public API ────────────────────────────────────────────────────

    def show(self):
        """Show the recording overlay."""
        if self._is_alive():
            self._send({"type": "show"})
            self._visible = True
            return

        self._start_process()
        time.sleep(0.35)  # let subprocess initialise
        self._send({"type": "show"})
        self._visible = True

    def hide(self):
        """Hide the overlay (subprocess stays alive for reuse)."""
        if self._is_alive():
            self._send({"type": "hide"})
        self._visible = False

    def update_level(self, level: float):
        """
        Push an audio RMS level (0.0-1.0) to animate the VU bars.
        Safe to call from any thread at any rate.
        """
        if self._is_alive():
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
        if self._is_alive():
            self._send({
                "type": "show_toast",
                "style": style,
                "heading": heading,
                "body": body,
            })

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
        # In frozen apps, sys.executable is the bundled binary — not Python.
        # We need an actual Python interpreter to run the overlay script.
        if getattr(sys, 'frozen', False):
            import shutil
            # Try common Python interpreter names (including Windows py launcher)
            for name in ('python3', 'python', 'py'):
                path = shutil.which(name)
                if path:
                    print(f"[overlay] Found Python: {path}")
                    return path
            # No Python found — return None so caller can handle it
            print("[overlay] WARNING: No Python interpreter found in PATH")
            return None
        return sys.executable

    def _start_process(self):
        """Launch the overlay subprocess."""
        if not self._script.exists():
            print(f"[overlay] WARNING: script not found: {self._script}", flush=True)
            return

        python = self._find_python()
        if python is None:
            print("[overlay] Cannot start overlay: no Python interpreter available")
            return

        try:
            # Use CREATE_NO_WINDOW on Windows to avoid a console flash
            kwargs = {}
            if _PLATFORM == "Windows":
                CREATE_NO_WINDOW = 0x08000000
                kwargs["creationflags"] = CREATE_NO_WINDOW
        except Exception:
            kwargs = {}

        self._process = subprocess.Popen(
            [python, str(self._script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            **kwargs,
        )
        self._reader_thread = threading.Thread(
            target=self._read_stdout,
            daemon=True,
            name="OverlayReader",
        )
        self._reader_thread.start()

    def _is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _send(self, data: dict):
        """Write a JSON command to the subprocess stdin."""
        if not self._is_alive():
            return
        try:
            self._process.stdin.write(json.dumps(data) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _read_stdout(self):
        """Read event callbacks from subprocess stdout."""
        if not self._process or not self._process.stdout:
            return
        for line in self._process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                data  = json.loads(line)
                event = data.get("event")
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
