"""Meetings, as the rest of Waffler sees them.

One object in front of the engine in src/meetings (recorder, store,
transcription, notes). The engine is made on first use, so opening Waffler
costs nothing until someone records or opens the Meetings page.

Tap the hotkey twice: `toggle()` starts a recording, or finishes the one
running. Dictation is untouched and still works while a meeting records.

Signed in to an account (src/account.py), the meetings sync with it: soon
after a meeting's notes are written, when Waffler opens, and every couple of
minutes. The engine's own sync does the work (its sync_soon and
start_auto_sync); `_account_round` is what it runs. Sync is always on a
thread of its own, so a recording or a dictation never waits on it, and a
round that fails is logged in a word and tried again at the next one.
"""
from __future__ import annotations

import importlib
import os
import threading
import time

_KEY_NAMES = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
TOGGLE_GAP_S = 1.5        # a second toggle sooner than this is the same gesture again
SYNC_EVERY_S = 120.0      # signed in: how often the meetings sync with the account
ACCOUNT_CURSORS = "account_sync_cursors"      # the engine's settings: how far sync has got, per account
ACCOUNT_LAST_SYNCED = "account_last_synced"   # ... and when it last went through
SYNC_BUSY = "A sync is still running. Try again in a moment."


def _account():
    try:
        from src import account
    except ImportError:
        import account
    return account


def _engine(name: str):
    """A module of the meetings engine, the same copy the Service came from."""
    try:
        return importlib.import_module(f"src.meetings.{name}")
    except ImportError:
        return importlib.import_module(f"meetings.{name}")


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
    def __init__(self, log=lambda msg: None, on_change=lambda: None,
                 on_account_change=lambda: None):
        self._log = log
        self._on_change = on_change        # called when the list or the recording state changes
        self._on_account_change = on_account_change   # sync started, ended, or the account signed this computer out
        self._sync = {"syncing": False, "last_synced_at": "", "error": ""}
        self._sync_scope = ""              # the account `last_synced_at` belongs to
        self._sync_lock = threading.Lock()
        self._auto_sync = False
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
                self._service.account_sync = self._account_round
                self._log(f"[meetings] engine ready in {time.time() - t:.1f}s")
            return self._service

    # ── the account: the meetings sync with it ──────────────────────────────
    def _set_sync(self, **changes) -> None:
        with self._sync_lock:
            self._sync.update(changes)
        try:
            self._on_account_change()
        except Exception:  # noqa: BLE001
            pass

    def sync_state(self) -> dict:
        """{"syncing", "last_synced_at", "error"} for the Account page."""
        with self._sync_lock:
            return dict(self._sync)

    def _account_round(self, store) -> None:
        """One sync with the account this computer is signed in to. The
        engine calls this (sync_soon, start_auto_sync, sync_now), one round at
        a time, never on a thread that records. Signed out, it does nothing.
        Never raises; the log gets a word about a failure, never a secret."""
        acct = _account()
        cred = acct.sync_credentials()
        if not cred:
            return
        if self._sync_scope != cred["scope"]:          # the first round, or another account
            last = store.settings().get(ACCOUNT_LAST_SYNCED) or {}
            self._sync_scope = cred["scope"]
            with self._sync_lock:
                self._sync["last_synced_at"] = last.get("at", "") if last.get("scope") == cred["scope"] else ""
        before = self.sync_state()["error"]
        self._set_sync(syncing=True)
        error, done = "", {}
        try:
            sync = _engine("sync")
            try:
                client = acct.http_client(cred["server"], acct.SYNC_TIMEOUT_S)
                try:
                    out = sync.sync_with_hub(store, client=client, token=cred["token"],
                                             cursors_key=ACCOUNT_CURSORS, scope=cred["scope"])
                finally:
                    client.close()
                at = _engine("store").now_iso()
                store.set_settings({ACCOUNT_LAST_SYNCED: {"scope": cred["scope"], "at": at}})
                done = {"last_synced_at": at}
                if out["pulled"] or out["pushed"] or before:
                    self._log(f"[account] synced: {out['pulled']} in, {out['pushed']} out")
                if out["pulled"]:
                    self._on_change()                  # meetings from another device are in the list
            except sync.SyncError as e:
                error = acct.sync_refusal(e.status, e.detail, e.code)
                if error != before:                    # said once, not every two minutes
                    answer = f"answer {e.status}" if e.status else "no answer"
                    self._log(f"[account] sync did not go through ({answer}); it will be tried again")
        except Exception as e:  # noqa: BLE001
            error = acct.FAILED
            if error != before:
                self._log(f"[account] sync failed: {type(e).__name__}")
        finally:
            self._set_sync(syncing=False, error=error, **done)

    def start_sync(self, every_s: float = SYNC_EVERY_S) -> None:
        """Signed in: sync now and every couple of minutes from here on.
        Asked again (signed in a second time), it syncs now and leaves the
        one loop running."""
        svc = self._svc()
        with self._sync_lock:
            running, self._auto_sync = self._auto_sync, True
        if running:
            svc.sync_soon()
        else:
            svc.start_auto_sync(every_s)

    def sync_now(self) -> dict:
        """ "Sync now": a round, waited for. {"ok", "sync"}, with "error"
        (one sentence) when it did not go through."""
        if not self._svc().sync_now():
            return {"ok": False, "error": SYNC_BUSY, "reason": "busy", "sync": self.sync_state()}
        state = self.sync_state()
        if state["error"]:
            return {"ok": False, "error": state["error"], "reason": "sync", "sync": state}
        return {"ok": True, "sync": state}

    def account_signed_out(self) -> None:
        """Signed out: nothing is left to say about syncing."""
        self._sync_scope = ""
        self._set_sync(syncing=False, last_synced_at="", error="")

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
            extra: str = "", on_text=None) -> dict:
        return self._svc().ask(question, note_id, history or [], extra=extra, on_text=on_text)

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
