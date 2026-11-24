"""Deleting a meeting is final (src/meetings/store.py, sync.py, service.py).

Until November 2026 a deleted meeting was only marked and hidden: its notes,
what was typed and its whole transcript stayed in the database for ever, and
the first sync after signing in to an account uploaded them. Now:

1. Delete removes the content at once (transcript, notes, typed notes, title,
   sessions, search index, audio) and leaves a tombstone: id, times, the mark.
2. Sync sends a deleted note as its tombstone and nothing else.
3. Meetings deleted the old way are emptied once, when the store opens.
4. A deletion that arrives from sync empties the meeting here too.

("Delete all my data" is tests/test_meetings_delete_all_my_data.py.)

Two computers are two stores in two folders, signed in to one account on a
stand-in service (tests/_account_fake.py). Temporary folders, no network.
"""
import json
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src.meetings import store as store_mod                                  # noqa: E402
from src.meetings.paths import audio_dir                                      # noqa: E402
from src.meetings.service import Service                                      # noqa: E402
from src.meetings.store import TOMBSTONE, Store, new_id, now_iso              # noqa: E402
from test_account_sync import EMAIL, a_meeting, ctl, fake, two                # noqa: E402,F401

SECRETS = ("Zanzibarbudget", "marmaladeforecast", "pomegranatesecret", "xylophonist")


@pytest.fixture
def home(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(data))
    monkeypatch.setenv("WAFFLER_MEETINGS_HOME", str(data / "meetings"))
    return data / "meetings"


def a_full_meeting(store, title="Zanzibarbudget review"):
    """A meeting with everything a meeting can hold, audio included."""
    note = store.create_note(title=title)
    nid = note["id"]
    session = store.start_session(nid)
    store.add_segments(nid, [{"source": "mic", "start_s": i, "end_s": i + 1.0,
                              "text": f"the xylophonist costs seventeen million {i}"} for i in range(40)])
    store.end_session(session["id"], nid, 40.0)
    store.update_note(nid, summary="## Notes\n- marmaladeforecast agreed", my_notes="typed: pomegranatesecret",
                      title_locked=1, status="ready", error="Couldn't write notes.")
    audio = store.audio_root / nid / "s1_0000_mic.flac"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"fLaC" + b"what was said" * 200)
    return nid, audio


def on_disk(store) -> bytes:
    """Every byte of the database as it lies on the disk, the journal too."""
    store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    return b"".join(p.read_bytes() for p in sorted(store.path.parent.glob(store.path.name + "*")))


def rows_of(store, nid) -> dict:
    count = lambda table: store.db.execute(                                   # noqa: E731
        f"SELECT count(*) FROM {table} WHERE note_id=?", (nid,)).fetchone()[0]
    return {t: count(t) for t in ("segments", "sessions", "segments_fts", "notes_fts")}


def is_a_bare_tombstone(store, nid) -> bool:
    note = store.get_note(nid, with_segments=True)
    return note["deleted"] == 1 and all(note[k] == v for k, v in TOMBSTONE.items()) \
        and note["segments"] == [] and note["sessions"] == [] and not any(rows_of(store, nid).values())


# ── 1. deleting removes the content at once ─────────────────────────────────

def test_deleting_a_meeting_leaves_a_tombstone_and_nothing_that_was_said(home):
    store = Store()
    nid, audio = a_full_meeting(store)
    kept, _ = a_full_meeting(store, "Kept meeting")
    before = store.get_note(nid)
    time.sleep(0.005)
    store.delete_note(nid)
    after = store.get_note(nid, with_segments=True)
    assert is_a_bare_tombstone(store, nid)
    assert after["id"] == nid and after["created_at"] == before["created_at"]
    assert after["updated_at"] > before["updated_at"], "dated when it went, so sync carries the deletion"
    assert not audio.exists() and not audio.parent.exists(), "its audio is gone from the disk"
    assert store.transcript_text(nid) == ""
    for word in ("xylophonist", "Zanzibarbudget", "marmaladeforecast", "pomegranatesecret"):
        assert [n["id"] for n in store.list_notes(query=word)] in ([], [kept]), word
        assert all(s["note_id"] == kept for s in store.search_segments(word)), word
    # The other meeting is whole.
    assert len(store.segments(kept)) == 40 and (store.audio_root / kept).is_dir()
    assert store.get_note(kept)["summary"].startswith("## Notes")


