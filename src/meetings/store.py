"""The notes database: SQLite (WAL) with full-text search.

One note = one recording or meeting. A note has sessions (the first recording
plus every resume) and transcript segments on one continuous timeline.
Segments are append-only, which is what makes sync simple: notes are merged
last-write-wins by `updated_at`, segments by id.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .paths import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  title_locked INTEGER NOT NULL DEFAULT 0,
  my_notes TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  template_id TEXT NOT NULL DEFAULT 'general',
  folder_id TEXT,
  status TEXT NOT NULL DEFAULT 'idle',
  error TEXT NOT NULL DEFAULT '',
  duration_s REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  note_id TEXT NOT NULL,
  n INTEGER NOT NULL,
  offset_s REAL NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  ended_at TEXT
);
CREATE TABLE IF NOT EXISTS segments (
  id TEXT PRIMARY KEY,
  note_id TEXT NOT NULL,
  session_n INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'mic',
  start_s REAL NOT NULL,
  end_s REAL NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS segments_note ON segments(note_id, start_s);
CREATE TABLE IF NOT EXISTS folders (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  prompt TEXT NOT NULL,
  builtin INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS uploads (
  id TEXT PRIMARY KEY, note_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
  note_id UNINDEXED, title, my_notes, summary, tokenize='porter unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
  segment_id UNINDEXED, note_id UNINDEXED, text, tokenize='porter unicode61');
"""

BUILTIN_TEMPLATES = [
    ("general", "General", "Write clear notes with a short heading for each topic that was actually "
     "discussed, bullet points under each, and a final 'Next steps' section listing actions with "
     "an owner where one was named."),
    ("voice-note", "Voice note", "This is one person thinking out loud. Organise what they said into "
     "topics with bullet points, keep every concrete request, idea and decision, and end with "
     "'Next steps': the things they said they want done, as an actionable list."),
    ("one-to-one", "1:1", "Sections: 'Updates', 'Discussion', 'Feedback', 'Decisions', 'Next steps' "
     "(with owners). Leave out any section nothing was said about."),
    ("standup", "Stand-up", "For each person who spoke: what they did, what they are doing next, "
     "and anything blocking them. End with 'Blockers and follow-ups'."),
    ("discovery", "Customer call", "Sections: 'About them', 'Problems and needs' (their words where "
     "possible), 'What they use today', 'Reactions to what we showed', 'Objections and risks', "
     "'Next steps'."),
    ("interview", "Interview", "Sections: 'Background', 'Experience and examples', 'Strengths', "
     "'Concerns', 'Their questions', 'Next steps'. Report what was said; do not judge the person."),
]

