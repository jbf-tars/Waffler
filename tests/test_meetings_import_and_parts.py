"""Two rough edges in the Meetings tab.

1. An imported audio file with nothing set up (no speech key, no free model,
   no Waffler Plus) is a calm state like recording is, not a red error. The
   audio is kept; the page offers "Download the free model" and "Use your own
   key". With speech but no way to write notes, an import ends as a recording
   does: the transcript, and "Notes need a key."
2. On Waffler Plus the Meetings page says which part a long meeting is on,
   in the words Home uses, from the same place (recorder.progress).
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import meetings_ctl  # noqa: E402
from meetings import paths  # noqa: E402
from meetings import transcribe as mt  # noqa: E402
from meetings.store import wait_until  # noqa: E402
from src.meetings import llm  # noqa: E402
from test_free_meetings import _record, call, ctl, machine  # noqa: E402,F401
from test_plus_meetings import _speech, plus  # noqa: E402,F401


def _page():
    (one,) = sorted((ROOT / "ui" / "meetings-app" / "assets").glob("*.js"))
    return one.read_text(encoding="utf-8")


# ── 1. an import with nothing set up ─────────────────────────────────────────

def test_an_import_with_nothing_set_up_keeps_the_audio_and_shows_no_error(machine, ctl):
    note_id = _record(ctl)                       # the same path an imported file takes
    note = call(ctl, "GET", f"/notes/{note_id}")["body"]
    assert note["status"] == "ready" and note["segments"] == []
    assert note["error"] == "", "nothing red for the page to draw"
    assert note["has_audio"] is True, "nothing lost"
    info = call(ctl, "GET", "/app")["body"]
    assert info["local"] == {"speech": False, "writing": False}
    assert not any(k["set"] for k in info["keys"].values())
    assert list(paths.audio_dir(note_id).glob("*.flac")), "the audio file is on disk"


def test_the_audio_can_be_written_down_once_something_is_set_up(machine, ctl):
    note_id = _record(ctl)
    machine.speech_model = True
    machine.choose(transcription_mode="local")
    assert call(ctl, "POST", f"/notes/{note_id}/retranscribe", {})["status"] == 200
    store = ctl._svc().store
    assert wait_until(lambda: len(store.segments(note_id)) == 2, timeout=20)
    note = call(ctl, "GET", f"/notes/{note_id}")["body"]
    assert [s["text"] for s in note["segments"]][1] == "Tom books the electrician."
    assert note["error"] == ""


def test_an_import_with_speech_but_no_notes_ends_like_a_recording(machine, ctl):
    machine.speech_model = True
    machine.choose(transcription_mode="local")
    note_id = _record(ctl)
    note = call(ctl, "GET", f"/notes/{note_id}")["body"]
    assert note["status"] == "ready" and len(note["segments"]) == 2
    assert note["summary"] == "" and note["error"] == ""      # the page says "Notes need a key."
    assert machine.wrote == []


def test_the_page_says_it_calmly_in_few_words_and_in_the_recording_state_s_own_words():
    page = _page()
    assert page.count('"Download the free model"') >= 2 and page.count('"Use your own key"') >= 2
    assert '"Your audio is kept."' in page
    for label in ("Your audio is kept.", "Download the free model", "Use your own key", "Transcribe it"):
        assert len(re.findall(r"[A-Za-z']+", label)) <= 9
    assert len("Download the free model".split()) <= 4 and len("Use your own key".split()) <= 4
    # The calm state is the quiet style, never the red problem line.
    at = page.index('"Your audio is kept."')
    assert 'className:"problem"' not in page[at - 120:at + 400]


# ── 2. which part, on the Meetings page ──────────────────────────────────────

def test_the_meetings_page_is_told_which_part_from_the_place_home_reads(plus, monkeypatch):
    monkeypatch.setattr(meetings_ctl._engine("transcribe"), "PLUS_PART_S", 60.0)
    ctl = meetings_ctl.MeetingsController()
    try:
        service = ctl._svc()
        seen = []

        def hear(seconds, n):
            rows = call(ctl, "GET", "/notes")["body"]
            nid = rows[0]["id"]
            seen.append((rows[0].get("progress"), call(ctl, "GET", f"/notes/{nid}")["body"].get("progress"),
                         ctl.list()[0].get("progress")))
            return [{"start_s": 1.0, "end_s": 2.0, "text": f"Part {n} speaking."}]
        plus.hear = hear
        note = service.store.create_note(template_id="general")
        service.ingest_audio(note["id"], _speech(150.0))
        assert wait_until(lambda: service.store.get_note(note["id"])["status"] == "ready", timeout=30)
        n = len(plus.transcriptions)
        assert n >= 3
        for i, (listed, page, home) in enumerate(seen, 1):
            assert listed == page == home == f"Part {i} of {n}"
        done = call(ctl, "GET", f"/notes/{note['id']}")["body"]
        assert "progress" not in done and "progress" not in call(ctl, "GET", "/notes")["body"][0]
    finally:
        ctl.shutdown()


def test_the_page_uses_home_s_words_on_the_note_and_in_the_list():
    page = _page()
    assert "Transcribing. " in page
    assert len(re.findall(r'Transcribing\. \$\{', page)) == 1, "one place makes the words"
    home = (ROOT / "ui" / "meetings.js").read_text(encoding="utf-8")
    assert "'Transcribing. ' + n.progress + '.'" in home
