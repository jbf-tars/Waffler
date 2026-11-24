#!/usr/bin/env python3
"""Ask a built Waffler whether it works, without opening its window.

    python scripts/check_built_app.py dist/Waffler.app

Three things, all through the built app's own executable, in a data folder
made for this run (never the person's):

  1. `--self-check --complete`: every part the build needs is inside it, the
     two on-device engines, the computer-sound helper, anthropic and the
     ffmpeg program for Import audio among them (src/self_check.py). On
     Windows the writing engine comes down with the model, so the check
     names what would be fetched;
  2. on a Mac, `--self-check --audio`: a two-second .m4a made here with
     afconvert (what an iPhone voice memo is) goes through Import audio, with
     no ffmpeg on the PATH, so it is the build's own ffmpeg that reads it;
  3. `--mcp`: the connector Claude starts answers `initialize`, `tools/list`
     and `tools/call` over a meeting and three dictations put there first.

No window, no shortcut, no connection, so it is safe on a build machine and
beside a running Waffler. Exit code 0 when both pass. The pills are checked
separately, on a Mac with a screen: scripts/drive_overlay.py --app.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def executable(path: str) -> Path:
    p = Path(path).resolve()
    return p / "Contents" / "MacOS" / "Waffler" if p.suffix == ".app" else p


def seed(data: Path) -> str:
    """One meeting and three dictations, written with the checkout's own code."""
    os.environ["WAFFLER_DATA_DIR"] = str(data)
    os.environ.pop("WAFFLER_MEETINGS_HOME", None)
    sys.path.insert(0, str(ROOT))
    from src.meetings.store import Store
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
        {"id": 3, "timestamp": "2026-11-19T11:00:00", "text": "Send the invoice on Monday", "app": "Mail"},
    ]), encoding="utf-8")
    return note["id"]


def voice_memo(folder: Path) -> Path:
    """Two seconds of a 440 Hz tone as AAC in an .m4a, made with afconvert."""
    import math
    import struct
    import wave
    wav, m4a = folder / "tone.wav", folder / "memo.m4a"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 44100)))
                               for i in range(2 * 44100)))
    subprocess.run(["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", str(wav), str(m4a)], check=True)
    return m4a


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    exe = executable(sys.argv[1])
    data = Path(tempfile.mkdtemp(prefix="waffler-built-check-"))
    env = dict(os.environ, WAFFLER_DATA_DIR=str(data))
    env.pop("WAFFLER_MEETINGS_HOME", None)
    failed = []

    def check(what: str, good: bool, detail: str = "") -> None:
        print(f"{'ok  ' if good else 'FAIL'}  {what}{'  ' + detail if detail else ''}", flush=True)
        if not good:
            failed.append(what)

    # 1. every part is inside the build
    done = subprocess.run([str(exe), "--self-check", "--complete"], env=env, capture_output=True,
                          text=True, timeout=180)
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        answer = {}
    check("self-check answers with JSON", bool(answer), done.stdout[:200] if not answer else "")
    check("self-check: every required part", done.returncode == 0 and answer.get("ok") is True,
          json.dumps({k: answer.get(k) for k in ("modules", "files", "optional_missing")}))
    for name, engine in (answer.get("engines") or {}).items():
        if engine.get("comes_with_model"):           # Windows: llama.cpp's server is fetched with the model
            check(f"self-check: the {name} engine comes down with its model", "error" not in engine,
                  engine.get("detail", "")[:90])
            continue
        check(f"self-check: the {name} engine runs", bool(engine.get("runs")),
              engine.get("error") or engine.get("detail", "")[:60])
    check("self-check: the computer-sound helper is in the app",
          bool((answer.get("computer_sound_helper") or {}).get("present")) or sys.platform != "darwin")
    libraries = answer.get("libraries") or {}
    check("self-check: anthropic imports", bool((libraries.get("anthropic") or {}).get("present")),
          (libraries.get("anthropic") or {}).get("version", ""))
    check("self-check: the ffmpeg program for Import audio is in the app",
          bool((libraries.get("ffmpeg") or {}).get("present")), (libraries.get("ffmpeg") or {}).get("path", ""))
    keys = answer.get("meeting_keys") or {}
    check("self-check: meetings keep keys without keyring", keys.get("keyring") is False, json.dumps(keys))
    check("self-check wrote nothing in the data folder", list(data.iterdir()) == [])

    # 2. a voice memo goes through Import audio with the build's own ffmpeg
    if sys.platform == "darwin":
        scratch = Path(tempfile.mkdtemp(prefix="waffler-built-check-audio-"))
        memo = voice_memo(scratch)
        bare = dict(env, PATH="/usr/bin:/bin")       # no ffmpeg of the person's own
        done = subprocess.run([str(exe), "--self-check", "--audio", str(memo)], env=bare,
                              capture_output=True, text=True, timeout=180)
        try:
            audio = json.loads(done.stdout).get("audio") or {}
        except ValueError:
            audio = {}
        check("Import audio: an .m4a is decoded by the build's own ffmpeg",
              audio.get("decoded") is True and audio.get("needed_ffmpeg") is True
              and audio.get("ffmpeg_on_path") == "" and 1.8 <= audio.get("seconds", 0) <= 2.3,
              json.dumps(audio))

    # 3. the connector answers over seeded data
    note_id = seed(data)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "get_latest_meeting", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "search_dictations", "arguments": {"keywords": ["invoice"]}}},
    ]
    done = subprocess.run([str(exe), "--mcp"], env=env, capture_output=True, timeout=60,
                          input=b"".join(json.dumps(m).encode() + b"\n" for m in messages))
    try:
        replies = {r["id"]: r for r in map(json.loads, done.stdout.decode("utf-8").splitlines())}
    except (ValueError, KeyError):
        replies = {}
    check("--mcp: stdout is protocol messages only, one per request",
          sorted(replies) == [1, 2, 3, 4], done.stdout[:200].decode("utf-8", "replace") if not replies else "")
    info = replies.get(1, {}).get("result", {}).get("serverInfo", {})
    check("--mcp: initialize", info.get("name") == "waffler", json.dumps(info))
    tools = [t["name"] for t in replies.get(2, {}).get("result", {}).get("tools", [])]
    check("--mcp: tools/list", "get_latest_meeting" in tools and "search_dictations" in tools,
          f"{len(tools)} tools")
    latest = json.dumps(replies.get(3, {}).get("result", {}))
    check("--mcp: tools/call get_latest_meeting reads the meeting",
          note_id in latest and "Call with the supplier" in latest and "arrives on Tuesday" in latest)
    found = json.dumps(replies.get(4, {}).get("result", {}))
    check("--mcp: tools/call search_dictations finds the dictation",
          "Send the invoice on Monday" in found and "Book the van" not in found)

    print(f"--- {'all passed' if not failed else str(len(failed)) + ' failed'}; data folder: {data}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
