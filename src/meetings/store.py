"""The notes database: SQLite (WAL) with full-text search.

One note = one recording or meeting. A note has sessions (the first recording
plus every resume) and transcript segments on one continuous timeline.
Segments are append-only, which is what makes sync simple: notes are merged
last-write-wins by `updated_at`, segments by id.

Deleting a note is final. Everything said or written in it goes at once (its
transcript, its notes, what was typed, its title, its sessions, its audio)
and what stays is a tombstone: the id, when it was made, when it went and
the deleted mark, which is all sync needs to tell the person's other devices.
There is no trash and no undo. A tombstone never takes content again.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .paths import data_dir, remove_audio

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
    "capture_system_audio": True,
    "mic_device": "",
    "keep_audio": True,
    "auto_enhance": True,
    "theme": "auto",
    "hub_url": "",
    "phone_access": False,
    "device_name": "",
}


# Every field of a deleted note that could say what it was about, as it is
# kept and as it is sent: the same blanks the iPhone sends for a deletion,
# and the least the account service accepts (it wants each of them present).
TOMBSTONE = {"title": "", "title_locked": 0, "my_notes": "", "summary": "", "template_id": "general",
             "folder_id": None, "status": "ready", "error": "", "duration_s": 0}


def tombstone(note) -> dict:
    """A deleted note as sync sends it: no words, no sessions, no lines."""
    return {"id": note["id"], **TOMBSTONE, "created_at": note["created_at"],
            "updated_at": note["updated_at"], "deleted": 1, "sessions": [], "segments": []}


# What an import will take (import_external): one meeting's worth of text.
# The same sizes as the account service's import_meeting tool.
IMPORT_TITLE, IMPORT_NOTES, IMPORT_LINE, IMPORT_TRANSCRIPT = 300, 100_000, 4_000, 400_000
MAX_FOLDERS = 200
IMPORT_DELETED = "deleted"            # import_external's reasons for answering ok: False
IMPORT_BAD_DATE = "bad_date"
IMPORT_NO_ID = "no_id"
IMPORT_NO_FOLDER = "no_folder"
IMPORT_TOO_LONG = "too_long"


def import_id(source: str, external_id: str) -> str:
    """The id an imported meeting gets: made from where it came from, so
    importing it again finds the same meeting, here and through the account
    (whose service makes it the same way). An id made here for a recording is
    random, so the two never meet."""
    return hashlib.sha256(f"import\x00{source.lower()}\x00{external_id}".encode()).hexdigest()[:32]


def clean_text(text, limit: int) -> str:
    """Text as an import stores it: a string, no control characters but new
    lines and tabs, cut to size."""
    if not isinstance(text, str):
        return ""
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32)[:limit].strip()


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
        self.audio_root = self.path.parent / "audio"   # beside the database: each note's recordings
        self._local = threading.local()
        self._write = threading.RLock()
        with self._write:
            self.db.executescript(SCHEMA)
            self._seed()
            for index in ("notes_fts", "segments_fts"):
                try:                                   # a deleted note's words leave the search index too
                    self.db.execute(f"INSERT INTO {index}({index}, rank) VALUES('secure-delete', 1)")
                except sqlite3.Error:
                    pass                               # an SQLite from before 3.42 has no such setting
        self.purge_deleted()

    @property
    def db(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            try:
                con.row_factory = sqlite3.Row
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA synchronous=NORMAL")
                con.execute("PRAGMA secure_delete=ON")     # what is deleted is overwritten, not left in free pages
            except BaseException:
                # A file that is not a database: closed now, not whenever the
                # garbage collector gets to it. Windows cannot delete an open
                # file, so "Delete all my data" could not remove it.
                con.close()
                raise
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
        if fields.get("deleted"):
            self.delete_note(note_id)
            return self.get_note(note_id)
        if not fields or self._is_deleted(note_id):    # a tombstone takes nothing (notes still being written, say)
            return self.get_note(note_id)
        if touch:
            fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._write:
            self.db.execute(f"UPDATE notes SET {sets} WHERE id=?", (*fields.values(), note_id))
            self._reindex(note_id)
        return self.get_note(note_id)

    def _is_deleted(self, note_id: str) -> bool:
        r = self.db.execute("SELECT deleted FROM notes WHERE id=?", (note_id,)).fetchone()
        return bool(r and r["deleted"])

    def _is_live(self, note_id: str) -> bool:
        r = self.db.execute("SELECT deleted FROM notes WHERE id=?", (note_id,)).fetchone()
        return bool(r and not r["deleted"])

    def _empty(self, note_id: str) -> None:
        """Remove everything said in a note: its transcript, its sessions,
        what search knows of it, and its audio. The note's own row is the
        caller's business. Call with _write held."""
        for table in ("segments_fts", "segments", "sessions", "notes_fts"):
            self.db.execute(f"DELETE FROM {table} WHERE note_id=?", (note_id,))
        try:
            remove_audio(note_id, self.audio_root)
        except ValueError:
            pass                                       # not an id a folder was ever made from

    def _entomb(self, note_id: str, when: str) -> None:
        """Leave only a note's tombstone, dated `when`. Call with _write held."""
        sets = ", ".join(f"{k}=?" for k in TOMBSTONE)
        self.db.execute(f"UPDATE notes SET {sets}, deleted=1, updated_at=? WHERE id=?",
                        (*TOMBSTONE.values(), when, note_id))
        self._empty(note_id)

    def delete_note(self, note_id: str) -> None:
        """Delete a note for good: see the top of this file for what goes."""
        with self._write:
            self._entomb(note_id, now_iso())
        self._settle()

    def _settle(self) -> None:
        """Write what was just removed through to the database file. Until a
        checkpoint the emptied pages sit in the write-ahead log, and the words
        are still in the main file; this makes a delete complete at once.
        PASSIVE never waits for a reader, and a checkpoint that cannot run is
        simply the next one's work."""
        try:
            self.db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
        except sqlite3.Error:
            pass

    def purge_deleted(self) -> int:
        """Notes deleted before deleting removed anything (they were only
        marked and hidden) lose their content now, and are dated now so the
        account and the other devices are told again, this time with nothing
        in them. Audio left behind for any deleted note goes too. Runs when
        the store opens; a second run finds nothing. Returns notes emptied."""
        with self._write:
            old = [r["id"] for r in self.db.execute(
                "SELECT id FROM notes n WHERE deleted=1 AND (title<>'' OR my_notes<>'' OR summary<>'' "
                "OR error<>'' OR folder_id IS NOT NULL OR duration_s<>0 "
                "OR EXISTS(SELECT 1 FROM segments s WHERE s.note_id=n.id) "
                "OR EXISTS(SELECT 1 FROM sessions s WHERE s.note_id=n.id))").fetchall()]
            for note_id in old:
                self._entomb(note_id, now_iso())
            try:
                left = [p.name for p in self.audio_root.iterdir() if p.is_dir()]
            except OSError:
                left = []
            for name in left:
                if self._is_deleted(name):
                    try:
                        remove_audio(name, self.audio_root)
                    except ValueError:
                        pass
        if old:
            self._settle()
        return len(old)

    def wipe(self) -> None:
        """Delete every meeting, with no tombstones ("Delete all my data"):
        notes, transcripts, sessions, uploads, the search index and all the
        audio. The engine's settings (how far sync has got among them), the
        folders' names and the person's own templates stay, as Waffler's
        settings and snippets do. The file is then packed, so nothing deleted
        is left inside it."""
        with self._write:
            for table in ("segments_fts", "notes_fts", "segments", "sessions", "uploads", "notes"):
                self.db.execute(f"DELETE FROM {table}")
            for statement in ("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')",
                              "INSERT INTO notes_fts(notes_fts) VALUES('rebuild')",
                              "PRAGMA wal_checkpoint(TRUNCATE)", "VACUUM", "PRAGMA wal_checkpoint(TRUNCATE)"):
                try:
                    self.db.execute(statement).fetchall()
                except sqlite3.Error:
                    pass                               # a reader is busy: the rows are gone all the same
        shutil.rmtree(self.audio_root, ignore_errors=True)

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
            if not self._is_live(note_id):             # a line still being written down when the note went
                return out
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

    def retext_segments(self, note_id: str, changes: list[tuple[str, str, str]],
                        touch: bool = True) -> int:
        """New text for lines already stored (a transcript's capitals and
        punctuation, tidy_lines.py): [(segment id, the text it has, the text
        to give it)]. A line is changed only if it still says what the caller
        checked. Its id, times and source are never touched, and search
        follows the new text. All of them or none. Returns lines changed."""
        done = 0
        with self._write:
            rows = None
            self.db.execute("BEGIN")
            try:
                for sid, old, new in changes:
                    new = " ".join(str(new).split())
                    if not new or not self.db.execute(
                            "UPDATE segments SET text=? WHERE id=? AND note_id=? AND text=?",
                            (new, sid, note_id, old)).rowcount:
                        continue
                    if rows is None:               # one pass over the index, not one per line
                        rows = {r["segment_id"]: r["rowid"] for r in self.db.execute(
                            "SELECT rowid, segment_id FROM segments_fts WHERE note_id=?", (note_id,))}
                    if sid in rows:
                        self.db.execute("UPDATE segments_fts SET text=? WHERE rowid=?", (new, rows[sid]))
                    else:
                        self.db.execute("INSERT INTO segments_fts(segment_id,note_id,text) VALUES(?,?,?)",
                                        (sid, note_id, new))
                    done += 1
                if done and touch:
                    self.db.execute("UPDATE notes SET updated_at=? WHERE id=?", (now_iso(), note_id))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return done

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

    def folder_named(self, name: str, limit: int = MAX_FOLDERS) -> dict | None:
        """The folder with this name, whatever its capitals; made if there is
        none. None when the name is empty or there are already `limit`
        folders. `made` says which happened."""
        name = " ".join(str(name or "").split())
        if not name:
            return None
        with self._write:
            folders = self.folders()
            same = next((f for f in folders if f["name"].casefold() == name.casefold()), None)
            if same:
                return {**same, "made": False}
            if len(folders) >= limit:
                return None
            made = self.save_folder(name)
        return {"id": made["id"], "name": name, "note_count": 0, "made": True}

    # ── a meeting from another app ──────────────────────────────────────────
    def import_external(self, source: str, external_id: str, *, title: str, meeting_at: str,
                        summary: str = "", my_notes: str = "", lines: list[dict] | None = None,
                        part: int = 1, duration_s: float | None = None, folder_id: str | None = None,
                        folder_name: str | None = None, whole_parts_only: bool = False) -> dict:
        """Bring in one meeting from another notes app, as an ordinary meeting.
        "Import from Granola" and the Claude link's import_meeting both end
        here, and the account service's import_meeting follows the same rules,
        so one meeting imported three ways is one meeting.

        Its id comes from `source` and `external_id` (import_id), so a second
        import finds the first: the title and notes are brought up to date and
        no line is stored twice. A transcript arrives whole (`part` 1) or in
        numbered parts; a part whose first line is already here is skipped.
        `lines` are {"source": "mic" or "system", "text", "start_s" or None};
        a line with no time follows the one before, and lasts as long as it
        would take to say.

        A meeting the person deleted is not brought back. Titles, notes and
        lines are cut to IMPORT_TITLE, IMPORT_NOTES and IMPORT_LINE. Past
        IMPORT_TRANSCRIPT characters no more lines are stored and the answer
        says `transcript_truncated` (with `whole_parts_only`, the part is
        refused instead and none of it is stored). `folder_name` files it in
        the folder of that name, made if need be (folder_named).

        Answers {"ok": True, "id", "created", "lines_added", "part_already_stored",
        "transcript_truncated"} or {"ok": False, "reason", "id"}."""
        source = " ".join(clean_text(source, 40).split())
        external_id = clean_text(external_id, 200)
        if not source or not external_id:
            return {"ok": False, "reason": IMPORT_NO_ID, "id": ""}
        try:
            when = datetime.fromisoformat(str(meeting_at).replace("Z", "+00:00"))
        except ValueError:
            return {"ok": False, "reason": IMPORT_BAD_DATE, "id": ""}
        if when.tzinfo is None:
            when = when.astimezone()
        when_iso = when.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        title = " ".join(clean_text(title, IMPORT_TITLE).split()) or "Untitled"
        summary, my_notes = clean_text(summary, IMPORT_NOTES), clean_text(my_notes, IMPORT_NOTES)
        try:
            part = max(1, min(int(part or 1), 99))
        except (TypeError, ValueError):
            part = 1
        said = []
        for line in lines or []:
            text = " ".join(clean_text(line.get("text"), IMPORT_LINE).split())
            if text:
                said.append({"source": "system" if line.get("source") == "system" else "mic",
                             "text": text, "start_s": line.get("start_s")})

        nid = import_id(source, external_id)
        with self._write:
            if folder_id is not None and not any(f["id"] == folder_id for f in self.folders()):
                return {"ok": False, "reason": IMPORT_NO_FOLDER, "id": nid}
            note = self.get_note(nid)
            if note is not None and note["deleted"]:
                return {"ok": False, "reason": IMPORT_DELETED, "id": nid}
            if folder_id is None and folder_name:
                folder_id = (self.folder_named(folder_name) or {}).get("id")
            created = note is None
            if created:
                self.create_note(title=title, folder_id=folder_id, note_id=nid)
                self.db.execute("UPDATE notes SET created_at=?, title_locked=1 WHERE id=?", (when_iso, nid))
            fields: dict = {}
            if part == 1:
                fields["title"] = title
                if summary:
                    fields["summary"] = summary
                if my_notes:
                    fields["my_notes"] = my_notes
                if folder_id is not None:
                    fields["folder_id"] = folder_id

            have = self.db.execute("SELECT coalesce(sum(length(text)),0), coalesce(max(end_s),0) "
                                   "FROM segments WHERE note_id=?", (nid,)).fetchone()
            stored, clock = int(have[0]), float(have[1])
            already = bool(self.db.execute("SELECT 1 FROM segments WHERE id=?",
                                           (import_id(nid, f"{part}:0"),)).fetchone())
            added, truncated = 0, False
            if said and not already:
                if whole_parts_only and stored + sum(len(line["text"]) for line in said) > IMPORT_TRANSCRIPT:
                    return {"ok": False, "reason": IMPORT_TOO_LONG, "id": nid}
                rows = []
                for i, line in enumerate(said):
                    if stored + len(line["text"]) > IMPORT_TRANSCRIPT:
                        truncated = True
                        break
                    stored += len(line["text"])
                    start = line["start_s"]
                    if not isinstance(start, (int, float)) or isinstance(start, bool) \
                            or not 0 <= start < 86_400 * 2:
                        start = clock                  # no time given: it follows the line before
                    # A line takes as long as it would to say (15 characters a second).
                    end = max(start + 0.5, start + len(line["text"]) / 15)
                    clock = max(clock, end)
                    rows.append({"id": import_id(nid, f"{part}:{i}"), "source": line["source"],
                                 "start_s": round(start, 2), "end_s": round(end, 2),
                                 "text": line["text"], "created_at": when_iso})
                added = len(self.add_segments(nid, rows, touch=False))
            length = float(self.db.execute("SELECT coalesce(max(end_s),0) FROM segments WHERE note_id=?",
                                           (nid,)).fetchone()[0])
            if isinstance(duration_s, (int, float)) and not isinstance(duration_s, bool) \
                    and 0 < duration_s < 86_400:
                length = max(length, float(duration_s))
            self.update_note(nid, **fields, duration_s=length, status="ready")
        return {"ok": True, "id": nid, "created": created, "lines_added": added,
                "part_already_stored": already and bool(said), "transcript_truncated": truncated}

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
        """Everything changed after `since` (an ISO time), for sync. A deleted
        note goes as its tombstone and nothing else, whatever the row holds."""
        notes = []
        for r in self.db.execute("SELECT * FROM notes WHERE updated_at>? ORDER BY updated_at",
                                 (since,)).fetchall():
            if r["deleted"]:
                notes.append(tombstone(r))
                continue
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
        edited it last, its segments and sessions from both.

        A note deleted somewhere else arrives marked `deleted`. If that is
        the last word on it (it is newer than this computer's copy, and the
        note is not being recorded here), this computer's copy goes the same
        way as one deleted here: content and audio removed, a tombstone left.
        Whatever came with it is not stored: an older service still sends a
        deleted note's transcript. A note that is a tombstone here takes no
        transcript either, unless a newer edit elsewhere brings it back."""
        applied = 0
        with self._write:
            for n in changes.get("notes", []):
                mine = self.db.execute("SELECT updated_at,status,deleted,duration_s FROM notes WHERE id=?",
                                       (n["id"],)).fetchone()
                cols = ["title", "title_locked", "my_notes", "summary", "template_id", "folder_id",
                        "status", "error", "duration_s", "created_at", "updated_at", "deleted"]
                gone = bool(n.get("deleted"))
                vals = {c: n.get(c) for c in cols}
                # A note being recorded elsewhere arrives as simply "ready" here.
                if vals["status"] in ("recording", "processing"):
                    vals["status"] = "ready"
                if gone:
                    vals.update(TOMBSTONE, deleted=1)
                taken = False
                if mine is None:
                    self.db.execute(
                        f"INSERT INTO notes(id,{','.join(cols)}) VALUES(?{',?' * len(cols)})",
                        (n["id"], *[vals[c] for c in cols]))
                    taken = True
                elif n["updated_at"] > mine["updated_at"] and mine["status"] != "recording":
                    if not gone:
                        vals["duration_s"] = max(vals["duration_s"] or 0, mine["duration_s"])
                    self.db.execute(f"UPDATE notes SET {','.join(c + '=?' for c in cols)} WHERE id=?",
                                    (*[vals[c] for c in cols], n["id"]))
                    taken = True
                applied += taken
                if gone and taken and mine is not None and not mine["deleted"]:
                    self._empty(n["id"])               # (a tombstone, or a note never held, has nothing to remove)
                if gone or (not taken and mine["deleted"]):
                    continue                           # nothing is added to, or taken from, a deleted note
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
