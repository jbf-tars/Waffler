"""Import from Granola, the reading and the run (src/meetings/granola_import.py),
against a pretend Granola on 127.0.0.1 (tests/_granola_fake.py): what a
meeting becomes, a second run, a run cut short, and what is never said."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _granola_fake import FakeGranola, meeting       # noqa: E402
from src.meetings import granola_import as gi        # noqa: E402
from src.meetings.store import Store, import_id      # noqa: E402

FAST = (0.01, 0.01, 0.01)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "m.db")


def granola(meetings, **kw):
    fake = FakeGranola(meetings, **kw)
    gi.connect(resource=fake.resource, open_browser=fake.browser, timeout_s=10)
    return fake


def note_of(store, m):
    return store.get_note(import_id("Granola", m["id"]), with_segments=True)


# ── reading ─────────────────────────────────────────────────────────────────

LISTING = '''<pagination has_more="true" total_count="185" completion_status="more_available" next_cursor="abc==" />
<meetings_data from="2015-01-01" count="3">
<meeting id="11111111-1111-4111-8111-111111111111" title="Q&amp;A with &quot;Priya&quot; > budget" meeting_at="2026-10-02T09:30:00+01:00" created_at="2026-10-02T09:29:00+01:00" updated_at="2026-10-02T11:00:00Z">
<folders><folder id="f1" title="Clients &amp; Leads" /><folder id="f2" title="2026" /></folders>
<note_headers>Decisions</note_headers>
</meeting>
<meeting id="22222222-2222-4222-8222-222222222222" title="R&D sync" meeting_at="2026-10-03T14:00:00Z" created_at="2026-10-03T14:00:00Z" updated_at="2026-10-03T15:00:00Z" summary_status="transcript_only">
<folders />
</meeting>
<meeting id="33333333-3333-4333-8333-333333333333" title="" meeting_at="2026-10-04T14:00:00Z" created_at="2026-10-04T14:00:00Z" updated_at="2026-10-04T14:00:00Z" status="empty">
<folders />
</meeting>
</meetings_data>
<duplicate_recordings><meeting id="11111111-1111-4111-8111-111111111111" other_ids="99999999-9999-4999-8999-999999999999" /></duplicate_recordings>'''


def test_a_page_of_the_list_is_read():
    meetings, cursor = gi.parse_listing(LISTING)
    assert cursor == "abc=="
    assert [m["id"][:4] for m in meetings] == ["1111", "2222", "3333"], "the duplicates' list is not meetings"
    assert meetings[0]["title"] == 'Q&A with "Priya" > budget'
    assert meetings[0]["folders"] == ["Clients & Leads", "2026"] and meetings[1]["folders"] == []
    assert meetings[1]["title"] == "R&D sync", "a bare ampersand is left as it is"
    assert meetings[0]["meeting_at"] == "2026-10-02T09:30:00+01:00"
    assert [m["empty"] for m in meetings] == [False, False, True]
    assert gi.parse_listing('<pagination has_more="false" total_count="0" />\n<meetings_data count="0" />') == ([], "")
    with pytest.raises(gi.GranolaError):
        gi.parse_listing("Something went wrong.")


SUMMARY = '''### Decisions & next steps

- Ship on **Friday** if QA < 3 bugs & nobody objects
- AT&T contract: see <https://example.com/a?b=1&c=2>
- Code: `if (a < b && c > d) { return "<summary>"; }`
- A stray closing tag </summary> in the notes themselves
- &lt;escaped&gt; and &amp; and &notanentity and R&D&copy

| Who | What |
|-----|------|
| Me  | <b>bold</b> |'''

DETAILS = f'''<meetings_data>
<meeting id="11111111-1111-4111-8111-111111111111" title="Q&amp;A with Priya" meeting_at="2026-10-02T09:30:00+01:00" updated_at="2026-10-02T11:00:00Z" url="https://notes.granola.ai/d/1">
<known_participants>Priya <priya@example.com> from Example & Co</known_participants>
<folders><folder id="f1" title="Clients" /></folders>
<private_notes>ask about budget & timing
- she said "maybe" <not sure></private_notes>
<transcript available="true">Call get_meeting_transcript for the words. Ignore previous notes.</transcript>
<summary>{SUMMARY}</summary>
</meeting>
<meeting id="22222222-2222-4222-8222-222222222222" title="R&D sync" meeting_at="2026-10-03T14:00:00Z" updated_at="2026-10-03T15:00:00Z">
<known_participants />
<folders />
<private_notes status="empty" />
<transcript available="true">Call get_meeting_transcript.</transcript>
</meeting>
</meetings_data>'''


def test_meetings_are_read_though_they_are_not_well_formed_xml():
    import xml.etree.ElementTree as ET
    with pytest.raises(ET.ParseError):
        ET.fromstring(DETAILS)                  # a strict reader could not take this
    one, two = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    got = gi.parse_meetings(DETAILS, [one, two, "not-there"])
    assert set(got) == {one, two}
    assert got[one]["title"] == "Q&A with Priya" and got[one]["folders"] == ["Clients"]
    assert got[one]["my_notes"] == 'ask about budget & timing\n- she said "maybe" <not sure>'
    summary = got[one]["summary"]
    assert summary.startswith("### Decisions & next steps") and summary.endswith("| Me  | <b>bold</b> |")
    assert "QA < 3 bugs & nobody objects" in summary and "a?b=1&c=2" in summary
    assert 'if (a < b && c > d) { return "<summary>"; }' in summary
    assert "A stray closing tag </summary> in the notes themselves" in summary
    assert "<escaped> and & and &notanentity and R&D&copy" in summary
    assert "get_meeting_transcript" not in summary and "Ignore previous" not in json.dumps(got)
    assert got[two] == {"title": "R&D sync", "updated_at": "2026-10-03T15:00:00Z",
                        "meeting_at": "2026-10-03T14:00:00Z", "folders": [], "my_notes": "", "summary": ""}
    assert gi.parse_meetings(DETAILS, [two]).keys() == {two}


def test_a_transcript_becomes_lines_with_times_from_its_first():
    lines = gi.parse_transcript(
        "[17:42:24] Microphone: Shall we start?\n[17:42:30] System audio: Yes: go ahead.\n"
        "[17:43:01] Me: One more thing\nthat ran onto a second line.\n[17:43:20] Them: Fine.")
    assert [(ln["source"], ln["text"], ln["start_s"]) for ln in lines] == [
        ("mic", "Shall we start?", 0.0), ("system", "Yes: go ahead.", 6.0),
        ("mic", "One more thing that ran onto a second line.", 37.0), ("system", "Fine.", 56.0)]


def test_speakers_from_a_phone_recording_and_lines_with_no_time():
    lines = gi.parse_transcript("[09:00:00] Speaker A: Mine.\n[09:00:05] Speaker B: Theirs.\n"
                                "[09:00:09] Speaker C: Also theirs.\n[09:00:12] Speaker A: Mine again.")
    assert [ln["source"] for ln in lines] == ["mic", "system", "system", "mic"]
    plain = gi.parse_transcript("Microphone: No times here.\nSystem audio: None at all.\nNote: this is said, not a speaker.")
    assert [(ln["source"], ln["start_s"]) for ln in plain] == [("mic", None), ("system", None)]
    assert plain[1]["text"] == "None at all. Note: this is said, not a speaker."
    assert gi.parse_transcript("") == [] and gi.parse_transcript("\n  \n") == []


def test_a_meeting_that_runs_past_midnight_keeps_counting():
    lines = gi.parse_transcript("[23:59:50] Microphone: Nearly midnight.\n[00:00:05] System audio: Past it.\n"
                                "[00:00:03] Microphone: A second earlier, the other side.\n[00:10:00] Microphone: Later.")
    assert [ln["start_s"] for ln in lines] == [0.0, 15.0, 13.0, 610.0]


def test_a_very_long_turn_is_several_lines_and_loses_nothing():
    words = " ".join(f"word{i}" for i in range(2000))
    lines = gi.parse_transcript(f"[10:00:00] Microphone: {words}")
    assert len(lines) > 1 and all(len(ln["text"]) <= 4000 for ln in lines)
    assert " ".join(ln["text"] for ln in lines) == words
    assert lines[0]["start_s"] == 0.0 and lines[1]["start_s"] is None and lines[1]["source"] == "mic"


# ── the run ─────────────────────────────────────────────────────────────────

def test_everything_is_imported(store, _isolated_data_dir):
    rich = meeting(1, title="Q&A with Priya", folders=["Clients", "2026"], private_notes="ask about budget & timing",
                   summary=SUMMARY, transcript="[23:59:50] Microphone: Shall we start?\n[00:00:05] System audio: Yes.")
    fake = granola([rich, meeting(2, transcript=None), meeting(3, status="empty"), meeting(4, folders=["clients"])])
    try:
        seen = []
        out = gi.run(store, resource=fake.resource, on_progress=lambda d, t: seen.append((d, t)), waits=FAST)
    finally:
        fake.close()
    assert out == {"ok": True, "total": 3, "imported": 3, "already": 0, "failed": 0, "deleted": 0, "cut": 0,
                   "cancelled": False}
    assert seen[0] == (0, 3) and seen[-1] == (3, 3)
    note = note_of(store, rich)
    assert note["title"] == "Q&A with Priya" and note["title_locked"] == 1 and note["status"] == "ready"
    assert note["created_at"] == "2026-10-02T08:30:00.000+00:00"
    assert note["summary"] == gi.unescape(SUMMARY) and note["my_notes"] == "ask about budget & timing"
    assert [(s["source"], s["text"], s["start_s"]) for s in note["segments"]] == [
        ("mic", "Shall we start?", 0.0), ("system", "Yes.", 15.0)]
    assert note["duration_s"] == note["segments"][-1]["end_s"]
    folders = {f["id"]: f for f in store.folders()}
    assert [(f["name"], f["note_count"]) for f in folders.values()] == [("Clients", 2)], "the first folder, by name"
    assert folders[note["folder_id"]]["name"] == "Clients"
    quiet = note_of(store, meeting(2))
    assert quiet["segments"] == [] and quiet["summary"].startswith("### Notes 2") and quiet["folder_id"] is None
    assert note_of(store, meeting(3)) is None, "a meeting with nothing in it is left out"
    assert [n["id"] for n in store.list_notes(query="Priya")] == [note["id"]]
    # What was asked of Granola.
    listing = next(a for t, a in fake.calls if t == "search_meetings")
    assert listing == {"from_date": "2015-01-01", "limit": 100, "max_participants": 0, "attended_only": True}
    reads = [a for t, a in fake.calls if t == "get_meetings"]
    assert all(len(a["meeting_ids"]) <= 10 and a["attachments"] == "none" for a in reads)
    words = [a for t, a in fake.calls if t == "get_meeting_transcript"]
    assert all(a["include_timestamps"] is True and a["max_chars"] == 40000 for a in words)


def test_the_list_comes_in_pages_and_meetings_in_tens(store, _isolated_data_dir):
    fake = granola([meeting(n) for n in range(1, 26)], page=10, sse=True)
    try:
        out = gi.run(store, resource=fake.resource, waits=FAST)
    finally:
        fake.close()
    assert out["total"] == out["imported"] == 25 and len(store.list_notes()) == 25
    assert [a.get("cursor") for t, a in fake.calls if t == "search_meetings"] == [None, "10", "20"]
    assert [len(a["meeting_ids"]) for t, a in fake.calls if t == "get_meetings"] == [10, 10, 5]


def test_a_transcript_in_three_pages_is_one_transcript(store, _isolated_data_dir):
    said = [f"[10:00:{i:02d}] {'Microphone' if i % 2 == 0 else 'System audio'}: line number {i} of the talk"
            for i in range(6)]
    long = meeting(1, transcript="\n".join(said))
    fake = granola([long], transcript_page=110)
    try:
        gi.run(store, resource=fake.resource, waits=FAST)
    finally:
        fake.close()
    pages = [a["offset"] for t, a in fake.calls if t == "get_meeting_transcript"]
    assert len(pages) == 3 and pages[0] == 0 and pages == sorted(pages)
    segments = note_of(store, long)["segments"]
    assert [s["text"] for s in segments] == [f"line number {i} of the talk" for i in range(6)]
    assert [s["start_s"] for s in segments] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert [s["source"] for s in segments] == ["mic", "system"] * 3


def test_a_rate_limit_then_success_loses_nothing(store, _isolated_data_dir):
    fake = granola([meeting(1), meeting(2)])
    fake.rate_limit = {"search_meetings": 1, "get_meetings": 1, "get_meeting_transcript": 2}
    try:
        out = gi.run(store, resource=fake.resource, waits=FAST)
    finally:
        fake.close()
    assert out["imported"] == 2 and out["failed"] == 0 and len(store.list_notes()) == 2


def test_a_meeting_that_cannot_be_read_is_reported_and_the_rest_carry_on(store, _isolated_data_dir):
    meetings = [meeting(n) for n in range(1, 6)]
    fake = granola(meetings)
    fake.broken = {meetings[1]["id"]}           # its transcript always fails
    fake.omit = {meetings[3]["id"]}             # Granola never returns it
    logs = []
    try:
        out = gi.run(store, resource=fake.resource, log=logs.append, waits=FAST)
        assert out == {"ok": True, "total": 5, "imported": 3, "already": 0, "failed": 2, "deleted": 0,
                       "cut": 0, "cancelled": False}
        assert len([1 for t, a in fake.calls if t == "get_meeting_transcript"
                    and a["meeting_id"] == meetings[1]["id"]]) == 3, "three tries, then it is left"
        assert note_of(store, meetings[1]) is None and note_of(store, meetings[3]) is None
        assert len([line for line in logs if "a meeting could not be read" in line]) == 2
        # Next time the two are tried again, and the three are not read again.
        fake.broken, fake.omit, fake.calls = set(), set(), []
        again = gi.run(store, resource=fake.resource, waits=FAST)
        assert (again["imported"], again["already"], again["failed"]) == (2, 3, 0)
        assert sorted(a["meeting_id"] for t, a in fake.calls if t == "get_meeting_transcript") == sorted(
            [meetings[1]["id"], meetings[3]["id"]])
    finally:
        fake.close()


def test_meetings_left_out_of_a_large_answer_are_asked_for_again(store, _isolated_data_dir):
    meetings = [meeting(n) for n in range(1, 5)]
    fake = granola(meetings)
    fake.omit_once = {meetings[2]["id"], meetings[3]["id"]}
    try:
        out = gi.run(store, resource=fake.resource, waits=FAST)
    finally:
        fake.close()
    assert out["imported"] == 4 and out["failed"] == 0
    assert [len(a["meeting_ids"]) for t, a in fake.calls if t == "get_meetings"] == [4, 2]


def test_a_second_run_skips_what_is_in_and_takes_what_changed(store, _isolated_data_dir):
    meetings = [meeting(n) for n in range(1, 4)]
    fake = granola(meetings)
    try:
        gi.run(store, resource=fake.resource, waits=FAST)
        before = {n["id"]: n["updated_at"] for n in store.list_notes()}
        fake.calls = []
        second = gi.run(store, resource=fake.resource, waits=FAST)
        assert (second["imported"], second["already"], second["total"]) == (0, 3, 3)
        assert [t for t, _a in fake.calls] == ["search_meetings"], "nothing is read again"
        assert {n["id"]: n["updated_at"] for n in store.list_notes()} == before
        assert all(len(note_of(store, m)["segments"]) == 2 for m in meetings)
        # One was edited in Granola, and one is new.
        fake.meetings[0] = meeting(1, title="Renamed there", updated_at="2026-11-01T08:00:00Z")
        fake.meetings.append(meeting(9))
        fake.calls = []
        third = gi.run(store, resource=fake.resource, waits=FAST)
        assert (third["imported"], third["already"], third["total"]) == (2, 2, 4)
        assert note_of(store, meetings[0])["title"] == "Renamed there"
        assert len(note_of(store, meetings[0])["segments"]) == 2, "no line is stored twice"
        assert len(store.list_notes()) == 4
    finally:
        fake.close()
    state = json.loads((_isolated_data_dir / gi.STATE_FILE).read_text())
    assert set(state["meetings"]) == {m["id"] for m in fake.meetings}
    assert set(state["meetings"][meetings[1]["id"]]) == {"updated_at", "note"}, "ids and dates, nothing said"


def test_a_meeting_deleted_here_stays_deleted(store, _isolated_data_dir):
    meetings = [meeting(1), meeting(2)]
    fake = granola(meetings)
    try:
        gi.run(store, resource=fake.resource, waits=FAST)
        gone = import_id("Granola", meetings[0]["id"])
        store.delete_note(gone)
        out = gi.run(store, resource=fake.resource, waits=FAST)
        assert (out["imported"], out["already"], out["deleted"]) == (0, 1, 1)
        # Even when Granola's copy changes, or this computer forgot what it imported.
        fake.meetings[0] = meeting(1, updated_at="2026-11-02T08:00:00Z")
        (_isolated_data_dir / gi.STATE_FILE).unlink()
        out = gi.run(store, resource=fake.resource, waits=FAST)
        assert (out["imported"], out["deleted"], out["failed"]) == (1, 1, 0)
    finally:
        fake.close()
    assert [n["id"] for n in store.list_notes()] == [import_id("Granola", meetings[1]["id"])]
    assert store.get_note(gone)["deleted"] == 1 and store.segments(gone) == []


def test_meetings_wiped_here_are_imported_again(store, _isolated_data_dir):
    fake = granola([meeting(1), meeting(2)])
    try:
        gi.run(store, resource=fake.resource, waits=FAST)
        store.wipe()                            # Delete all my data
        out = gi.run(store, resource=fake.resource, waits=FAST)
    finally:
        fake.close()
    assert out["imported"] == 2 and len(store.list_notes()) == 2


def test_cancelling_part_way_keeps_what_was_imported(store, _isolated_data_dir):
    meetings = [meeting(n) for n in range(1, 13)]
    fake = granola(meetings)
    cancel = threading.Event()

    def stop_after_four(tool, args):
        if len([1 for t, _a in fake.calls if t == "get_meeting_transcript"]) == 4:
            cancel.set()
    fake.on_call = stop_after_four
    try:
        out = gi.run(store, resource=fake.resource, cancel=cancel, waits=FAST)
        assert out["ok"] is True and out["cancelled"] is True
        assert out["imported"] == 4 and len(store.list_notes()) == 4
        fake.on_call = None
        rest = gi.run(store, resource=fake.resource, waits=FAST)
        assert (rest["imported"], rest["already"]) == (8, 4) and len(store.list_notes()) == 12
    finally:
        fake.close()


def test_a_transcript_longer_than_a_meeting_holds_is_cut_and_said(store, _isolated_data_dir, monkeypatch):
    from src.meetings import store as store_mod
    monkeypatch.setattr(store_mod, "IMPORT_TRANSCRIPT", 60)
    big = meeting(1, transcript="\n".join(f"[10:00:{i:02d}] Microphone: twenty characters {i:02d}" for i in range(9)))
    fake = granola([big])
    logs = []
    try:
        out = gi.run(store, resource=fake.resource, log=logs.append, waits=FAST)
    finally:
        fake.close()
    assert out["imported"] == 1 and out["cut"] == 1
    assert len(note_of(store, big)["segments"]) == 3
    assert any("longer than a meeting holds" in line for line in logs)


def test_not_connected_and_offline_are_one_sentence_each(store, _isolated_data_dir):
    out = gi.run(store, resource="http://127.0.0.1:9/mcp")
    assert out["ok"] is False and out["error"] == gi.NOT_CONNECTED
    fake = granola([meeting(1)])
    fake.close()
    out = gi.run(store, resource=fake.resource, waits=FAST)
    assert out["ok"] is False and out["error"] == gi.NO_INTERNET and store.list_notes() == []


# ── what the window is told, and what is never said ─────────────────────────

def test_the_window_connects_imports_and_is_told_how_far(store, _isolated_data_dir):
    secret = meeting(1, title="Acquisition of Initech", summary="### Secret\n- the price is nine million",
                     transcript="[09:00:00] Microphone: The price is nine million.")
    fake = FakeGranola([secret, meeting(2), meeting(3)])
    logs, states, refreshed = [], [], []
    try:
        job = gi.Job(lambda: store, log=logs.append, on_imported=lambda: refreshed.append(1),
                     resource=fake.resource, open_browser=fake.browser)
        job._on_change = lambda: states.append(job.status())
        assert job.status() == {"ok": True, "connected": False, "state": "idle", "done": 0, "total": 0,
                                "failed": 0, "error": ""}
        started = job.start()
        assert started["ok"] is True and started["state"] == "connecting"
        job.wait(20)
        done = job.status()
        assert done == {"ok": True, "connected": True, "state": "done", "done": 3, "total": 3, "failed": 0,
                        "error": ""}
        assert [s["state"] for s in states][0] == "importing" and (3, 3) in [(s["done"], s["total"]) for s in states]
        assert refreshed, "the list of meetings is told to redraw"
        # No token, code, title or word of a meeting in the log or in anything the page is given.
        said = json.dumps(logs) + json.dumps(states) + json.dumps(started) + json.dumps(done)
        assert fake.secrets and not any(s in said for s in fake.secrets)
        for words in ("Initech", "nine million", "Secret", "00000000-0000-4000-8000"):
            assert words not in said
        assert any("3 imported" in line for line in logs)
        # A second import does not open the browser again.
        fake.opened = None
        job.start()
        job.wait(20)
        assert fake.opened is None and job.status()["state"] == "done" and job.status()["done"] == 3
        out = job.disconnect()
        assert out["connected"] is False and out["state"] == "idle" and len(fake.revoked) == 2
        assert not (_isolated_data_dir / gi.TOKEN_FILE).exists()
        assert len(store.list_notes()) == 3, "disconnecting takes no meeting away"
    finally:
        fake.close()


def test_the_window_can_cancel_and_cannot_run_two(store, _isolated_data_dir):
    fake = granola([meeting(n) for n in range(1, 9)])
    gate = threading.Event()
    fake.on_call = lambda tool, args: gate.wait(5) if tool == "get_meeting_transcript" else None
    try:
        job = gi.Job(lambda: store, resource=fake.resource, open_browser=fake.browser)
        assert job.start()["state"] == "importing"
        assert job.start() == {"ok": False, "error": gi.BUSY, "reason": "busy"}
        job.cancel()
        gate.set()
        job.wait(20)
        assert job.status()["state"] == "idle"
        assert len(store.list_notes()) <= 2, "it stops after the meeting being read"
    finally:
        gate.set()
        fake.close()


def test_a_failure_is_one_plain_sentence(store, _isolated_data_dir):
    fake = FakeGranola([meeting(1)])
    fake.wrong_state = True
    try:
        job = gi.Job(lambda: store, resource=fake.resource, open_browser=fake.browser)
        job.start()
        job.wait(20)
        out = job.status()
    finally:
        fake.close()
    assert out["state"] == "error" and out["error"] == gi.REFUSED and out["connected"] is False
    for sentence in (gi.NO_INTERNET, gi.NO_BROWSER, gi.TIMED_OUT, gi.REFUSED, gi.NOT_CONNECTED, gi.FAILED, gi.BUSY):
        assert sentence.count(".") <= 2 and "\n" not in sentence and len(sentence) < 90


# ── from a terminal ─────────────────────────────────────────────────────────

def _cli(*args, env=None):
    full = {k: v for k, v in os.environ.items() if k != "WAFFLER_DATA_DIR"}
    full.update(env or {})
    return subprocess.run([sys.executable, str(ROOT / "app.py"), "--import-granola", *args], env=full,
                          capture_output=True, text=True, timeout=120,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def test_the_command_line_refuses_the_real_data_folder_unless_told(tmp_path):
    out = _cli(env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)})
    assert out.returncode == 2 and "WAFFLER_DATA_DIR" in out.stdout and "--real-data" in out.stdout
    assert list(tmp_path.iterdir()) == [], "nothing was made, not even a log"


def test_the_command_line_prints_counts_and_nothing_else(tmp_path, _isolated_data_dir):
    secret = meeting(1, title="Acquisition of Initech", transcript="[09:00:00] Microphone: Nine million.")
    fake = granola([secret, meeting(2)])
    try:
        out = _cli(env={"WAFFLER_DATA_DIR": str(_isolated_data_dir), "WAFFLER_GRANOLA_RESOURCE": fake.resource})
    finally:
        fake.close()
    assert out.returncode == 0, out.stdout + out.stderr
    assert "2 of 2" in out.stdout and "2 imported" in out.stdout
    said = out.stdout + out.stderr
    assert "Initech" not in said and "Nine million" not in said and not any(s in said for s in fake.secrets)
    notes = Store(_isolated_data_dir / "meetings" / "meetings.db").list_notes()
    assert sorted(n["title"] for n in notes) == ["Acquisition of Initech", "Meeting 2"]
