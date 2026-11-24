"""Settings, Privacy and data: "Delete all my data" deletes the meetings too
(src/privacy_data.py _delete_meetings, src/meetings/store.py Store.wipe).

It used to leave the meetings folder alone: every meeting's notes, its
transcript and its audio stayed on the disk. Now every meeting goes, audio
and all; the meetings' own settings and the person's templates stay, as
settings.json and snippets.json do.

Temporary folders only: no real data, no network.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import privacy_data as P                                                      # noqa: E402
from src.meetings.store import Store                                          # noqa: E402

SECRETS = ("Zanzibarbudget", "marmaladeforecast", "pomegranatesecret", "xylophonist")


@pytest.fixture
def data(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(d))
    monkeypatch.setenv("WAFFLER_MEETINGS_HOME", str(d / "meetings"))
    return d


def a_full_meeting(store, title="Zanzibarbudget review"):
    nid = store.create_note(title=title)["id"]
    session = store.start_session(nid)
    store.add_segments(nid, [{"source": "mic", "start_s": i, "end_s": i + 1.0,
                              "text": f"the xylophonist costs seventeen million {i}"} for i in range(40)])
    store.end_session(session["id"], nid, 40.0)
    store.update_note(nid, summary="## Notes\n- marmaladeforecast agreed", my_notes="typed: pomegranatesecret")
    audio = store.audio_root / nid / "s1_0000_mic.flac"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"fLaC" + b"what was said" * 200)
    return nid, audio


def everything_under(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file())


def test_delete_all_my_data_removes_every_meeting_and_its_audio(data):
    store = Store()
    for _ in range(3):
        a_full_meeting(store)
    deleted, _ = a_full_meeting(store, "Deleted earlier")
    store.delete_note(deleted)                                                # a tombstone goes too
    store.close()
    assert all(word.encode() in everything_under(data / "meetings") for word in SECRETS)

    assert P.delete_my_data(data) == {"ok": True, "failed": []}

    assert not (data / "meetings" / "audio").exists()
    raw = everything_under(data / "meetings")
    for word in SECRETS:
        assert word.encode() not in raw and word.lower().encode() not in raw, word
    after = Store()
    assert after.list_notes() == [] and after.export_changes("")["notes"] == []
    for table in ("notes", "segments", "sessions", "uploads", "segments_fts", "notes_fts"):
        assert after.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


def test_the_meetings_settings_and_the_persons_templates_stay(data):
    store = Store()
    a_full_meeting(store)
    store.set_settings({"keep_audio": False, "mic_device": "Desk microphone"})
    mine = store.save_template("Board meeting", "Decisions first.")
    store.save_folder("Clients")
    store.close()
    assert P.delete_my_data(data)["ok"]
    after = Store()
    assert after.settings()["keep_audio"] is False and after.settings()["mic_device"] == "Desk microphone"
    assert after.template(mine["id"])["prompt"] == "Decisions first."
    assert [f["name"] for f in after.folders()] == ["Clients"] and after.folders()[0]["note_count"] == 0
    assert len(after.templates()) == 7                                        # the six built in, and theirs


def test_a_running_engine_carries_on_with_no_meetings(data):
    """Waffler keeps running after Delete all my data: the engine's store is
    open while the meetings are deleted, and must work afterwards."""
    live = Store()
    nid, audio = a_full_meeting(live)
    assert len(live.list_notes()) == 1
    assert P.delete_my_data(data) == {"ok": True, "failed": []}
    assert live.list_notes() == [] and live.get_note(nid) is None and live.search_segments("xylophonist") == []
    assert not audio.exists()
    # A line of a recording that was still running has no note to go to.
    assert live.add_segments(nid, [{"start_s": 0, "end_s": 1, "text": "the xylophonist again"}]) == []
    new = live.create_note(title="The next meeting")
    live.add_segments(new["id"], [{"start_s": 0, "end_s": 1, "text": "a fresh start"}])
    assert [n["title"] for n in live.list_notes(query="fresh")] == ["The next meeting"]


def test_a_recording_that_was_running_leaves_no_audio_behind(data):
    from src.meetings.paths import audio_dir
    from src.meetings.service import Service
    svc = Service(store=Store(), source_factory=lambda *a, **k: None)
    nid, _ = a_full_meeting(svc.store)
    assert P.delete_my_data(data)["ok"]
    late = audio_dir(nid) / "s1_0001_mic.flac"                               # saved after the delete
    late.write_bytes(b"fLaC" + b"\x00" * 64)
    svc._after_recording(nid)                                                # the recorder finishing
    assert not late.exists() and svc.store.list_notes() == []


def test_a_database_that_cannot_be_opened_goes_with_its_folder(data):
    folder = data / "meetings"
    (folder / "audio" / ("a" * 32)).mkdir(parents=True)
    (folder / "audio" / ("a" * 32) / "s1_0000_mic.flac").write_bytes(b"fLaC")
    (folder / "meetings.db").write_bytes(b"this is not a database, but it may hold the xylophonist")
    assert P.delete_my_data(data) == {"ok": True, "failed": []}
    assert not folder.exists()


def test_audio_with_no_database_goes_too(data):
    folder = data / "meetings" / "audio" / ("b" * 32)
    folder.mkdir(parents=True)
    (folder / "s1_0000_mic.flac").write_bytes(b"fLaC")
    assert P.delete_my_data(data)["ok"] and not (data / "meetings").exists()


def test_no_meetings_folder_is_nothing_to_do(data):
    assert P.delete_my_data(data) == {"ok": True, "failed": []}
    assert not (data / "meetings").exists(), "nothing is made in order to be deleted"


def test_the_rest_of_delete_all_my_data_is_as_it_was(data):
    """Keys, words, snippets and settings stay; the Journal and the other
    recordings go, exactly as before the meetings were added."""
    for name in P.KEPT_FILES:
        (data / name).write_text("{}", encoding="utf-8")
    (data / "history.json").write_text(json.dumps([{"text": "a dictation"}]), encoding="utf-8")
    (data / "app.log").write_text("12:00:00  started\n", encoding="utf-8")
    for name in P.DATA_FOLDERS:
        (data / name).mkdir()
        (data / name / "take.wav").write_bytes(b"RIFF")
    a_full_meeting(Store())
    assert P.delete_my_data(data) == {"ok": True, "failed": []}
    assert all((data / name).exists() for name in P.KEPT_FILES)
    assert json.loads((data / "history.json").read_text(encoding="utf-8")) == []
    assert not (data / "app.log").exists() and not any((data / n).exists() for n in P.DATA_FOLDERS)
    assert P.DATA_FOLDERS == ("debug_audio", "unsent") and P.MEETINGS_FOLDER == "meetings"


def test_the_window_says_that_meetings_go():
    page = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "Delete your Journal, meetings, usage, recordings and logs?" in page
    assert "<b>Delete all my data.</b> Your Journal, your meetings (notes, transcripts and audio), usage, " \
           "recordings, logs and which tips you've seen." in page
    assert "Deleted your Journal, meetings, usage, recordings and logs." in (ROOT / "ui" / "app.js").read_text(
        encoding="utf-8")