def test_nothing_of_a_deleted_meeting_is_left_in_the_database_file(home):
    store = Store()
    ids = [a_full_meeting(store)[0] for _ in range(5)]
    assert all(word.encode() in on_disk(store) for word in SECRETS)
    for nid in ids:
        store.delete_note(nid)
    raw = on_disk(store)
    if store.db.execute("SELECT sqlite_version()").fetchone()[0] < "3.42":
        pytest.skip("this SQLite cannot take words out of its search index on delete")
    for word in SECRETS:
        assert word.encode() not in raw and word.lower().encode() not in raw, word


def test_a_tombstone_takes_nothing_written_after_the_delete(home):
    """Notes still being written, or a line still being written down, when
    the meeting was deleted must not put its content back."""
    store = Store()
    nid, _ = a_full_meeting(store)
    store.delete_note(nid)
    stamp = store.get_note(nid)["updated_at"]
    store.update_note(nid, summary="## Notes\n- marmaladeforecast", title="Zanzibarbudget", status="ready")
    store.update_note(nid, my_notes="pomegranatesecret", touch=False)
    assert store.add_segments(nid, [{"start_s": 0, "end_s": 1, "text": "the xylophonist again"}]) == []
    assert is_a_bare_tombstone(store, nid) and store.get_note(nid)["updated_at"] == stamp
    # And a note that is not there at all takes no lines either.
    assert store.add_segments(new_id(), [{"start_s": 0, "end_s": 1, "text": "nobody's"}]) == []
    assert store.db.execute("SELECT count(*) FROM segments").fetchone()[0] == 0


def test_marking_a_note_deleted_by_hand_is_the_same_delete(home):
    store = Store()
    nid, audio = a_full_meeting(store)
    store.update_note(nid, deleted=1)
    assert is_a_bare_tombstone(store, nid) and not audio.exists()


def test_the_apps_own_delete_removes_content_and_audio(home):
    svc = Service(store=Store(), source_factory=lambda *a, **k: None)
    nid, audio = a_full_meeting(svc.store)
    assert svc.has_audio(nid)
    svc.delete_note(nid)
    assert is_a_bare_tombstone(svc.store, nid)
    assert not audio.exists() and not svc.has_audio(nid)
    assert svc.store.list_notes() == []


def test_audio_saved_after_the_delete_goes_when_the_recorder_finishes(home):
    svc = Service(store=Store(), source_factory=lambda *a, **k: None)
    nid, audio = a_full_meeting(svc.store)
    svc.delete_note(nid)
    late = audio_dir(nid) / "s1_0001_mic.flac"                               # a part still being saved
    late.write_bytes(b"fLaC" + b"\x00" * 64)
    svc._after_recording(nid)
    assert not late.exists() and is_a_bare_tombstone(svc.store, nid)


# ── 2. sync sends a tombstone and nothing else ──────────────────────────────

_ID = re.compile(r"^[0-9a-f]{32}$")
_TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?\+00:00$")


def the_service_accepts(n: dict) -> bool:
    """What the account service asks of every pushed note, deleted or not
    (natter/hosted_sync.py check_changes): each of these present and of the
    right kind. Sessions and segments may be empty lists."""
    return bool(_ID.match(n["id"])
                and all(isinstance(n[k], str) for k in ("title", "my_notes", "summary", "error"))
                and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", n["template_id"])
                and n["folder_id"] is None
                and n["status"] in {"idle", "ready", "recording", "processing"}
                and isinstance(n["duration_s"], (int, float)) and 0 <= n["duration_s"] < 1e9
                and n["title_locked"] in (0, 1) and n["deleted"] in (0, 1)
                and _TIME.match(n["created_at"]) and _TIME.match(n["updated_at"])
                and isinstance(n["sessions"], list) and isinstance(n["segments"], list))


def test_a_deleted_note_is_exported_as_a_tombstone_the_service_accepts(home):
    store = Store()
    nid, _ = a_full_meeting(store)
    kept, _ = a_full_meeting(store, "Kept meeting")
    store.delete_note(nid)
    sent = {n["id"]: n for n in store.export_changes("")["notes"]}
    stone = sent[nid]
    assert stone == {"id": nid, "title": "", "title_locked": 0, "my_notes": "", "summary": "",
                     "template_id": "general", "folder_id": None, "status": "ready", "error": "",
                     "duration_s": 0, "created_at": stone["created_at"], "updated_at": stone["updated_at"],
                     "deleted": 1, "sessions": [], "segments": []}
    assert the_service_accepts(stone)
    assert len(sent[kept]["segments"]) == 40 and sent[kept]["title"] == "Kept meeting", "a live note goes whole"


