"""A meeting from another app becomes an ordinary meeting (Store.import_external):
the one function "Import from Granola" and the Claude link's import_meeting
both use, with the account service's rules, so a meeting imported on a
computer and the same one imported through the account are the same meeting."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.meetings import store as store_mod          # noqa: E402
from src.meetings.store import Store, import_id      # noqa: E402

WHEN = "2026-10-02T09:30:00+01:00"
LINES = [{"source": "mic", "text": "Shall we start?", "start_s": 0},
         {"source": "system", "text": "Yes, go ahead.", "start_s": 4.5}]


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "m.db")


def bring(store, external_id="abc-123", **changes):
    args = {"title": "Kickoff with Priya", "meeting_at": WHEN, "summary": "### Decisions\n- Ship on Friday",
            "my_notes": "ask about budget", "lines": LINES, **changes}
    return store.import_external("Granola", external_id, **args)


def test_the_id_is_the_one_the_account_service_makes():
    # From natter/mcp_server.py _import_id, run on 25 November 2026.
    nid = import_id("Granola", "3f2b8c1e-0000-4000-8000-123456789abc")
    assert nid == "b40d35d36bb5424c22d28a2df2f15981"
    assert import_id(nid, "1:0") == "1c4ad1f681e81bc4eacd7e657a3642ae"       # a transcript's first line
    assert import_id("granola", "x") == import_id("GRANOLA", "x") == "fcc3d3fdc071b826606e240e9a090147"


def test_an_imported_meeting_is_an_ordinary_meeting(store):
    out = bring(store)
    assert out == {"ok": True, "id": import_id("Granola", "abc-123"), "created": True, "lines_added": 2,
                   "part_already_stored": False, "transcript_truncated": False}
    note = store.get_note(out["id"], with_segments=True)
    assert note["title"] == "Kickoff with Priya" and note["title_locked"] == 1 and note["status"] == "ready"
    assert note["created_at"] == "2026-10-02T08:30:00.000+00:00"
    assert note["summary"].startswith("### Decisions") and note["my_notes"] == "ask about budget"
    assert [(s["source"], s["text"], s["start_s"]) for s in note["segments"]] == [
        ("mic", "Shall we start?", 0.0), ("system", "Yes, go ahead.", 4.5)]
    assert [s["id"] for s in note["segments"]] == [import_id(out["id"], "1:0"), import_id(out["id"], "1:1")]
    assert note["duration_s"] == note["segments"][-1]["end_s"] > 4.5
    assert note["deleted"] == 0 and note["sessions"] == []


def test_it_is_found_by_search_and_goes_out_with_sync(store):
    before = store_mod.now_iso()
    nid = bring(store)["id"]
    assert [n["id"] for n in store.list_notes(query="Priya")] == [nid]         # the title
    assert [n["id"] for n in store.list_notes(query="budget")] == [nid]        # what was typed
    assert [h["note_id"] for h in store.search_segments("go ahead")] == [nid]  # what was said
    sent = store.export_changes(since="2026-11-01")["notes"]   # dated now, not at the meeting
    assert [n["id"] for n in sent] == [nid] and len(sent[0]["segments"]) == 2
    assert store.get_note(nid)["updated_at"] >= before


def test_importing_again_updates_and_never_duplicates(store):
    first = bring(store)
    second = bring(store, title="Kickoff (renamed there)", summary="### New notes")
    assert second["id"] == first["id"] and second["created"] is False
    assert second["lines_added"] == 0 and second["part_already_stored"] is True
    assert len(store.list_notes()) == 1 and len(store.segments(first["id"])) == 2
    note = store.get_note(first["id"])
    assert note["title"] == "Kickoff (renamed there)" and note["summary"] == "### New notes"
    assert note["my_notes"] == "ask about budget"
    # Nothing to say this time leaves what is there.
    bring(store, summary="", my_notes="", lines=[])
    assert store.get_note(first["id"])["summary"] == "### New notes"


def test_a_transcript_can_arrive_in_parts(store):
    base = {"title": "Long one", "meeting_at": WHEN, "summary": "", "my_notes": ""}
    one = store.import_external("Granola", "long", lines=[
        {"source": "mic", "text": "part one line one"}, {"source": "system", "text": "part one line two"}], **base)
    two = store.import_external("Granola", "long", part=2, lines=[{"source": "mic", "text": "part two"}], **base)
    again = store.import_external("Granola", "long", part=2, lines=[{"source": "mic", "text": "part two"}], **base)
    assert (one["lines_added"], two["lines_added"], again["lines_added"]) == (2, 1, 0)
    assert again["part_already_stored"] is True
    segments = store.segments(one["id"])
    assert [s["text"] for s in segments] == ["part one line one", "part one line two", "part two"]
    assert [s["start_s"] for s in segments] == sorted(s["start_s"] for s in segments)
    assert segments[2]["id"] == import_id(one["id"], "2:0")


def test_a_deleted_import_is_not_brought_back(store):
    nid = bring(store)["id"]
    store.delete_note(nid)
    out = bring(store)
    assert out == {"ok": False, "reason": "deleted", "id": nid}
    assert store.list_notes() == [] and store.segments(nid) == []
    assert store.get_note(nid)["title"] == ""


def test_an_import_cannot_touch_a_meeting_recorded_here(store):
    mine = store.create_note(title="Recorded here")
    store.add_segments(mine["id"], [{"source": "mic", "start_s": 0, "end_s": 1, "text": "my own words"}])
    before = store.get_note(mine["id"], with_segments=True)
    # Even naming the recorded meeting's own id as the other app's id.
    out = bring(store, external_id=mine["id"], title="Overwrite?")
    assert out["id"] != mine["id"]
    assert store.get_note(mine["id"], with_segments=True) == before
    # Ids made for recordings are random; ids made for imports are hashes of where they came from.
    assert out["id"] == import_id("Granola", mine["id"]) and len(out["id"]) == 32


def test_sizes_are_capped(store, monkeypatch):
    out = bring(store, title="T" * 500, summary="s" * 150_000, my_notes="n" * 150_000,
                lines=[{"source": "mic", "text": "w" * 9_000}])
    note = store.get_note(out["id"], with_segments=True)
    assert len(note["title"]) == 300 and len(note["summary"]) == 100_000 and len(note["my_notes"]) == 100_000
    assert len(note["segments"][0]["text"]) == 4_000
    # Past the size a transcript may be, no more lines are stored, and the answer says so.
    monkeypatch.setattr(store_mod, "IMPORT_TRANSCRIPT", 50)
    cut = bring(store, external_id="big", lines=[{"source": "mic", "text": "a" * 20} for _ in range(5)])
    assert cut["lines_added"] == 2 and cut["transcript_truncated"] is True
    # The Claude link's tool refuses the part instead, as the account service does.
    no = bring(store, external_id="big2", whole_parts_only=True,
               lines=[{"source": "mic", "text": "a" * 20} for _ in range(5)])
    assert no["ok"] is False and no["reason"] == "too_long" and store.segments(no["id"]) == []


def test_control_characters_and_empty_lines_are_dropped(store):
    out = bring(store, title="  A\x00 title\x07 ", lines=[
        {"source": "mic", "text": "  spaced   out \x00 "}, {"source": "mic", "text": "   "},
        {"source": "nobody", "text": "unknown source is the microphone"}, {"source": "system", "text": None}])
    note = store.get_note(out["id"], with_segments=True)
    assert note["title"] == "A title"
    assert [(s["source"], s["text"]) for s in note["segments"]] == [
        ("mic", "spaced out"), ("mic", "unknown source is the microphone")]


def test_folders_by_name(store):
    a = bring(store, external_id="a", folder_name="Clients")
    b = bring(store, external_id="b", folder_name="  clients ")
    folders = store.folders()
    assert [(f["name"], f["note_count"]) for f in folders] == [("Clients", 2)]
    assert store.get_note(a["id"])["folder_id"] == store.get_note(b["id"])["folder_id"] == folders[0]["id"]
    c = bring(store, external_id="c")
    assert store.get_note(c["id"])["folder_id"] is None
    assert bring(store, external_id="d", folder_id="ghost") == {
        "ok": False, "reason": "no_folder", "id": import_id("Granola", "d")}
    assert store.get_note(import_id("Granola", "d")) is None


def test_no_more_than_two_hundred_folders(store, monkeypatch):
    monkeypatch.setattr(store_mod, "MAX_FOLDERS", 3)
    for name in ("One", "Two", "Three"):
        assert store.folder_named(name, store_mod.MAX_FOLDERS)["made"] is True
    assert store.folder_named("Four", store_mod.MAX_FOLDERS) is None
    assert store.folder_named("TWO", store_mod.MAX_FOLDERS)["made"] is False
    assert store_mod.Store.folder_named.__defaults__ == (200,)
    assert store.folder_named("   ") is None


def test_what_is_refused(store):
    assert store.import_external("Granola", "x", title="T", meeting_at="last Tuesday")["reason"] == "bad_date"
    assert store.import_external("Granola", "", title="T", meeting_at=WHEN)["reason"] == "no_id"
    assert store.import_external("", "x", title="T", meeting_at=WHEN)["reason"] == "no_id"
    assert store.list_notes() == []
    # No title is "Untitled"; a time with no zone is this computer's.
    out = store.import_external("Granola", "x", title="", meeting_at="2026-10-02T09:30:00Z")
    assert store.get_note(out["id"])["title"] == "Untitled"
    assert store.get_note(out["id"])["created_at"] == "2026-10-02T09:30:00.000+00:00"
