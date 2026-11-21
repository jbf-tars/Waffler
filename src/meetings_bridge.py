"""The Meetings page's calls, answered without a web server.

The Meetings tab shows the meetings interface (ui/meetings-app, built from
Natter's) in a frame. That interface speaks a small REST-like API; in Waffler
each call arrives through the pywebview bridge (Api.meetings_api in app.py)
and is answered here, straight from the engine in src/meetings. The routes and
their answers mirror Natter's natter/api.py, minus what has no place in
Waffler: keys are Waffler's own (read-only here), and there is no hub sync,
phone pairing or connector on this page.

`handle` never raises: it returns {"status": <http-like code>, "body": <json>}.
"""
from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

_NOTE_FIELDS = ("title", "my_notes", "summary", "template_id", "folder_id")
MAX_IMPORT_BYTES = 400 * 1024 * 1024


class _Reply(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _theme(waffler_theme: str) -> str:
    return {"dark": "dark", "cream": "light"}.get((waffler_theme or "").lower(), "auto")


def _notes_need_key() -> str:
    try:
        from src.meetings import llm
    except ImportError:
        from meetings import llm
    return llm.NOTES_NEED_KEY


def handle(ctl, method: str, path: str, body=None, waffler_theme: str = "") -> dict:
    """One call from the page. `ctl` is the MeetingsController."""
    try:
        return {"status": 200, "body": _route(ctl, (method or "GET").upper(), path or "", body, waffler_theme)}
    except _Reply as r:
        return {"status": r.status, "body": {"detail": r.detail}}
    except KeyError:
        return {"status": 404, "body": {"detail": "That note no longer exists."}}
    except Exception as e:  # noqa: BLE001 — the page shows the sentence; the type goes to the log
        ctl._log(f"[meetings] {method} {str(path).split('?')[0]} failed: {type(e).__name__}: {e}")
        from_llm = type(e).__name__ in ("LLMError", "TranscribeError")
        return {"status": 400 if from_llm else 500,
                "body": {"detail": str(e) if from_llm and str(e) else "Something went wrong."}}


def _route(ctl, method: str, raw: str, body, waffler_theme: str):
    svc = ctl._svc()
    store = svc.store
    parts = urlsplit(raw)
    query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    seg = [unquote(s) for s in parts.path.strip("/").split("/") if s]
    body = body if isinstance(body, dict) else {}

    def note_or_404(note_id: str, segments: bool = False) -> dict:
        note = store.get_note(note_id, with_segments=segments)
        if not note or note["deleted"]:
            raise _Reply(404, "That note no longer exists.")
        return note

    def full(note_id: str) -> dict:
        note = note_or_404(note_id, segments=True)
        note["has_audio"] = svc.has_audio(note_id)
        # A meeting that has its transcript and nothing to write the notes
        # with is not a problem to report. The page says so itself in one
        # quiet line, from `local` and the keys (GET /app), and stops saying
        # it when a key or the writing model arrives. The engine's sentence
        # stays in the store for everything else that reads it.
        if note.get("error") == _notes_need_key():
            note["error"] = ""
        return note

    def state() -> dict:
        return svc.state()

    # ── app ─────────────────────────────────────────────────────────────────
    if seg == ["app"] and method == "GET":
        try:
            from src.meetings import keys, llm, transcribe, __version__
            from src.meetings.recorder import SystemSource
        except ImportError:
            from meetings import keys, llm, transcribe, __version__
            from meetings.recorder import SystemSource
        settings = dict(store.settings())
        # What this computer does by itself, with no key: the page records
        # when there is a speech key or `speech`, and offers to write notes
        # when there is a writing key or `writing`. Two booleans, nothing else.
        local = {"speech": transcribe.local_ready(settings), "writing": llm.local_ready()}
        settings["theme"] = _theme(waffler_theme)            # the page follows Waffler's theme
        return {"name": "Waffler", "version": __version__, "settings": settings, "keys": keys.status(),
                "local": local,
                "local_whisper_available": False, "key_store": "waffler",
                "system_audio_supported": SystemSource.supported(), "waffler_keys_available": False,
                "data_dir": "", "templates": store.templates(), "folders": store.folders(), "state": state()}
    if seg == ["state"] and method == "GET":
        return state()
    if seg == ["settings"] and method == "PUT":
        try:
            from src.meetings.store import DEFAULT_SETTINGS
        except ImportError:
            from meetings.store import DEFAULT_SETTINGS
        allowed = {k: v for k, v in body.items() if k in DEFAULT_SETTINGS and k != "theme"}
        out = dict(store.set_settings(allowed))
        out["theme"] = _theme(waffler_theme)
        return out
    if seg == ["devices"] and method == "GET":
        try:
            from src.meetings.recorder import SystemSource, list_microphones
        except ImportError:
            from meetings.recorder import SystemSource, list_microphones
        return {"microphones": list_microphones(), "system_audio_supported": SystemSource.supported()}
    if seg == ["local-status"] and method == "GET":
        return {"installed": False}
    if seg and seg[0] == "keys":
        if method == "GET" and len(seg) == 1:
            try:
                from src.meetings import keys
            except ImportError:
                from meetings import keys
            return keys.status()
        raise _Reply(403, "Keys are set in Waffler's Settings.")
    if len(seg) == 2 and seg[0] == "models" and method == "GET":
        return {"models": [], "default": ""}

    # ── notes ───────────────────────────────────────────────────────────────
    if seg == ["notes"]:
        if method == "GET":
            return store.list_notes(query=query.get("q", ""), folder_id=query.get("folder") or None)
        if method == "POST":
            note = store.create_note(template_id=body.get("template_id") or "general")
            ctl._on_change()
            return note
    if len(seg) == 2 and seg[0] == "notes":
        note_id = seg[1]
        if method == "GET":
            return full(note_id)
        if method == "PATCH":
            note_or_404(note_id)
            fields = {k: body[k] for k in _NOTE_FIELDS if body.get(k) is not None}
            if body.get("clear_folder"):
                fields["folder_id"] = None
            if "title" in fields:
                fields["title_locked"] = 1 if str(fields["title"]).strip() else 0
            store.update_note(note_id, **fields)
            if body.get("clear_folder"):
                with store._write:
                    store.db.execute("UPDATE notes SET folder_id=NULL WHERE id=?", (note_id,))
            ctl._on_change()
            return full(note_id)
        if method == "DELETE":
            note_or_404(note_id)
            ctl.delete(note_id)
            return {"ok": True}
    if len(seg) == 3 and seg[0] == "notes":
        note_id, what = seg[1], seg[2]
        if what == "audio" and method == "DELETE":
            note_or_404(note_id)
            svc.delete_audio(note_id)
            return full(note_id)
        if what == "enhance" and method == "POST":
            note_or_404(note_id)
            try:
                from src.meetings import llm
            except ImportError:
                from meetings import llm
            llm.resolve_provider(store.settings())           # fail now if there is no key
            svc.enhance(note_id, body.get("template_id"))
            ctl._watch(note_id)
            return full(note_id)
        if what == "retranscribe" and method == "POST":
            note_or_404(note_id)
            if not svc.has_audio(note_id):
                raise _Reply(400, "This note's audio has been deleted.")
            svc.retranscribe(note_id)
            ctl._watch(note_id)
            return full(note_id)

    # ── recording (the same recording the hotkey and the bar control) ───────
    if seg == ["record", "start"] and method == "POST":
        if svc.state().get("recording"):
            raise _Reply(409, "Already recording.")
        note_id = body.get("note_id")
        try:
            note = svc.start_recording(note_id, body.get("template_id"))
        except KeyError:
            raise _Reply(404, "That note no longer exists.") from None
        except RuntimeError as e:
            raise _Reply(409, str(e) or "The recording couldn't start.") from None
        ctl._log(f"[meetings] recording started ({note['id']})")
        ctl._on_change()
        return {"note": full(note["id"]), "state": state()}
    if seg == ["record", "stop"] and method == "POST":
        result = ctl.stop()
        note_id = result.get("note_id")
        return {"note": full(note_id) if note_id else None, "state": state()}

    # ── a file dropped in ───────────────────────────────────────────────────
    if seg == ["import"] and method == "POST":
        data = body.get("file_base64") or ""
        if not data:
            raise _Reply(400, "Choose an audio file.")
        if len(data) * 3 // 4 > MAX_IMPORT_BYTES:
            raise _Reply(400, "That file is too large to import here.")
        suffix = Path(str(body.get("file_name") or "audio")).suffix
        suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", suffix or "") else ".bin"
        path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                path = Path(tmp.name)
                tmp.write(base64.b64decode(data, validate=False))
            note = svc.ingest_file(path)
        except _Reply:
            raise
        except Exception as e:  # noqa: BLE001
            raise _Reply(400, f"Couldn't read that audio file ({type(e).__name__}).") from None
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
        ctl._on_change()
        ctl._watch(note["id"])
        return full(note["id"])

    if seg == ["ask"] and method == "POST":
        if not str(body.get("question") or "").strip():
            raise _Reply(400, "Ask a question.")
        return svc.ask(body["question"], body.get("note_id") or None, body.get("history") or [])

    # ── folders and templates ───────────────────────────────────────────────
    if seg and seg[0] == "folders":
        if method == "POST" and len(seg) == 1:
            store.save_folder(str(body.get("name") or ""))
        elif method == "PUT" and len(seg) == 2:
            store.save_folder(str(body.get("name") or ""), seg[1])
        elif method == "DELETE" and len(seg) == 2:
            store.delete_folder(seg[1])
        elif not (method == "GET" and len(seg) == 1):
            raise _Reply(404, "Not found.")
        return store.folders()
    if seg and seg[0] == "templates":
        if method == "POST" and len(seg) == 1:
            store.save_template(str(body.get("name") or ""), str(body.get("prompt") or ""))
        elif method == "PUT" and len(seg) == 2:
            store.save_template(str(body.get("name") or ""), str(body.get("prompt") or ""), seg[1])
        elif method == "DELETE" and len(seg) == 2:
            store.delete_template(seg[1])
        elif not (method == "GET" and len(seg) == 1):
            raise _Reply(404, "Not found.")
        return store.templates()

    raise _Reply(404, "Not found.")