def test_export_sends_no_content_even_from_a_row_that_still_holds_some(home):
    """The export does not rely on the purge having run."""
    store = Store()
    nid, _ = a_full_meeting(store)
    store.db.execute("UPDATE notes SET deleted=1 WHERE id=?", (nid,))        # the old soft delete, by hand
    sent = json.dumps(store.export_changes(""))
    assert not any(word in sent for word in SECRETS)
    assert json.loads(sent)["notes"][0]["segments"] == []


def pushed(fake) -> list[dict]:
    return [n for call in fake.calls("POST", "/api/sync/push") for n in json.loads(call.body)["notes"]]


def test_a_meeting_deleted_before_signing_in_is_never_uploaded(ctl, fake):
    """The finding itself: delete, then the first sync with an account."""
    svc = ctl._svc()
    nid, _ = a_full_meeting(svc.store)
    kept = a_meeting(svc.store, "Still here")
    ctl.delete(nid)
    assert ctl.sync_now()["ok"]
    everything = b"".join(call.body for call in fake.requests)
    assert not any(word.encode() in everything for word in SECRETS)
    sent = {n["id"]: n for n in pushed(fake)}
    assert sent[nid]["deleted"] == 1 and sent[nid]["segments"] == [] and sent[nid]["title"] == ""
    assert the_service_accepts(sent[nid])
    assert sent[kept]["title"] == "Still here"
    held = fake.notes_of(EMAIL)[nid]
    assert held["deleted"] == 1 and held["summary"] == "" and held.get("segments", []) == []


def test_deleting_a_synced_meeting_sends_only_its_tombstone(two, fake):
    one, other = two
    nid = one.note("Zanzibarbudget review", words="the xylophonist costs seventeen million")
    one.sync()
    before = len(fake.requests)
    time.sleep(0.01)
    one.store.delete_note(nid)
    assert one.sync()["pushed"] == 1
    after = b"".join(call.body for call in fake.requests[before:])
    assert b"xylophonist" not in after and b"Zanzibarbudget" not in after
    assert other.sync()["pulled"] == 1 and other.titles() == []


# ── 3. meetings deleted the old way are emptied once ────────────────────────

def soft_delete_the_old_way(store, nid, when="2026-06-01T10:00:00.000+00:00"):
    with store._write:
        store.db.execute("UPDATE notes SET deleted=1, updated_at=? WHERE id=?", (when, nid))
        store.db.execute("DELETE FROM notes_fts WHERE note_id=?", (nid,))    # what the old delete did


def test_opening_the_store_empties_meetings_deleted_the_old_way(home):
    store = Store()
    nid, audio = a_full_meeting(store)
    kept, kept_audio = a_full_meeting(store, "Kept meeting")
    soft_delete_the_old_way(store, nid)
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"fLaC")                                               # audio the old delete missed
    assert len(store.segments(nid)) == 40 and store.get_note(nid)["summary"]
    store.close()

    opened = Store()
    assert is_a_bare_tombstone(opened, nid)
    assert not audio.exists() and not audio.parent.exists()
    stamp = opened.get_note(nid)["updated_at"]
    assert stamp > "2026-06-01T10:00:00.000+00:00", "dated now, so the account is told again, with nothing in it"
    assert len(opened.segments(kept)) == 40 and kept_audio.exists(), "a live meeting is not touched"
    opened.close()

    again = Store()                                                          # a second run finds nothing
    assert again.purge_deleted() == 0 and again.get_note(nid)["updated_at"] == stamp
    sent = {n["id"]: n for n in again.export_changes("")["notes"]}
    assert not any(word in json.dumps(sent[nid]) for word in SECRETS) and sent[nid]["segments"] == []


def test_an_old_deleted_meeting_can_no_longer_be_uploaded(ctl, fake, monkeypatch):
    """Deleted the old way while signed out, then Waffler is updated and the
    person signs in: the first sync carries a tombstone."""
    data = ctl.data / "meetings"
    old = Store(data / "meetings.db")
    nid, _ = a_full_meeting(old)
    soft_delete_the_old_way(old, nid)
    old.close()
    assert ctl.sync_now()["ok"]                                              # the engine opens the store here
    everything = b"".join(call.body for call in fake.requests)
    assert not any(word.encode() in everything for word in SECRETS)
    assert [n["deleted"] for n in pushed(fake) if n["id"] == nid] == [1]
    assert is_a_bare_tombstone(ctl._svc().store, nid)


def test_a_tombstone_already_bare_is_not_dated_again(home):
    store = Store()
    nid, _ = a_full_meeting(store)
    store.delete_note(nid)
    stamp = store.get_note(nid)["updated_at"]
    store.close()
    time.sleep(0.005)
    assert Store().get_note(nid)["updated_at"] == stamp


