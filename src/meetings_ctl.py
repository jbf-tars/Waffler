"""Meetings, as the rest of Waffler sees them.

One object in front of the engine in src/meetings (recorder, store,
transcription, notes). The engine is made on first use, so opening Waffler
costs nothing until someone records or opens the Meetings page.

Tap the hotkey twice: `toggle()` starts a recording, or finishes the one
running. Dictation is untouched and still works while a meeting records.
"""
from __future__ import annotations

import os
import threading
import time

_KEY_NAMES = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def _share_waffler_keys() -> None:
    """Keys are entered once, in Waffler. The engine reads them from the
    environment, so copy in any that are not there yet. Never logged."""
    try:
        from src.data_paths import data_file
    except ImportError:
        from data_paths import data_file
    env = data_file(".env")
    try:
        lines = env.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        name, sep, value = line.strip().partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if sep and name in _KEY_NAMES and value and not value.lower().startswith("your_") \
                and not os.environ.get(name):
            os.environ[name] = value


class MeetingsController:
    def __init__(self, log=lambda msg: None, on_change=lambda: None):
        self._log = log
        self._on_change = on_change        # called when the list or the recording state changes
        self._lock = threading.Lock()
        self._service = None
        self._watching = False

    # ── the engine ──────────────────────────────────────────────────────────
    def _svc(self):
        with self._lock:
            if self._service is None:
                # The engine's own key store is the system keychain under another
                # app's name: Waffler's keys (the environment) are the only source.
                os.environ.setdefault("NATTER_NO_KEYRING", "1")
                _share_waffler_keys()
                try:
                    from src.meetings.service import Service
                except ImportError:
                    from meetings.service import Service
                t = time.time()
                self._service = Service()
                self._log(f"[meetings] engine ready in {time.time() - t:.1f}s")
            return self._service

    # ── recording ───────────────────────────────────────────────────────────
    def state(self) -> dict:
        if self._service is None:
            return {"recording": False, "note_id": None, "elapsed_s": 0, "sources": [], "warnings": []}
        s = self._service.state()
        return {"recording": bool(s.get("recording")), "note_id": s.get("note_id"),
                "elapsed_s": s.get("elapsed_s") or 0, "sources": s.get("sources") or [],
                "warnings": s.get("warnings") or []}

    def start(self) -> dict:
        svc = self._svc()
        if svc.state().get("recording"):
            return {"ok": True, **self.state()}
        try:
            note = svc.start_recording(template_id="general")
        except Exception as e:  # noqa: BLE001 — a plain sentence for the person, the type for the log
            self._log(f"[meetings] start failed: {type(e).__name__}: {e}")
            return {"ok": False, "error": str(e) or "The recording couldn't start.", **self.state()}
        self._log(f"[meetings] recording started ({note['id']})")
        self._on_change()
        return {"ok": True, **self.state()}

    def stop(self) -> dict:
        svc = self._svc()
        if not svc.state().get("recording"):
            return {"ok": True, "note_id": None, **self.state()}
        note = svc.stop_recording()
        note_id = note["id"] if note else None
        self._log(f"[meetings] recording finished ({note_id})")
        self._on_change()
        if note_id:
            self._watch(note_id)
        return {"ok": True, **self.state(), "note_id": note_id}

    def toggle(self) -> dict:
        """Start a meeting, or finish the one running. `started` says which."""
        if self.state()["recording"]:
            return {**self.stop(), "started": False}
        return {**self.start(), "started": True}

    def _watch(self, note_id: str) -> None:
        """Tell the page when the notes are written (or could not be)."""
        def run():
            deadline = time.time() + 900
            while time.time() < deadline:
                time.sleep(1.5)
                try:
                    note = self._service.store.get_note(note_id)
                except Exception:  # noqa: BLE001
                    return
                if not note or note.get("status") != "processing":
                    break
            self._on_change()
        threading.Thread(target=run, daemon=True, name="meetings-watch").start()

    # ── notes ───────────────────────────────────────────────────────────────
    def list(self, query: str = "") -> list[dict]:
        return self._svc().store.list_notes(query=query or "")

    def get(self, note_id: str) -> dict | None:
        svc = self._svc()
        note = svc.store.get_note(note_id, with_segments=True)
        if not note or note.get("deleted"):
            return None
        note["has_audio"] = svc.has_audio(note_id)
        return note

    def rename(self, note_id: str, title: str) -> dict | None:
        self._svc().store.update_note(note_id, title=title.strip(), title_locked=1 if title.strip() else 0)
        self._on_change()
        return self.get(note_id)

    def delete(self, note_id: str) -> None:
        svc = self._svc()
        if svc.state().get("note_id") == note_id:
            svc.stop_recording()
        svc.delete_note(note_id)
        self._on_change()

    def ask(self, question: str, note_id: str | None = None, history: list | None = None) -> dict:
        return self._svc().ask(question, note_id, history or [])

    # ── quitting ────────────────────────────────────────────────────────────
    def shutdown(self) -> None:
        """A recording still running is stopped and saved before Waffler quits."""
        svc = self._service
        if svc is None:
            return
        try:
            if svc.state().get("recording"):
                self._log("[meetings] stopping a running recording before quit")
                svc.stop_recording()
                deadline = time.time() + 20
                while time.time() < deadline and svc.recorder.busy(""):
                    time.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            self._log(f"[meetings] shutdown: {type(e).__name__}")
