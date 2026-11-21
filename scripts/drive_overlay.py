#!/usr/bin/env python3
"""Run the overlay helper by itself and put three pills through it.

    venv/bin/python scripts/drive_overlay.py
    venv/bin/python scripts/drive_overlay.py --app dist/Waffler.app    # a built app's helper

It starts `app.py --overlay` as its own process (never the running app's),
sends it the same JSON lines src/overlay.py sends, and prints what the helper
said back:

  1. a Done pill ("Polished"), which goes by itself after two seconds;
  2. a "Your call" ("Still working", Send later), whose button is pressed;
  3. a Problem with a button ("Didn't hear you", Mic), whose button is pressed;
  4. a Working pill ("Tidying") that becomes "Done" in place when the work ends;
  5. a Problem in the resting place, which ends when the dictation pill needs
     the place; the same Problem above a dictation pill that is showing, which
     comes down into the resting place when that pill goes.

The buttons are pressed with the helper's "_toast_click" command, so the
person's mouse is never touched. The pills do appear on screen for a moment,
at the bottom centre, as they would for real. The helper is given a data
folder of its own, so nothing is written in the real one.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pill  # noqa: E402
import user_messages as um  # noqa: E402

OFFER = [{"label": "Keep waiting", "action": "keep_waiting", "kind": "primary"},
         {"label": "Send later", "action": "send_later", "kind": "secondary"},
         {"label": "Cancel", "action": "cancel_processing", "kind": "danger"}]
JOURNAL = [{"label": "Show in Journal", "action": "show_transform:7", "kind": "primary"}]


def main():
    # A data folder of its own: app.py opens crash.log in the data folder as
    # it starts, and this run must not write in the person's real one.
    env = dict(os.environ)
    env["WAFFLER_DATA_DIR"] = tempfile.mkdtemp(prefix="waffler-overlay-drive-")
    command = [sys.executable, str(ROOT / "app.py"), "--overlay"]
    if "--app" in sys.argv:                 # a built app: its own executable draws
        app = Path(sys.argv[sys.argv.index("--app") + 1]).resolve()
        exe = app / "Contents" / "MacOS" / "Waffler" if app.suffix == ".app" else app
        command = [str(exe), "--overlay"]
    print(f"--- helper: {' '.join(command)}", flush=True)
    proc = subprocess.Popen(command, cwd=str(ROOT),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1, env=env)
    events, log = [], []
    started = time.time()

    def stamp():
        return f"{time.time() - started:5.2f}s"

    def read_out():
        for line in proc.stdout:
            line = line.strip()
            if line:
                events.append(line)
                print(f"{stamp()}  helper said   {line}", flush=True)

    def read_err():
        for line in proc.stderr:
            line = line.rstrip()
            if any(word in line for word in ("pill", "FAILED", "Traceback", "Error")):
                log.append(line)
                print(f"{stamp()}  helper logged {line}", flush=True)

    threading.Thread(target=read_out, daemon=True).start()
    threading.Thread(target=read_err, daemon=True).start()

    def send(cmd):
        short = {k: v for k, v in cmd.items() if k != "pill"}
        if "pill" in cmd:
            short["pill"] = f"{cmd['pill']['kind']} '{cmd['pill']['text']}'"
        print(f"{stamp()}  sent          {json.dumps(short)}", flush=True)
        proc.stdin.write(json.dumps(cmd) + "\n")
        proc.stdin.flush()

    def show(style, heading, body, buttons=None, timeout=None):
        made = pill.pill_for(style, heading, body, buttons, timeout)
        send({"type": "show_toast", "style": style, "pill": made.as_dict(), "reposition": True})

    deadline = time.time() + 15
    while not any('"ready"' in e for e in events) and time.time() < deadline:
        time.sleep(0.05)
    if not any('"ready"' in e for e in events):
        print("the helper never said it was ready")
        proc.kill()
        return 1

    print("--- 1. Done")
    show("info", *um.transform_done_message("Polish", "darwin"), JOURNAL, 8)
    time.sleep(3.0)                 # two seconds, then it goes by itself

    print("--- 2. Your call, and its button")
    show("info", "Still working on it", "Groq is slow to answer. Your recording is safe: keep "
         "waiting, or let Waffler send it later.", OFFER)
    time.sleep(1.2)
    send({"type": "_toast_click", "where": "body"})      # a stray click: nothing happens
    time.sleep(0.4)
    send({"type": "_toast_click", "where": "button"})
    time.sleep(1.0)

    print("--- 3. A Problem with a button, and its button")
    show("error", "We couldn't hear you", "Check your mic is connected and not muted.")
    time.sleep(1.2)
    send({"type": "_toast_click", "where": "button"})
    time.sleep(1.0)

    print("--- 4. Working, which becomes Done where it is when the work ends")
    show("warn", *um.take_limit_stopped(20))
    time.sleep(1.5)
    send({"type": "working_end", "result": "done"})
    time.sleep(2.8)

    print("--- 5. Where it sits: resting, above a dictation pill, then down when that pill goes")
    show("warn", "Connection failed", "Check your internet or VPN.")       # no pill: the resting place
    time.sleep(1.0)
    send({"type": "working", "elapsed_seconds": 1.0})                      # the pill needs its place back
    time.sleep(0.8)
    show("warn", "Connection failed", "Check your internet or VPN.")       # pill showing: above it
    time.sleep(1.0)
    send({"type": "working_end", "result": "quiet"})                       # the pill goes: it comes down
    time.sleep(1.2)

    send({"type": "quit"})
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
    print(f"--- the helper ended with code {proc.returncode}")
    actions = [json.loads(e).get("action") for e in events if '"toast_action"' in e]
    print(f"--- answers it sent: {actions}")
    return 0 if actions == ["send_later", "select_mic"] else 1


if __name__ == "__main__":
    sys.exit(main())
