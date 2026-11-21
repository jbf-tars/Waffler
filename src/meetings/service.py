"""What the app can do, in one place: the API, the window and the tests all
drive this."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

from . import audioproc, llm
from .audioproc import RATE
from .paths import audio_dir, data_dir
from .recorder import CHUNK_MAX_S, CHUNK_MIN_S, Recorder, _Job, retranscribe
from .store import Store


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
        self.recorder = Recorder(self.store, source_factory=source_factory,
                                 transcribe_backend=transcribe_backend, on_idle=self._after_recording)
        self._enhancing: set[str] = set()
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()

    # ── sync ────────────────────────────────────────────────────────────────
    def sync_soon(self) -> None:
        """Sync with the hub in the background, if one is set up. Never raises."""
        def run():
            from . import keys, sync
            if not (self.store.settings().get("hub_url") and keys.get_key("hub")):
                return
            if not self._sync_lock.acquire(blocking=False):
                return
            try:
                sync.sync_with_hub(self.store)
            except Exception:
                pass                       # offline or hub down: the next round will catch up
            finally:
                self._sync_lock.release()
        threading.Thread(target=run, daemon=True, name="natter-sync").start()

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
        if not note:
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
            self._enhance(note_id)
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

    def _enhance(self, note_id: str) -> None:
        with self._lock:
            if note_id in self._enhancing:
                return
            self._enhancing.add(note_id)
        try:
            note = self.store.get_note(note_id)
            template = self.store.template(note["template_id"])
            title, notes = llm.write_notes(
                self.store.transcript_text(note_id), note["my_notes"], template["prompt"],
                self.store.settings(), backend=self.llm_backend, when=_when(note["created_at"]))
            fields = {"summary": notes, "status": "ready", "error": ""}
            if title and not note["title_locked"]:
                fields["title"] = title
            self.store.update_note(note_id, **fields)
        except Exception as e:  # noqa: BLE001
            msg = str(e) if isinstance(e, llm.LLMError) else f"Couldn't write notes ({type(e).__name__})."
            self.store.update_note(note_id, status="ready", error=msg)
        finally:
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
                retranscribe(self.store, note_id, backend=self.transcribe_backend)
                self._after_recording(note_id)
            except Exception as e:  # noqa: BLE001
                self.store.update_note(note_id, status="ready", error=str(e))
        threading.Thread(target=run, daemon=True).start()
        return self.store.get_note(note_id)

    def delete_audio(self, note_id: str) -> None:
        folder = audio_dir(note_id)                # refuses anything that is not a plain id
        root = (data_dir() / "audio").resolve()
        if folder.resolve().parent != root:        # only ever a folder directly under audio/
            raise ValueError("not a note's audio folder")
        shutil.rmtree(folder, ignore_errors=True)

    def has_audio(self, note_id: str) -> bool:
        return any(audio_dir(note_id).glob("*.flac"))

    def delete_note(self, note_id: str) -> None:
        self.store.delete_note(note_id)
        self.delete_audio(note_id)


def load_any_audio(path: Path) -> np.ndarray:
    """16 kHz mono from any common audio file; ffmpeg handles what soundfile can't (m4a)."""
    try:
        return audioproc.read_audio(path)
    except Exception:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            try:                                   # a server without ffmpeg: the pip-bundled one
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                ffmpeg = None
        if not ffmpeg:
            raise RuntimeError("This audio format needs ffmpeg, which isn't installed.") from None
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "a.flac"
            subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar",
                            str(RATE), str(out)], check=True, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return audioproc.read_audio(out)
