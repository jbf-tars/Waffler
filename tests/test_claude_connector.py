"""The connector Claude starts to read meetings and dictations
(src/claude_connector.py): the protocol, the tools, and the one entry it
writes into the Claude desktop app's settings file."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import claude_connector as cc          # noqa: E402
from src.meetings.store import Store            # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A data folder of its own with one meeting and three dictations, and a
    pretend Claude settings folder."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(data))
    monkeypatch.delenv("WAFFLER_MEETINGS_HOME", raising=False)
    claude = tmp_path / "Claude"
    claude.mkdir()
    monkeypatch.setenv("WAFFLER_CLAUDE_CONFIG", str(claude / "claude_desktop_config.json"))
    store = Store()
    note = store.create_note(title="Call with the supplier")
    store.add_segments(note["id"], [
        {"source": "mic", "start_s": 0.0, "end_s": 2.0, "text": "When does the quote arrive?"},
        {"source": "system", "start_s": 2.0, "end_s": 5.0, "text": "The quote arrives on Tuesday."}])
    store.update_note(note["id"], summary="## Quote\n- Arrives Tuesday.", status="ready", duration_s=300)
    (data / "history.json").write_text(json.dumps([
        {"id": 1, "timestamp": "2026-11-18T09:00:00", "text": "Book the van for Friday", "app": "Notes"},
        {"id": 2, "timestamp": "2026-11-19T10:00:00", "text": "raw", "styled": "Ask Priya about pricing",
         "app": "Slack"},
        {"id": 3, "timestamp": "2026-11-19T11:00:00", "text": "never sent", "failed": True},
        {"id": 4, "timestamp": "2026-11-19T12:00:00", "text": "rewrite this", "kind": "transform"},
    ]), encoding="utf-8")
    return {"data": data, "claude": claude, "note": note["id"]}


def rpc(*messages):
    """Run messages through main() and give back the replies."""
    inp = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages))
    out = io.BytesIO()
    assert cc.main(inp, out) == 0
    return [json.loads(line) for line in out.getvalue().splitlines()]


def call(name, **arguments):
    reply = cc.call_tool(name, arguments)
    return json.loads(reply["content"][0]["text"]), reply["isError"]


# ── the protocol ────────────────────────────────────────────────────────────

def test_handshake_lists_the_tools_and_ignores_notifications(home):
    replies = rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert [r["id"] for r in replies] == [1, 2, 3]            # the notification got no answer
    init = replies[0]["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["capabilities"] == {"tools": {}}
    assert init["serverInfo"]["name"] == "waffler"
    names = [t["name"] for t in replies[2]["result"]["tools"]]
    assert names == ["search_meetings", "get_latest_meeting", "get_meetings", "get_meeting_transcript",
                     "search_transcripts", "list_meeting_folders", "create_meeting_folder",
                     "rename_meeting_folder", "move_meetings_to_folder", "import_meeting",
                     "get_recent_dictations", "search_dictations"]
    for tool in replies[2]["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"
        # Four tools file and bring in (tests/test_claude_organise_import.py); the rest only read.
        assert tool["annotations"]["readOnlyHint"] is (tool["name"] not in cc.WRITES)
        assert tool["annotations"].get("destructiveHint", False) is False


def test_an_unknown_protocol_version_gets_ours(home):
    (reply,) = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "1999-01-01"}})
    assert reply["result"]["protocolVersion"] == cc.VERSIONS[0]


def test_rubbish_and_unknown_methods_are_answered_not_fatal(home):
    inp = io.BytesIO(b"this is not json\n\n" + json.dumps(
        {"jsonrpc": "2.0", "id": 7, "method": "resources/list"}).encode() + b"\n" + json.dumps(
        {"jsonrpc": "2.0", "id": 8, "method": "ping"}).encode() + b"\n")
    out = io.BytesIO()
    cc.main(inp, out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["error"]["code"] == -32601
    assert replies[2] == {"jsonrpc": "2.0", "id": 8, "result": {}}


def test_a_tool_call_over_the_wire(home):
    (reply,) = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "get_latest_meeting", "arguments": {}}})
    body = json.loads(reply["result"]["content"][0]["text"])
    assert reply["result"]["isError"] is False
    assert body["meeting"]["title"] == "Call with the supplier"
    assert "Me: When does the quote arrive?" in body["meeting"]["transcript"]
    assert "Them: The quote arrives on Tuesday." in body["meeting"]["transcript"]
    assert "data" in body["content_advisory"]


def test_started_as_the_app_it_speaks_only_protocol_on_stdout(home):
    """The real thing: `app.py --mcp`, as Claude starts it."""
    env = {**os.environ, "WAFFLER_DATA_DIR": str(home["data"])}
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "search_meetings", "arguments": {"keywords": ["supplier"]}}}]
    done = subprocess.run([sys.executable, str(ROOT / "app.py"), "--mcp"],
                          input="".join(json.dumps(m) + "\n" for m in msgs).encode(),
                          capture_output=True, env=env, timeout=60)
    assert done.returncode == 0
    lines = done.stdout.splitlines()
    assert len(lines) == 2                                     # nothing but the two replies
    first, second = (json.loads(line) for line in lines)
    assert first["result"]["protocolVersion"] == "2025-03-26"
    assert first["result"]["serverInfo"]["version"] not in ("", "0")
    assert json.loads(second["result"]["content"][0]["text"])["count"] == 1


# ── the tools ───────────────────────────────────────────────────────────────

def test_meetings_can_be_found_read_and_searched(home):
    found, err = call("search_meetings", keywords=["supplier"])
    assert not err and found["count"] == 1
    assert found["meetings"][0]["note_headers"] == ["Quote"]
    assert found["meetings"][0]["duration_minutes"] == 5.0
    assert call("search_meetings", keywords=["nothing-like-this"])[0]["count"] == 0

    read, _ = call("get_meetings", meeting_ids=[home["note"], "nope"])
    assert read["meetings"][0]["ai_notes"].startswith("## Quote")
    assert read["meetings"][0]["has_transcript"] is True
    assert read["not_found"] == ["nope"]

    said, _ = call("search_transcripts", keywords=["tuesday"])
    assert said["passages"][0]["speaker"] == "Them" and said["passages"][0]["at"] == "00:02"

    page, _ = call("get_meeting_transcript", meeting_id=home["note"], include_timestamps=True)
    assert page["transcript"].splitlines()[0] == "[00:00] Me: When does the quote arrive?"
    assert page["truncated"] is False

    assert call("list_meeting_folders")[0] == {"folders": []}


def test_missing_things_are_errors_claude_can_read(home):
    body, err = call("get_meeting_transcript", meeting_id="nope")
    assert err and body == {"error": "Meeting not found."}
    reply = cc.call_tool("get_meetings", {})
    assert reply["isError"] and "meeting_ids" in reply["content"][0]["text"]
    assert cc.call_tool("delete_everything", {})["isError"]
    assert cc.call_tool("search_meetings", {"limit": "many"})["isError"]      # a bad argument


def test_a_deleted_meeting_is_not_shown(home):
    Store().delete_note(home["note"])
    assert call("get_meetings", meeting_ids=[home["note"]])[0]["meetings"] == []
    assert call("get_latest_meeting")[1] is True


def test_dictations_newest_first_without_failures_or_transforms(home):
    got, err = call("get_recent_dictations")
    assert not err
    assert [d["text"] for d in got["dictations"]] == ["Ask Priya about pricing", "Book the van for Friday"]
    assert got["dictations"][0]["typed_into"] == "Slack"
    assert call("get_recent_dictations", app="notes")[0]["count"] == 1


def test_dictations_search_by_words_dates_and_app(home):
    assert call("search_dictations", keywords=["van friday"])[0]["count"] == 1
    assert call("search_dictations", keywords=["van pricing"])[0]["count"] == 0     # both words, one entry
    assert call("search_dictations", keywords=["van", "pricing"])[0]["count"] == 2  # either entry
    assert call("search_dictations", from_date="2026-11-19")[0]["count"] == 1
    assert call("search_dictations", to_date="2026-11-18")[0]["count"] == 1          # the whole day
    assert call("search_dictations", app="slack", keywords=["van"])[0]["count"] == 0


def test_dictations_switched_off_stay_out(home):
    (home["data"] / "settings.json").write_text(json.dumps({cc.SETTING: False}))
    body, err = call("get_recent_dictations")
    assert err and "switched off" in body["error"]
    assert call("search_dictations", keywords=["van"])[1] is True
    assert call("search_meetings")[0]["count"] == 1               # meetings are still readable
    assert cc.status()["dictations"] is False


def test_no_history_file_is_no_dictations(home):
    (home["data"] / "history.json").unlink()
    assert call("get_recent_dictations")[0]["count"] == 0


# ── hooking it up to Claude ─────────────────────────────────────────────────

def cfg(home):
    return home["claude"] / "claude_desktop_config.json"


def test_connect_adds_one_entry_and_keeps_everything_else(home):
    cfg(home).write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
    before = cc.status()
    assert before["installed"] and not before["connected"]
    out = cc.connect()
    assert out["ok"] and out["connected"] and out["current"]
    data = json.loads(cfg(home).read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "x"}
    entry = data["mcpServers"]["waffler"]
    assert entry["args"][-1] == "--mcp"
    assert entry["env"] == {"WAFFLER_DATA_DIR": str(home["data"])}        # a test copy reads its own folder
    backup = home["claude"] / "claude_desktop_config.json.before-waffler"
    assert json.loads(backup.read_text()) == {"theme": "dark", "mcpServers": {"other": {"command": "x"}}}


def test_connect_with_no_settings_file_yet(home):
    assert not cfg(home).exists()
    assert cc.connect()["ok"]
    assert list(json.loads(cfg(home).read_text())["mcpServers"]) == ["waffler"]


def test_disconnect_takes_only_waffler_out(home):
    cfg(home).write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    cc.connect()
    out = cc.disconnect()
    assert out["ok"] and not out["connected"]
    assert json.loads(cfg(home).read_text()) == {"mcpServers": {"other": {"command": "x"}}}
    assert cc.disconnect()["ok"]                                          # twice is fine


def test_a_broken_settings_file_is_left_alone(home):
    cfg(home).write_text('{"mcpServers": {')
    out = cc.connect()
    assert not out["ok"] and "left it alone" in out["error"]
    assert cfg(home).read_text() == '{"mcpServers": {'
    cfg(home).write_text('{"mcpServers": []}')
    assert not cc.connect()["ok"]
    assert cfg(home).read_text() == '{"mcpServers": []}'


def test_no_claude_on_this_computer(home, monkeypatch):
    monkeypatch.setenv("WAFFLER_CLAUDE_CONFIG", str(home["data"] / "nowhere" / "claude_desktop_config.json"))
    assert cc.status()["installed"] is False
    out = cc.connect()
    assert not out["ok"] and "isn't on this computer" in out["error"]
    assert not (home["data"] / "nowhere").exists()                       # nothing was created


def test_an_entry_for_another_copy_is_connected_but_not_current(home):
    cfg(home).write_text(json.dumps({"mcpServers": {"waffler": {"command": "/old/Waffler", "args": ["--mcp"]}}}))
    s = cc.status()
    assert s["connected"] and not s["current"]
    assert cc.connect()["current"]


def test_the_claude_code_command(home):
    cmd = cc.claude_code_command()
    assert cmd.startswith("claude mcp add --scope user waffler -- ") and cmd.endswith("--mcp")


# ── the page ────────────────────────────────────────────────────────────────

def test_settings_has_a_claude_section_wired_to_the_app():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "ui" / "claude.js").read_text(encoding="utf-8")
    app_js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    app_py = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'data-sec="claude" data-onclick="showSettingsSection(\'claude\')">AI apps</button>' in html
    assert '<section class="s-sec" id="secClaude" data-sec="claude" hidden>' in html
    assert '<script src="claude.js"></script>' in html
    for ident in ("claudeState", "claudeConnect", "claudeDisconnect", "claudeDictToggle", "claudeCommand"):
        assert f'id="{ident}"' in html and ident in js
    assert "'claude'" in app_js.split("const SETTINGS_SECTIONS =")[1].split(";")[0]
    for method in ("claude_status", "claude_connect", "claude_disconnect"):
        assert f"def {method}(self)" in app_py and method in js
    assert "claude_dictations" in app_py and "claude_dictations" in js
    assert app_py.index("'--mcp' in sys.argv") < app_py.index("import webview")   # before the heavy imports


def test_other_ai_apps_are_given_the_same_server_in_their_own_form(monkeypatch):
    import json as _json
    import tomllib
    from src import claude_connector as cc
    monkeypatch.setattr(cc, "launcher", lambda: ["/Applications/Waffler.app/Contents/MacOS/Waffler", "--mcp"])
    codex = tomllib.loads(cc.codex_config())
    assert codex["mcp_servers"]["waffler"]["command"].endswith("/Waffler")
    assert codex["mcp_servers"]["waffler"]["args"] == ["--mcp"]
    other = _json.loads(cc.generic_config())
    assert other["mcpServers"]["waffler"]["command"] == codex["mcp_servers"]["waffler"]["command"]
    assert other["mcpServers"]["waffler"]["args"] == ["--mcp"]
    st = cc.status()
    assert st["codex"] == cc.codex_config() and st["other"] == cc.generic_config()


def test_a_windows_path_survives_both_forms(monkeypatch):
    import json as _json
    import tomllib
    from src import claude_connector as cc
    path = "C:\\Program Files\\Waffler\\Waffler.exe"
    monkeypatch.setattr(cc, "launcher", lambda: [path, "--mcp"])
    assert tomllib.loads(cc.codex_config())["mcp_servers"]["waffler"]["command"] == path
    assert _json.loads(cc.generic_config())["mcpServers"]["waffler"]["command"] == path


def test_the_page_offers_codex_and_the_rest():
    from pathlib import Path as _P
    html = (_P(__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")
    js = (_P(__file__).resolve().parent.parent / "ui" / "claude.js").read_text(encoding="utf-8")
    for ident in ("claudeCodex", "claudeCodexCopy", "claudeOther", "claudeOtherCopy"):
        assert f'id="{ident}"' in html and ident in js
