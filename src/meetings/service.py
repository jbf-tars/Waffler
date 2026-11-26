"""What the app can do, in one place: the API, the window and the tests all
drive this."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import numpy as np

from . import audio_decode, audioproc, llm, tidy_lines
from .audioproc import RATE
from .paths import audio_dir, data_dir, remove_audio
from .recorder import (CHUNK_MAX_S, CHUNK_MIN_S, WAITING, Recorder, _Job, finish_waiting, retranscribe,
                       waiting_files)
from .store import Store
from .transcribe import SPEECH_NEEDS_SETUP, TranscribeError


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%A %d %B %Y, %H:%M")
    except ValueError:
        return ""


class Service:
    def __init__(self, store: Store | None = None, source_factory=None,
                 transcribe_backend=None, llm_backend=None):
        self.store = store or Store()
        self.llm_backend = llm_backend
        self.transcribe_backend = transcribe_backend
        self.store.recover_interrupted()
        self._note_waiting()
        self.recorder = Recorder(self.store, source_factory=source_factory,
                                 transcribe_backend=transcribe_backend, on_idle=self._after_recording)
        self._enhancing: set[str] = set()
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()
        # Waffler's account (src/meetings_ctl.py): a function given the store
        # that syncs with the signed-in account, and does nothing when nobody
        # is signed in. The hub setting below is separate and still works.
        self.account_sync = None

    def _note_waiting(self) -> None:
        """A meeting whose audio was still waiting to be written down when
        Waffler closed (Waffler Plus takes a meeting once it has ended) says
        so, which is what offers to try again. Never raises."""
        try:
            for folder in {f.parent for f in (data_dir() / "audio").glob("*/s*_*.wait.flac")}:
                note = self.store.get_note(folder.name)
                if note and not note["deleted"] and not note["error"]:
                    self.store.update_note(note["id"], error=WAITING, touch=False)
        except Exception:
            pass

    # ── sync ────────────────────────────────────────────────────────────────
    def _sync_round(self) -> None:
        """One round with the account and with the hub, whichever is set up.
        Call with _sync_lock held. Never raises."""
        account = self.account_sync
        if account is not None:
            try:
                account(self.store)
            except Exception:
                pass                       # it keeps its own record of what went wrong
        try:
            from . import keys, sync
            if self.store.settings().get("hub_url") and keys.get_key("hub"):
                sync.sync_with_hub(self.store)
        except Exception:
            pass                           # offline or hub down: the next round will catch up

    def sync_soon(self) -> None:
        """Sync in the background with the account and the hub, whichever is
        set up. Returns at once and never raises: recording never waits on it."""
        def run():
            if not self._sync_lock.acquire(blocking=False):
                return                     # a round is running: it will carry this change, or the next will
            try:
                self._sync_round()
            finally:
                self._sync_lock.release()
        threading.Thread(target=run, daemon=True, name="natter-sync").start()

    def sync_now(self, wait_s: float = 90.0) -> bool:
        """A round now, on the caller's thread ("Sync now"). False when a
        round already running did not finish in `wait_s`."""
        if not self._sync_lock.acquire(timeout=wait_s):
            return False
        try:
            self._sync_round()
        finally:
            self._sync_lock.release()
        return True

    def start_auto_sync(self, every_s: float = 120.0) -> None:
        def loop():
            while True:
                self.sync_soon()
                threading.Event().wait(every_s)
        threading.Thread(target=loop, daemon=True, name="natter-autosync").start()

    # ── recording ───────────────────────────────────────────────────────────
    def start_recording(self, note_id: str | None = None, template_id: str | None = None) -> dict:
        """Start a new note, or resume an existing one (new audio continues its transcript)."""
        if self.recorder.state()["recording"]:
            raise RuntimeError("Already recording.")
        if note_id:
            note = self.store.get_note(note_id)
            if not note or note["deleted"]:
                raise KeyError(note_id)
        else:
            note = self.store.create_note(template_id=template_id or "voice-note")
        try:
            self.recorder.start(note["id"])
        except Exception:
            if not note_id:                      # don't leave an empty note behind
                with self.store._write:
                    self.store.db.execute("DELETE FROM notes WHERE id=?", (note["id"],))
                    self.store.db.execute("DELETE FROM notes_fts WHERE note_id=?", (note["id"],))
            raise
        return self.store.get_note(note["id"])

    def stop_recording(self) -> dict:
        note_id = self.recorder.state()["note_id"]
        self.recorder.stop()
        return self.store.get_note(note_id) if note_id else {}

    def _after_recording(self, note_id: str) -> None:
        note = self.store.get_note(note_id)
        if not note or note["deleted"]:
            # Deleted while it was still being written down: what the
            # recorder saved since then goes too.
            self.delete_audio(note_id)
            return
        has_text = bool(self.store.segments(note_id))
        settings = self.store.settings()
        if has_text and settings.get("auto_enhance", True):
            if self.llm_backend is None and not llm.can_write(settings):
                # No key (Waffler used on this computer only, say): the
                # meeting is finished with its transcript and one plain line
                # where the notes would be. Nothing is asked, nothing fails,
                # and no transcription error already noted is covered up.
                note = self.store.get_note(note_id) or {}
                self.store.update_note(note_id, status="ready",
                                       error=note.get("error") or llm.NOTES_NEED_KEY)
                self.sync_soon()
                return
            self._enhance(note_id, written_down=True)
        else:
            self.store.update_note(note_id, status="ready")

    def state(self) -> dict:
        return self.recorder.state()

    # ── notes from audio already recorded (import, phone upload) ────────────
    def ingest_audio(self, note_id: str, audio: np.ndarray, source: str = "mic") -> None:
        session = self.store.start_session(note_id)
        self.store.update_note(note_id, status="processing", error="")
        pos, total = 0, len(audio)
        rec = self.recorder
        rec._finishing.add(note_id)
        while pos < total:
            window = audio[pos:pos + int(CHUNK_MAX_S * RATE)]
            n = len(window) if len(window) < CHUNK_MAX_S * RATE else \
                audioproc.quiet_cut(window, CHUNK_MIN_S, CHUNK_MAX_S)
            with rec._pending_lock:
                rec._pending += 1
            rec._jobs.put(_Job(note_id, session["n"], session["offset_s"] + pos / RATE,
                               {source: audio[pos:pos + n]}))
            pos += n
        self.store.end_session(session["id"], note_id, session["offset_s"] + total / RATE)
        rec._jobs.put(("done", note_id))

    def ingest_file(self, path: str | Path, note_id: str | None = None, title: str = "") -> dict:
        """Any audio file → a note (or more audio on an existing note)."""
        audio = load_any_audio(Path(path))
        note = self.store.get_note(note_id) if note_id else self.store.create_note(
            title=title, template_id="voice-note")
        if not note:
            raise KeyError(note_id)
        self.ingest_audio(note["id"], audio)
        return self.store.get_note(note["id"])

    # ── AI ──────────────────────────────────────────────────────────────────
    def enhance(self, note_id: str, template_id: str | None = None, wait: bool = False) -> dict:
        note = self.store.get_note(note_id)
        if not note:
            raise KeyError(note_id)
        if template_id:
            self.store.update_note(note_id, template_id=template_id)
        self.store.update_note(note_id, status="processing", error="", touch=False)
        if wait:
            self._enhance(note_id)
        else:
            threading.Thread(target=self._enhance, args=(note_id,), daemon=True).start()
        return self.store.get_note(note_id)

    def _ask_writer(self, settings: dict, system: str, user: str, fall_back: bool = True) -> str:
        """One reply from whatever writes this meeting's notes (a key,
        Waffler Plus, the model on this computer), with no chance in its
        choice of words where the writer has that setting."""
        return llm.complete(system, [{"role": "user", "content": user}], settings, max_tokens=8000,
                            backend=self.llm_backend, effort="low", background=True, temperature=0.0,
                            fall_back=fall_back)

    def _lines_after_notes(self) -> bool:
        """Is the writer the model on this computer? It is slow where there
        is no graphics card (measured on a PC: about two minutes for sixty
        lines, a quarter of an hour for an hour's meeting), so there the
        notes are written first and the lines tidied behind them. Any other
        writer takes a second or two a request and goes first, so the notes
        are written from the tidied lines."""
        if self.llm_backend is not None:
            return False
        try:
            return llm.resolve_provider(self.store.settings()) == "local"
        except Exception:  # noqa: BLE001
            return False

    def _other_writer_now(self) -> bool:
        """Is something other than Waffler Plus the writer just now (Plus
        resting after a fault on its side, with a writer here)?"""
        try:
            return self.llm_backend is None and llm.resolve_provider(self.store.settings()) != "plus"
        except Exception:  # noqa: BLE001
            return False

    def _tidy_lines(self, note_id: str, fall_back: bool = True) -> bool:
        """Capitals and punctuation for the transcript's lines, by the notes'
        writer, and never a changed word (tidy_lines.py). With no writer
        nobody is asked. Never raises: the notes come next whatever happens.

        True when it stopped because Waffler Plus failed on its own side:
        with `fall_back` False that is not passed to another writer here
        (the model on this computer can take a quarter of an hour over an
        hour's lines, and the notes come first), and the lines can be tidied
        again after the notes, by the writer Plus is resting for."""
        down = []

        def write(system, user):
            try:
                return self._ask_writer(settings, system, user, fall_back=fall_back)
            except llm.LLMError as e:
                if e.kind == "plus_down":
                    down.append(e)
                raise
        try:
            settings = self.store.settings()
            if self.llm_backend is None and not llm.can_write(settings):
                return False
            tidy_lines.tidy(self.store.segments(note_id), write,
                            lambda changes: self.store.retext_segments(note_id, changes))
        except Exception:  # noqa: BLE001
            pass
        return bool(down)

    def _enhance(self, note_id: str, written_down: bool = False) -> None:
        """`written_down`: the transcript has just been made (a recording
        ended, a file came in, it was written down again)."""
        with self._lock:
            if note_id in self._enhancing:
                return
            self._enhancing.add(note_id)
        try:
            if waiting_files(note_id):
                # Audio that has not been written down yet (a part Waffler
                # Plus did not take): that first, then notes from all of it.
                finish_waiting(self.store, note_id, backend=self.transcribe_backend,
                               progress=self.recorder.say_part(note_id))
                written_down = True
            note = self.store.get_note(note_id)
            # The lines are tidied once, when the transcript is new, or with
            # the meeting's first notes (written down before there was a
            # writer). Not each time the notes are written again: a line that
            # runs on still starts small after it, and would be sent for ever.
            tidy = written_down or not note["summary"]
            after = tidy and self._lines_after_notes()
            plus_stopped = False
            if tidy and not after:
                # Plus failing here on its own side: the lines wait for the
                # notes, then go to the writer Plus rests for (below).
                plus_stopped = self._tidy_lines(note_id, fall_back=False)
            template = self.store.template(note["template_id"])
            title, notes = llm.write_notes(
                self.store.transcript_text(note_id), note["my_notes"], template["prompt"],
                self.store.settings(), backend=self.llm_backend, when=_when(note["created_at"]))
            fields = {"summary": notes, "status": "ready", "error": ""}
            if title and not note["title_locked"]:
                fields["title"] = title
            self.store.update_note(note_id, **fields)
            if after or (plus_stopped and self._other_writer_now()):
                self._tidy_lines(note_id)
        except Exception as e:  # noqa: BLE001
            if isinstance(e, llm.LLMError) and e.kind == "plus_down":
                msg = llm.NOTES_PLUS_DOWN          # nothing else here writes: the transcript waits
            elif isinstance(e, (llm.LLMError, TranscribeError)):
                msg = str(e)
            else:
                msg = f"Couldn't write notes ({type(e).__name__})."
            self.store.update_note(note_id, status="ready", error=msg)
        finally:
            self.recorder.progress.pop(note_id, None)
            with self._lock:
                self._enhancing.discard(note_id)
            self.sync_soon()

    def ask(self, question: str, note_id: str | None = None, history: list | None = None,
            extra: str = "", on_text=None) -> dict:
        """`extra`: more material to answer from, besides the notes (Waffler
        passes the person's dictations when they ask across everything)."""
        if note_id:
            note = self.store.get_note(note_id)
            if not note:
                raise KeyError(note_id)
            context = self._note_material(note)
            sources = [{"id": note["id"], "title": note["title"]}]
        else:
            hits = self.store.list_notes(query=question, limit=6)
            if len(hits) < 6:
                seen = {h["id"] for h in hits}
                hits += [n for n in self.store.list_notes(limit=12) if n["id"] not in seen][:6 - len(hits)]
            notes = [self.store.get_note(h["id"]) for h in hits]
            context = "\n\n".join(self._note_material(n, transcript=len(notes) <= 3) for n in notes)
            sources = [{"id": n["id"], "title": n["title"]} for n in notes]
        settings = self.store.settings()
        if self.llm_backend is None and not llm.can_write(settings):
            raise llm.LLMError(llm.ASK_NEEDS_KEY, "no_key")
        answer = llm.ask(question, context, history or [], settings,
                         backend=self.llm_backend, extra="" if note_id else extra,
                         on_text=on_text)
        return {"answer": answer, "sources": sources}

    def _note_material(self, note: dict, transcript: bool = True) -> str:
        parts = [f"## Note: {note['title'] or 'Untitled'} ({_when(note['created_at'])})"]
        if note["summary"]:
            parts.append("Notes:\n" + note["summary"])
        if note["my_notes"].strip():
            parts.append("Typed by me:\n" + note["my_notes"])
        text = self.store.transcript_text(note["id"])
        if text and (transcript or not note["summary"]):
            parts.append("Transcript:\n" + text)
        return "\n\n".join(parts)

    def retranscribe(self, note_id: str) -> dict:
        self.store.update_note(note_id, status="processing", error="", touch=False)

        def run():
            try:
                retranscribe(self.store, note_id, backend=self.transcribe_backend,
                             progress=self.recorder.say_part(note_id))
                self.recorder.progress.pop(note_id, None)      # written down: the notes are next
                self._after_recording(note_id)
            except Exception as e:  # noqa: BLE001
                # Nothing set up to write it down is a state, not a failure
                # (as for an import): the audio is kept, the page says what to do.
                setup = isinstance(e, TranscribeError) and e.kind in ("no_key", "no_local")
                self.store.update_note(note_id, status="ready", error=SPEECH_NEEDS_SETUP if setup else str(e))
            finally:
                self.recorder.progress.pop(note_id, None)
        threading.Thread(target=run, daemon=True).start()
        return self.store.get_note(note_id)

    def delete_audio(self, note_id: str) -> None:
        remove_audio(note_id)                      # refuses anything that is not a plain id

    def has_audio(self, note_id: str) -> bool:
        return any(audio_dir(note_id).glob("*.flac"))

    def delete_note(self, note_id: str) -> None:
        """Final: the store removes the content and leaves a tombstone, and
        the audio goes. The next sync tells the person's other devices."""
        self.store.delete_note(note_id)
        self.delete_audio(note_id)


def load_any_audio(path: Path) -> np.ndarray:
    """16 kHz mono float32 from any common audio file. soundfile reads wav,
    flac, aiff, ogg and mp3; everything else (m4a above all) goes through
    src/meetings/audio_decode.py, which raises an audio_decode.DecodeError
    (a RuntimeError) with a plain sentence when it cannot."""
    try:
        return audioproc.read_audio(path)
    except Exception:
        samples, rate = audio_decode.decode(path)
        return samples if rate == RATE else audioproc.resample(samples, rate)
