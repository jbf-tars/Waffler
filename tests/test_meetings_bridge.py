"""The Meetings page's calls, answered without a web server (src/meetings_bridge.py)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("WAFFLER_MEETINGS_HOME", str(tmp_path / "meetings"))
    monkeypatch.setenv("NATTER_NO_KEYRING", "1")
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from src import meetings_ctl
    monkeypatch.setattr(meetings_ctl, "_share_waffler_keys", lambda: None)
    c = meetings_ctl.MeetingsController()
    yield c
    c.shutdown()


def call(ctl, method, path, body=None, theme="dark"):
    from src import meetings_bridge
    return meetings_bridge.handle(ctl, method, path, body, waffler_theme=theme)


def test_the_page_starts_with_everything_it_needs_and_waffler_s_theme(ctl):
    r = call(ctl, "GET", "/app")
    assert r["status"] == 200
    for key in ("settings", "keys", "templates", "folders", "state", "system_audio_supported"):
        assert key in r["body"]
    assert r["body"]["name"] == "Waffler" and r["body"]["data_dir"] == ""       # no paths leak to the page
    assert r["body"]["settings"]["theme"] == "dark"
    assert call(ctl, "GET", "/app", theme="cream")["body"]["settings"]["theme"] == "light"
    assert call(ctl, "GET", "/app", theme="")["body"]["settings"]["theme"] == "auto"


def test_notes_folders_and_templates_round_trip(ctl):
    note = call(ctl, "POST", "/notes", {})["body"]
    folders = call(ctl, "POST", "/folders", {"name": "Work"})["body"]
    fid = folders[0]["id"]
    got = call(ctl, "PATCH", f"/notes/{note['id']}", {"title": "Weekly sync", "folder_id": fid})["body"]
    assert got["title"] == "Weekly sync" and got["folder_id"] == fid and got["title_locked"] == 1
    assert [n["id"] for n in call(ctl, "GET", f"/notes?q=&folder={fid}")["body"]] == [note["id"]]
    assert call(ctl, "GET", "/notes?q=weekly&folder=")["body"][0]["id"] == note["id"]
    assert call(ctl, "PATCH", f"/notes/{note['id']}", {"clear_folder": True})["body"]["folder_id"] is None
    assert call(ctl, "PUT", f"/folders/{fid}", {"name": "Clients"})["body"][0]["name"] == "Clients"
    assert call(ctl, "DELETE", f"/folders/{fid}")["body"] == []
    before = len(call(ctl, "GET", "/templates")["body"])
    assert len(call(ctl, "POST", "/templates", {"name": "Mine", "prompt": "Short."})["body"]) == before + 1
    assert call(ctl, "DELETE", f"/notes/{note['id']}")["body"] == {"ok": True}
    assert call(ctl, "GET", f"/notes/{note['id']}")["status"] == 404


def test_keys_can_be_read_but_never_set_from_the_page(ctl):
    assert call(ctl, "GET", "/keys")["status"] == 200
    for method, path in (("PUT", "/keys/groq"), ("DELETE", "/keys/groq"), ("POST", "/keys/import-waffler")):
        r = call(ctl, method, path, {"value": "sk-not-a-real-key"})
        assert r["status"] == 403 and "Waffler" in r["body"]["detail"]


def test_a_note_id_is_never_a_path(ctl, tmp_path):
    marker = tmp_path / "meetings" / "keep.txt"
    call(ctl, "GET", "/app")
    marker.write_text("still here")
    for bad in ("%2e%2e", "..", "%2e%2e%2f%2e%2e", "a%2fb"):
        assert call(ctl, "DELETE", f"/notes/{bad}/audio")["status"] == 404
        assert call(ctl, "DELETE", f"/notes/{bad}")["status"] == 404
    assert marker.read_text() == "still here" and (tmp_path / "meetings" / "meetings.db").exists()


def test_unknown_calls_and_bad_input_get_a_plain_refusal_never_an_exception(ctl):
    assert call(ctl, "GET", "/nope")["status"] == 404
    assert call(ctl, "POST", "/ask", {"question": "  "})["status"] == 400
    assert call(ctl, "POST", "/import", {})["status"] == 400
    assert call(ctl, "POST", "/record/stop")["body"]["note"] is None          # nothing was recording
    assert call(ctl, "POST", "/notes/missing/enhance", {})["status"] == 404
    assert call(ctl, None, None)["status"] in (404, 500)


def test_enhance_without_a_key_says_so_in_words(ctl):
    note = call(ctl, "POST", "/notes", {})["body"]
    r = call(ctl, "POST", f"/notes/{note['id']}/enhance", {})
    assert r["status"] == 400 and r["body"]["detail"] and "Traceback" not in r["body"]["detail"]
