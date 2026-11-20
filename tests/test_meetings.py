"""Meetings: finding the Natter program, and starting / stopping its engine."""
import os
import stat
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import meetings  # noqa: E402


def test_no_binary_gives_a_plain_message(monkeypatch):
    monkeypatch.setattr(meetings, "candidates", lambda: [])
    m = meetings.Meetings()
    problem = m.ensure_engine()
    assert "Natter" in problem and not m.running()
    assert m.open() == {"ok": False, "error": problem}


def test_env_override_comes_first(monkeypatch, tmp_path):
    fake = tmp_path / "Natter"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("WAFFLER_NATTER_BIN", str(fake))
    assert meetings.candidates()[0] == fake
    assert meetings.find_binary() == fake


def _fake_engine(tmp_path, keys_set: bool) -> Path:
    """A stand-in for `Natter serve`: answers the three calls Waffler makes."""
    script = tmp_path / "fake_natter.py"
    script.write_text(textwrap.dedent(f"""
        import json, sys
        from http.server import BaseHTTPRequestHandler, HTTPServer
        port, token = int(sys.argv[sys.argv.index("--port") + 1]), sys.argv[sys.argv.index("--token") + 1]
        log = open({str(tmp_path / "calls.log")!r}, "a")
        class H(BaseHTTPRequestHandler):
            def _go(self):
                if self.headers.get("Authorization") != "Bearer " + token:
                    self.send_response(401); self.end_headers(); return
                log.write(self.command + " " + self.path + "\\n"); log.flush()
                body = {{"/api/state": {{"recording": False, "pending_chunks": 0}},
                        "/api/keys": {{"groq": {{"set": {keys_set}}}, "openai": {{"set": False}}}},
                        "/api/keys/import-waffler": {{"imported": ["groq"]}}}}.get(self.path, {{}})
                data = json.dumps(body).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = _go
            def log_message(self, *a): pass
        HTTPServer(("127.0.0.1", port), H).serve_forever()
    """))
    launcher = tmp_path / "Natter"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    return launcher


def test_engine_starts_shares_keys_and_stops(monkeypatch, tmp_path):
    if os.name == "nt":
        return
    monkeypatch.setenv("WAFFLER_NATTER_BIN", str(_fake_engine(tmp_path, keys_set=False)))
    m = meetings.Meetings()
    try:
        assert m.ensure_engine() == "" and m.running()
        assert m.url().startswith("http://127.0.0.1:") and "?t=" in m.url()
        calls = (tmp_path / "calls.log").read_text()
        assert "POST /api/keys/import-waffler" in calls        # no keys yet: copied from Waffler
        assert m.ensure_engine() == ""                           # a second open reuses the engine
    finally:
        m.shutdown()
    assert not m.running()


def test_keys_already_there_are_left_alone(monkeypatch, tmp_path):
    if os.name == "nt":
        return
    monkeypatch.setenv("WAFFLER_NATTER_BIN", str(_fake_engine(tmp_path, keys_set=True)))
    m = meetings.Meetings()
    try:
        assert m.ensure_engine() == ""
        assert "import-waffler" not in (tmp_path / "calls.log").read_text()
    finally:
        m.shutdown()
