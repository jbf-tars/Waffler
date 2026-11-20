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
TOGGLE_GAP_S = 1.5        # a second toggle sooner than this is the same gesture again


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


def dictations_material(history: list, question: str, limit_chars: int = 16000) -> str:
    """The person's dictations as material for Ask: those that share a word
    with the question first, then the most recent, newest first, up to a size
    a model reads comfortably. Entries that were never sent, and transforms
    and commands, are left out. Returns "" when there is nothing to give."""
    import re
    items = [h for h in (history or []) if isinstance(h, dict) and not h.get("failed")
             and h.get("kind") not in ("transform", "command")]

    def text_of(h):
        return str(h.get("edited_text") or h.get("styled") or h.get("text") or "").strip()
    words = set(re.findall(r"[a-z0-9']{4,}", (question or "").lower()))
    recent = sorted(items, key=lambda h: str(h.get("timestamp") or ""), reverse=True)
    hits = [h for h in recent if words and any(w in text_of(h).lower() for w in words)]
    chosen, seen, used = [], set(), 0
    for h in hits[:60] + recent[:80]:
        body = text_of(h)
        key = h.get("id") or (h.get("timestamp"), body[:40])
        if key in seen or not body:
            continue
        where = f" in {h.get('app')}" if h.get("app") else ""
        line = f"- {str(h.get('timestamp') or '')[:16].replace('T', ' ')}{where}: {body}"
        if used + len(line) > limit_chars:
            break
        seen.add(key)
        chosen.append(line)
        used += len(line)
    if not chosen:
        return ""
    return ("## Dictations (things the owner said and had typed, newest first)\n"
            + "\n".join(chosen))


class MeetingsController:
    def __init__(self, log=lambda msg: None, on_change=lambda: None):
        self._log = log
        self._on_change = on_change        # called when the list or the recording state changes
        self._lock = threading.Lock()
        self._service = None
        self._watching = False
        self._toggle_lock = threading.Lock()
        self._toggled_at = 0.0

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
        """Start a meeting, or finish the one running. `started` says which.

        One at a time, and not twice in quick succession: a burst of double
        taps (or a tap and a click) is one decision, not three. A toggle that
        arrives within TOGGLE_GAP_S of the last is ignored (`ignored`: True)."""
        if not self._toggle_lock.acquire(blocking=False):
            return {"ok": True, "ignored": True, **self.state()}
        try:
            if time.time() - self._toggled_at < TOGGLE_GAP_S:
                return {"ok": True, "ignored": True, **self.state()}
            if self.state()["recording"]:
                result = {**self.stop(), "started": False}
            else:
                result = {**self.start(), "started": True}
            self._toggled_at = time.time()
            return result
        finally:
            self._toggle_lock.release()

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
        if not svc.store.get_note(note_id):        # only a meeting that exists (an id is never a path)
            return
        if svc.state().get("note_id") == note_id:
            svc.stop_recording()
        svc.delete_note(note_id)
        self._on_change()

    def ask(self, question: str, note_id: str | None = None, history: list | None = None,
            extra: str = "") -> dict:
        return self._svc().ask(question, note_id, history or [], extra=extra)

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