DEFAULT_SETTINGS = {
    "transcribe_provider": "groq",
    "transcribe_model": "whisper-large-v3",
    "language": "en",
    "llm_provider": "auto",
    "llm_model": "",
    "custom_base_url": "",
    "local_thinking": False,
    "local_whisper_model": "auto",
    "capture_system_audio": True,
    "mic_device": "",
    "keep_audio": True,
    "auto_enhance": True,
    "theme": "auto",
    "hub_url": "",
    "phone_access": False,
    "device_name": "",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex


def _fts_query(q: str) -> str:
    """User text as a safe FTS5 query: every word must match, as a prefix."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in q).split() if w]
    return " ".join(f'"{w}"*' for w in words)


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else data_dir() / "meetings.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write = threading.RLock()
        with self._write:
            self.db.executescript(SCHEMA)
            self._seed()

    @property
    def db(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            self._local.con = con
        return con

    def close(self) -> None:
        con = getattr(self._local, "con", None)
        if con is not None:
            con.close()
            self._local.con = None

    def _seed(self) -> None:
        for tid, name, prompt in BUILTIN_TEMPLATES:
            self.db.execute(
                "INSERT INTO templates(id,name,prompt,builtin,updated_at) VALUES(?,?,?,1,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, prompt=excluded.prompt "
                "WHERE templates.builtin=1", (tid, name, prompt, "1970-01-01T00:00:00+00:00"))

    # ── settings ────────────────────────────────────────────────────────────
    def settings(self) -> dict:
        out = dict(DEFAULT_SETTINGS)
        for r in self.db.execute("SELECT key,value FROM settings"):
            try:
                out[r["key"]] = json.loads(r["value"])
            except ValueError:
                pass
        return out

    def set_settings(self, values: dict) -> dict:
        with self._write:
            for k, v in values.items():
                self.db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (k, json.dumps(v)))
        return self.settings()

    # ── notes ───────────────────────────────────────────────────────────────
    def _reindex(self, note_id: str) -> None:
        self.db.execute("DELETE FROM notes_fts WHERE note_id=?", (note_id,))
        r = self.db.execute("SELECT title,my_notes,summary,deleted FROM notes WHERE id=?",
                            (note_id,)).fetchone()
        if r and not r["deleted"]:
            self.db.execute("INSERT INTO notes_fts(note_id,title,my_notes,summary) VALUES(?,?,?,?)",
                            (note_id, r["title"], r["my_notes"], r["summary"]))

    def create_note(self, title: str = "", template_id: str = "general",
                    folder_id: str | None = None, note_id: str | None = None) -> dict:
        nid, ts = note_id or new_id(), now_iso()
        with self._write:
            self.db.execute("INSERT INTO notes(id,title,template_id,folder_id,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?)", (nid, title, template_id, folder_id, ts, ts))
            self._reindex(nid)
        return self.get_note(nid)

    def update_note(self, note_id: str, touch: bool = True, **fields) -> dict | None:
        allowed = {"title", "title_locked", "my_notes", "summary", "template_id", "folder_id",
                   "status", "error", "duration_s", "deleted"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return self.get_note(note_id)
        if touch:
            fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._write:
            self.db.execute(f"UPDATE notes SET {sets} WHERE id=?", (*fields.values(), note_id))
            self._reindex(note_id)
        return self.get_note(note_id)

    def delete_note(self, note_id: str) -> None:
        self.update_note(note_id, deleted=1)

    def get_note(self, note_id: str, with_segments: bool = False) -> dict | None:
        r = self.db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if not r:
            return None
        note = dict(r)
        note["sessions"] = [dict(s) for s in self.db.execute(
            "SELECT * FROM sessions WHERE note_id=? ORDER BY n", (note_id,))]
        if with_segments:
            note["segments"] = self.segments(note_id)
        return note

    def list_notes(self, query: str = "", folder_id: str | None = None, limit: int = 200,
                   from_date: str | None = None, to_date: str | None = None) -> list[dict]:
        where, args = ["n.deleted=0"], []
        if folder_id:
            where.append("n.folder_id=?")
            args.append(folder_id)
        if from_date:
            where.append("n.created_at>=?")
            args.append(from_date)
        if to_date:
            where.append("n.created_at<=?")
            args.append(to_date if "T" in to_date else to_date + "T23:59:59.999+00:00")
        fts = _fts_query(query) if query else ""
        if fts:
            where.append("(n.id IN (SELECT note_id FROM notes_fts WHERE notes_fts MATCH ?) "
                         "OR n.id IN (SELECT note_id FROM segments_fts WHERE segments_fts MATCH ?))")
            args += [fts, fts]
        rows = self.db.execute(
            "SELECT n.id,n.title,n.status,n.folder_id,n.template_id,n.duration_s,n.created_at,"
            "n.updated_at,substr(n.summary,1,240) AS preview,"
            "(SELECT count(*) FROM segments s WHERE s.note_id=n.id) AS segment_count "
            f"FROM notes n WHERE {' AND '.join(where)} ORDER BY n.created_at DESC LIMIT ?",
            (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    # ── sessions and segments ───────────────────────────────────────────────
    def start_session(self, note_id: str) -> dict:
        """A new recording on this note. Its timeline starts where the note's
        transcript and earlier sessions end, so a resumed note reads as one."""
        with self._write:
            n = self.db.execute("SELECT coalesce(max(n),0)+1 FROM sessions WHERE note_id=?",
                                (note_id,)).fetchone()[0]
            offset = self.db.execute("SELECT duration_s FROM notes WHERE id=?",
                                     (note_id,)).fetchone()[0]
            sid = new_id()
            self.db.execute("INSERT INTO sessions(id,note_id,n,offset_s,started_at) VALUES(?,?,?,?,?)",
                            (sid, note_id, n, offset, now_iso()))
        return {"id": sid, "note_id": note_id, "n": n, "offset_s": offset}

    def end_session(self, session_id: str, note_id: str, duration_s: float) -> None:
        with self._write:
            self.db.execute("UPDATE sessions SET ended_at=? WHERE id=?", (now_iso(), session_id))
        self.update_note(note_id, duration_s=duration_s)

    def add_segments(self, note_id: str, segments: list[dict], touch: bool = True) -> list[dict]:
        out = []
        with self._write:
            for s in segments:
                text = " ".join(str(s.get("text", "")).split())
                if not text:
                    continue
                sid = s.get("id") or new_id()
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO segments(id,note_id,session_n,source,start_s,end_s,text,"
                    "created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (sid, note_id, int(s.get("session_n", 1)), s.get("source", "mic"),
                     float(s["start_s"]), float(s["end_s"]), text, s.get("created_at") or now_iso()))
                if cur.rowcount:
                    self.db.execute("INSERT INTO segments_fts(segment_id,note_id,text) VALUES(?,?,?)",
                                    (sid, note_id, text))
                    out.append({**s, "id": sid, "text": text})
            if out and touch:
                self.db.execute("UPDATE notes SET updated_at=? WHERE id=?", (now_iso(), note_id))
        return out

    def segments(self, note_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT id,session_n,source,start_s,end_s,text,created_at FROM segments "
            "WHERE note_id=? ORDER BY start_s, source", (note_id,))]

    def transcript_text(self, note_id: str, labels: bool = True) -> str:
        """The transcript as plain text. Consecutive lines from one source are
        joined; a resumed session is marked."""
        lines, last_src, last_sess = [], None, None
        for s in self.segments(note_id):
            if last_sess is not None and s["session_n"] != last_sess:
                lines.append(f"--- recording resumed (part {s['session_n']}) ---")
                last_src = None
            last_sess = s["session_n"]
            if s["source"] == last_src and lines:
                lines[-1] += " " + s["text"]
            else:
                who = {"mic": "Me", "system": "Them"}.get(s["source"], s["source"])
                lines.append(f"{who}: {s['text']}" if labels else s["text"])
                last_src = s["source"]
        return "\n".join(lines)

    def search_segments(self, query: str, note_ids: list[str] | None = None,
                        limit: int = 20) -> list[dict]:
        fts = _fts_query(query)
        if not fts:
            return []
        sql = ("SELECT s.id,s.note_id,s.source,s.start_s,s.end_s,s.text,n.title,n.created_at "
               "FROM segments_fts f JOIN segments s ON s.id=f.segment_id "
               "JOIN notes n ON n.id=s.note_id WHERE segments_fts MATCH ? AND n.deleted=0")
        args: list = [fts]
        if note_ids:
            sql += f" AND s.note_id IN ({','.join('?' * len(note_ids))})"
            args += note_ids
        sql += " ORDER BY n.created_at DESC, s.start_s LIMIT ?"
        return [dict(r) for r in self.db.execute(sql, (*args, limit))]

    # ── uploads ─────────────────────────────────────────────────────────────
    def claim_upload(self, upload_id: str, note_id: str) -> bool:
        """True the first time an upload id is seen. A phone retries an upload
        whose reply it never received; the audio must not be added twice."""
        with self._write:
            cur = self.db.execute("INSERT OR IGNORE INTO uploads(id,note_id,created_at) VALUES(?,?,?)",
                                  (upload_id, note_id, now_iso()))
        return cur.rowcount == 1

    def release_upload(self, upload_id: str) -> None:
        with self._write:
            self.db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))

    # ── folders and templates ───────────────────────────────────────────────
    def folders(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT f.id,f.name,(SELECT count(*) FROM notes n WHERE n.folder_id=f.id AND n.deleted=0)"
            " AS note_count FROM folders f WHERE f.deleted=0 ORDER BY f.name COLLATE NOCASE")]

    def save_folder(self, name: str, folder_id: str | None = None) -> dict:
        fid = folder_id or new_id()
        with self._write:
            self.db.execute("INSERT INTO folders(id,name,updated_at) VALUES(?,?,?) ON CONFLICT(id) "
                            "DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at",
                            (fid, name.strip() or "Untitled", now_iso()))
        return {"id": fid, "name": name}

    def delete_folder(self, folder_id: str) -> None:
        with self._write:
            self.db.execute("UPDATE folders SET deleted=1, updated_at=? WHERE id=?",
                            (now_iso(), folder_id))
            for r in self.db.execute("SELECT id FROM notes WHERE folder_id=?", (folder_id,)).fetchall():
                self.update_note(r["id"], folder_id=None)

    def templates(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT id,name,prompt,builtin FROM templates WHERE deleted=0 "
            "ORDER BY builtin DESC, name COLLATE NOCASE")]

    def template(self, template_id: str) -> dict:
        r = self.db.execute("SELECT id,name,prompt,builtin FROM templates WHERE id=? AND deleted=0",
                            (template_id,)).fetchone()
        return dict(r) if r else self.template("general")

    def save_template(self, name: str, prompt: str, template_id: str | None = None) -> dict:
        tid = template_id or new_id()
        with self._write:
            self.db.execute(
                "INSERT INTO templates(id,name,prompt,builtin,updated_at) VALUES(?,?,?,0,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, prompt=excluded.prompt, "
                "updated_at=excluded.updated_at, deleted=0 WHERE templates.builtin=0",
                (tid, name.strip() or "Untitled", prompt, now_iso()))
        return self.template(tid)

    def delete_template(self, template_id: str) -> None:
        with self._write:
            self.db.execute("UPDATE templates SET deleted=1, updated_at=? WHERE id=? AND builtin=0",
                            (now_iso(), template_id))

    # ── sync ────────────────────────────────────────────────────────────────
    def export_changes(self, since: str = "") -> dict:
        """Everything changed after `since` (an ISO time), for sync."""
        notes = []
        for r in self.db.execute("SELECT * FROM notes WHERE updated_at>? ORDER BY updated_at",
                                 (since,)).fetchall():
            n = dict(r)
            n["sessions"] = [dict(s) for s in self.db.execute(
                "SELECT * FROM sessions WHERE note_id=?", (n["id"],))]
            n["segments"] = [dict(s) for s in self.db.execute(
                "SELECT * FROM segments WHERE note_id=?", (n["id"],))]
            notes.append(n)
        folders = [dict(r) for r in self.db.execute(
            "SELECT * FROM folders WHERE updated_at>?", (since,))]
        templates = [dict(r) for r in self.db.execute(
            "SELECT * FROM templates WHERE builtin=0 AND updated_at>?", (since,))]
        return {"notes": notes, "folders": folders, "templates": templates, "now": now_iso()}

    def import_changes(self, changes: dict) -> dict:
        """Merge another device's changes: a note's fields from whichever side
        edited it last, its segments and sessions from both."""
        applied = 0
        with self._write:
            for n in changes.get("notes", []):
                mine = self.db.execute("SELECT updated_at,status FROM notes WHERE id=?",
                                       (n["id"],)).fetchone()
                cols = ["title", "title_locked", "my_notes", "summary", "template_id", "folder_id",
                        "status", "error", "duration_s", "created_at", "updated_at", "deleted"]
                # A note being recorded elsewhere arrives as simply "ready" here.
                vals = {c: n.get(c) for c in cols}
                if vals["status"] in ("recording", "processing"):
                    vals["status"] = "ready"
                if mine is None:
                    self.db.execute(
                        f"INSERT INTO notes(id,{','.join(cols)}) VALUES(?{',?' * len(cols)})",
                        (n["id"], *[vals[c] for c in cols]))
                    applied += 1
                elif n["updated_at"] > mine["updated_at"] and mine["status"] != "recording":
                    vals["duration_s"] = max(vals["duration_s"] or 0, self.db.execute(
                        "SELECT duration_s FROM notes WHERE id=?", (n["id"],)).fetchone()[0])
                    self.db.execute(f"UPDATE notes SET {','.join(c + '=?' for c in cols)} WHERE id=?",
                                    (*[vals[c] for c in cols], n["id"]))
                    applied += 1
                for s in n.get("sessions", []):
                    self.db.execute(
                        "INSERT OR IGNORE INTO sessions(id,note_id,n,offset_s,started_at,ended_at) "
                        "VALUES(?,?,?,?,?,?)", (s["id"], s["note_id"], s["n"], s["offset_s"],
                                                s["started_at"], s.get("ended_at")))
                self.add_segments(n["id"], n.get("segments", []), touch=False)
                self._reindex(n["id"])
            for f in changes.get("folders", []):
                self.db.execute(
                    "INSERT INTO folders(id,name,updated_at,deleted) VALUES(?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at,"
                    " deleted=excluded.deleted WHERE excluded.updated_at>folders.updated_at",
                    (f["id"], f["name"], f["updated_at"], f.get("deleted", 0)))
            for t in changes.get("templates", []):
                self.db.execute(
                    "INSERT INTO templates(id,name,prompt,builtin,updated_at,deleted) "
                    "VALUES(?,?,?,0,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
                    "prompt=excluded.prompt, updated_at=excluded.updated_at, deleted=excluded.deleted "
                    "WHERE excluded.updated_at>templates.updated_at AND templates.builtin=0",
                    (t["id"], t["name"], t["prompt"], t["updated_at"], t.get("deleted", 0)))
        return {"applied": applied}

    def recover_interrupted(self) -> int:
        """Notes left 'recording' or 'processing' by a crash become ready again."""
        with self._write:
            cur = self.db.execute("UPDATE notes SET status='ready' "
                                  "WHERE status IN ('recording','processing')")
        return cur.rowcount


def wait_until(predicate, timeout: float = 10.0, step: float = 0.05) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())