# ── 4. a deletion that arrives from sync empties the meeting here ───────────

def test_a_deletion_from_sync_removes_content_and_audio_here(two, fake):
    one, other = two
    nid = one.note("Zanzibarbudget review", words="the xylophonist costs seventeen million")
    one.sync()
    assert other.sync()["pulled"] == 1
    other.use()
    audio = audio_dir(nid) / "s1_0000_mic.flac"
    audio.write_bytes(b"fLaC" + b"\x00" * 512)
    time.sleep(0.01)
    one.use()
    one.store.delete_note(nid)
    one.sync()
    assert other.sync()["pulled"] == 1
    assert is_a_bare_tombstone(other.store, nid) and other.titles() == []
    assert not audio.exists()
    assert other.store.search_segments("xylophonist") == []
    assert other.sync()["pulled"] == 0 and is_a_bare_tombstone(other.store, nid)


def a_deleted_row_with_content(nid, when):
    """What a service from before this change sends for a deleted note: the
    mark, and everything the note ever held."""
    return {"id": nid, "title": "Zanzibarbudget review", "title_locked": 1, "my_notes": "pomegranatesecret",
            "summary": "## Notes\n- marmaladeforecast", "template_id": "general", "folder_id": None,
            "status": "ready", "error": "", "duration_s": 30.0, "created_at": when, "updated_at": when,
            "deleted": 1,
            "sessions": [{"id": new_id(), "note_id": nid, "n": 1, "offset_s": 0.0, "started_at": when,
                          "ended_at": when}],
            "segments": [{"id": new_id(), "note_id": nid, "session_n": 1, "source": "mic", "start_s": 0.0,
                          "end_s": 2.0, "text": "the xylophonist costs seventeen million", "created_at": when}]}


def test_content_sent_along_with_a_deletion_is_never_stored(home):
    store = Store()
    unknown = new_id()                                                       # a note this computer never had
    known, audio = a_full_meeting(store)                                     # and one it has
    time.sleep(0.005)
    when = now_iso()
    out = store.import_changes({"notes": [a_deleted_row_with_content(unknown, when),
                                          a_deleted_row_with_content(known, when)]})
    assert out == {"applied": 2}
    assert is_a_bare_tombstone(store, unknown) and is_a_bare_tombstone(store, known)
    assert store.get_note(known)["updated_at"] == when, "the deletion keeps its own time"
    assert not audio.exists()
    assert store.search_segments("xylophonist") == [] and store.list_notes(query="marmaladeforecast") == []


def test_an_older_copy_cannot_put_a_transcript_back_under_a_tombstone(home):
    store = Store()
    nid, _ = a_full_meeting(store)
    earlier = store.get_note(nid)["updated_at"]
    time.sleep(0.005)
    store.delete_note(nid)
    stale = dict(a_deleted_row_with_content(nid, earlier), deleted=0)        # another device's copy, from before
    assert store.import_changes({"notes": [stale]}) == {"applied": 0}
    assert is_a_bare_tombstone(store, nid)


def test_a_newer_edit_elsewhere_still_brings_a_deleted_meeting_back(home):
    """Last write wins, as it always has: a deletion is not stronger than an
    edit made after it on another device."""
    store = Store()
    nid, _ = a_full_meeting(store)
    store.delete_note(nid)
    time.sleep(0.005)
    newer = dict(a_deleted_row_with_content(nid, now_iso()), deleted=0, title="Edited on the phone")
    assert store.import_changes({"notes": [newer]}) == {"applied": 1}
    note = store.get_note(nid, with_segments=True)
    assert note["deleted"] == 0 and note["title"] == "Edited on the phone" and len(note["segments"]) == 1
    assert [n["id"] for n in store.list_notes(query="xylophonist")] == [nid]


def test_the_engine_names_one_place_for_what_a_tombstone_is():
    assert set(TOMBSTONE) == {"title", "title_locked", "my_notes", "summary", "template_id", "folder_id",
                              "status", "error", "duration_s"}
    assert store_mod.tombstone({"id": "a" * 32, "created_at": "c", "updated_at": "u", "title": "x"})["title"] == ""


def test_a_delete_is_in_the_database_file_at_once_not_only_in_the_log(tmp_path):
    from src.meetings.store import Store
    store = Store(tmp_path / "meetings.db")
    note = store.create_note(title="Quarterly zebracrossing review")
    store.delete_note(note["id"])
    assert b"zebracrossing" not in (tmp_path / "meetings.db").read_bytes()
