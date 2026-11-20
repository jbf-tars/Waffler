"""Meetings: Natter's recorder and notes, opened from inside Waffler.

Phase 0 of the Waffler + Natter merge (natter-notes/docs/WAFFLER-MERGE-PLAN.md).
Waffler does not import Natter's code. It starts Natter's own program without a
window (`Natter serve`), which runs the recorder, transcription and notes on a
private local port, and shows that page in a Waffler window. Dictation and the
hotkey are untouched.

The engine keeps running while Waffler is open, so closing the Meetings window
does not stop a recording. On quit, a recording still running is stopped and
saved first.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

TITLE = "Waffler — Meetings"


def candidates() -> list[Path]:
    """Places the Natter program may be, best first."""
    out: list[Path] = []
    env = os.environ.get("WAFFLER_NATTER_BIN")
    if env:
        out.append(Path(env))
    if sys.platform == "darwin":
        inner = Path("Natter.app") / "Contents" / "MacOS" / "Natter"
        exe = Path(sys.executable).resolve()
        # Shipped inside Waffler.app (Contents/Resources/Natter.app), once we bundle it.
        for parent in exe.parents:
            if parent.name == "Contents":
                out.append(parent / "Resources" / inner)
                break
        out += [Path("/Applications") / inner, Path.home() / "Applications" / inner]
    elif sys.platform == "win32":
        exe_dir = Path(sys.executable).resolve().parent
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        out += [exe_dir / "Natter" / "Natter.exe",
                local / "Programs" / "Natter" / "Natter.exe",
                Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Natter" / "Natter.exe"]
    return out


def find_binary() -> Path | None:
    for p in candidates():
        try:
            if p.is_file() and os.access(p, os.X_OK):
                return p
        except OSError:
            continue
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Meetings:
    """Owns the Natter engine process and the Meetings window."""

    def __init__(self, log=lambda msg: None):
        self._log = log
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._port = 0
        self._token = ""
        self._window = None

    # ── the engine ──────────────────────────────────────────────────────────
    def _call(self, method: str, path: str, timeout: float = 4.0):
        req = urllib.request.Request(f"http://127.0.0.1:{self._port}{path}", method=method,
                                     data=b"{}" if method != "GET" else None,
                                     headers={"Authorization": f"Bearer {self._token}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/?t={self._token}"

    def ensure_engine(self) -> str:
        """Starts the engine if it is not running. Returns "" or a plain-words problem."""
        with self._lock:
            if self.running():
                return ""
            binary = find_binary()
            if binary is None:
                return ("Meetings needs the Natter app. Put Natter in your Applications folder, "
                        "then try again.")
            self._port, self._token = _free_port(), secrets.token_urlsafe(24)
            try:
                self._proc = subprocess.Popen(
                    [str(binary), "serve", "--port", str(self._port), "--token", self._token],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            except OSError as e:
                self._proc = None
                return f"Meetings couldn't start ({e})."
            deadline = time.time() + 40             # a frozen app unpacks itself on first run
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    self._proc = None
                    return "Meetings stopped as soon as it started. Open Natter once on its own to check it works."
                try:
                    self._call("GET", "/api/state", timeout=1.5)
                    break
                except (urllib.error.URLError, OSError, ValueError):
                    time.sleep(0.3)
            else:
                self.shutdown()
                return "Meetings took too long to start. Try again."
            self._log(f"[meetings] engine up on port {self._port} ({binary})")
            self._share_keys()
            return ""

    def _share_keys(self) -> None:
        """Keys are entered once, in Waffler: Natter copies them if it has none of its own."""
        try:
            keys = self._call("GET", "/api/keys")
            if not any(v.get("set") for k, v in keys.items() if k in ("groq", "openai")):
                got = self._call("POST", "/api/keys/import-waffler")
                self._log(f"[meetings] keys shared from Waffler: {got.get('imported')}")
        except Exception as e:  # noqa: BLE001 — never block opening the window on this
            self._log(f"[meetings] key sharing skipped: {type(e).__name__}")

    # ── the window ──────────────────────────────────────────────────────────
    def open(self) -> dict:
        """Starts the engine if needed and shows the Meetings window."""
        problem = self.ensure_engine()
        if problem:
            self._log(f"[meetings] {problem}")
            return {"ok": False, "error": problem}
        import webview
        w = self._window
        if w is not None:
            try:
                w.show()
                w.restore()
                return {"ok": True}
            except Exception:  # noqa: BLE001 — the window was closed: make a new one
                self._window = None
        w = webview.create_window(TITLE, self.url(), width=1240, height=820, min_size=(760, 560),
                                  background_color="#FDFCFB", text_select=True)

        def closed():
            if self._window is w:
                self._window = None
        w.events.closed += closed
        self._window = w
        return {"ok": True}

    # ── quitting ────────────────────────────────────────────────────────────
    def shutdown(self) -> None:
        """Stops the engine. A recording still running is stopped and saved first."""
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if self._call("GET", "/api/state").get("recording"):
                    self._log("[meetings] stopping a running recording before quit")
                    self._call("POST", "/api/record/stop", timeout=20)
                    deadline = time.time() + 20      # let the last audio reach the disk
                    while time.time() < deadline and self._call("GET", "/api/state").get("pending_chunks"):
                        time.sleep(0.3)
            except Exception:  # noqa: BLE001
                pass
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None
