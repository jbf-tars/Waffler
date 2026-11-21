#!/usr/bin/env python3
"""A real end-to-end check of the writing model on this computer (src/local_llm.py).

Not part of the test suite: it downloads the model (5.2 GB) and runs it.
Everything goes into a temporary data folder that is deleted at the end; the
real ~/.waffler-hosted is never read or written, and no key is in the
environment.

    python scripts/local_llm_check.py
    python scripts/local_llm_check.py --keep --data-dir /tmp/llm-check
    python scripts/local_llm_check.py --samples FILE.py --transcript FILE.txt

What it does, in order:
  1. status: not downloaded, and whether this computer is offered the model
  2. starts the download, cancels it part-way, checks the part-file is kept
  3. downloads again: it must carry on from the part-file, then verify
  4. switches the network off (any connection after this point fails the run)
  5. tidies dictations through the app's own clean-up (OpenAIStyler.style):
     the first one loads the model and reads the long prompt
  6. writes a meeting's notes through the app's own write_notes
  7. starts notes and a dictation at the same moment: the dictation goes first
  8. waits for the idle unload, then removes the model

--samples: a Python file with SAMPLES = [{"id", "text"}, ...] to tidy instead
of the three below. --transcript: a transcript to write notes from.
"""
from __future__ import annotations

import argparse
import os
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DICTATIONS = [
    {"id": "ramble", "text": (
        "So um I was thinking about the onboarding flow again and, you know, I think the problem is "
        "basically that we ask for too much up front. Like, people just want to try the thing. So "
        "maybe we move the team invite to later, uh, after the first project is created, and I'll ask "
        "Priya to put it on staging by Thursday so we can see how it feels.")},
    {"id": "list", "text": (
        "Right, three things for today. First, chase the Hartley invoice because it's now two weeks "
        "late. Second, um, sort out the VAT return before the deadline. And third, book the van for "
        "Saturday morning.")},
    {"id": "email", "text": (
        "Hi Sarah. Thanks for sending the contract over. Um, I've read through it and I'm happy with "
        "everything apart from clause seven, which, you know, I think needs to say thirty days not "
        "fourteen. Can you send a revised version by the 14th March? Kind regards, James.")},
]
MEETING = (
    "Me: Okay, let's get started. First item is the kitchen refit on Maple Road. The client approved "
    "the quote of eight thousand four hundred pounds on Monday, and the cabinets arrive on the "
    "fourteenth of January.\n"
    "Them: I can confirm the electrician is booked for the week before. It's Tom here, by the way.\n"
    "Me: Thanks Tom. We also need to order the worktops by Friday, otherwise the supplier says the "
    "lead time goes from two weeks to five.\n"
    "Them: I'll order them tomorrow.\n"
    "Me: Second item is the invoice for the Henderson job. It is still unpaid after forty five days, "
    "so I will call them tomorrow morning and send a reminder in writing. If we hear nothing by the "
    "end of the month we will pause work on the extension.\n"
    "Them: Agreed.\n"
    "Me: Third, the new van. The lease quote came back at three hundred and twenty pounds a month "
    "over four years, which is about ten percent more than we budgeted. I would like to compare it "
    "with buying a two year old one outright before we decide.\n"
    "Them: I can get two prices for a used one by Wednesday.\n"
    "Me: Great. That is everything from me.")


def footprint_mb() -> str:
    try:
        rss = int(subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True,
                                 text=True).stdout.strip()) / 1024
        return f"{rss:.0f} MB resident"
    except Exception:
        return "memory not read"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir")
    ap.add_argument("--keep", action="store_true", help="keep the data folder (and the model) afterwards")
    ap.add_argument("--samples")
    ap.add_argument("--transcript")
    ap.add_argument("--idle", type=float, default=3.0, help="idle seconds before the model is released")
    ap.add_argument("--force", action="store_true", help="run even where the model is not offered")
    args = ap.parse_args()

    data = Path(args.data_dir or tempfile.mkdtemp(prefix="waffler-llm-check-"))
    data.mkdir(parents=True, exist_ok=True)
    os.environ["WAFFLER_DATA_DIR"] = str(data)
    os.environ["WAFFLER_LOCAL_LLM_IDLE_S"] = "0"
    os.environ["NATTER_NO_KEYRING"] = "1"
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY", "ANTHROPIC_API_KEY",
                 "NATTER_CUSTOM_API_KEY"):
        os.environ.pop(name, None)
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    import local_llm

    ok = True

    def check(what: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and bool(passed)
        print(f"  [{'ok' if passed else 'FAILED'}] {what}" + (f": {detail}" if detail else ""), flush=True)

    print(f"data folder: {data}")
    status = local_llm.model_status()
    print(f"1. {status['label']}, {status['bytes_total'] / 1e9:.2f} GB, state {status['state']}, "
          f"engine {'here' if status['available'] else 'MISSING'}, offered on this computer: "
          f"{status['suits_machine']} ({status['machine_reason']})")
    if not status["available"]:
        print("   llama-cpp-python is not installed in this Python.")
        return 2
    if not status["suits_machine"]:
        if not args.force:
            print("   Not offered here. --force runs it anyway.")
            return 2
        local_llm.machine_verdict = lambda machine=None: {"suits": True, "reason": "forced"}

    if status["state"] != "ready":
        print("2. download, cancelled part-way")
        local_llm.start_download()
        end = time.time() + 120
        while local_llm.model_status()["bytes_done"] < 150e6 and time.time() < end:
            time.sleep(0.2)
        local_llm.cancel_download()
        local_llm._wait_for_download(30)
        part = local_llm.model_status()
        check("stopped, and what arrived is kept", part["state"] == "not_downloaded" and part["cancelled"]
              and part["bytes_done"] > 100e6, f"{part['bytes_done'] / 1e6:.0f} MB kept")
        print("3. download again: carries on, then verifies size and SHA-256")
        t0, last = time.time(), [0]

        def progress(percent, done, total):
            if percent >= last[0] + 10:
                last[0] = percent
                print(f"     {percent}% ({done / 1e9:.2f} of {total / 1e9:.2f} GB)", flush=True)
        result = local_llm.download(progress)
        check("downloaded and verified", result["ok"], f"{time.time() - t0:.0f} s; {result.get('error', '')}")
        if not result["ok"]:
            return 1
    else:
        print("2-3. the model is already in this data folder")

    print("4. network off")
    attempts = []

    def no_network(*a, **k):
        attempts.append(a[1:2] or a[:1])
        raise OSError("the network is off for this check")
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network

    from style_openai import OpenAIStyler
    from meetings import llm as notes_llm
    from meetings.store import BUILTIN_TEMPLATES
    styler = OpenAIStyler()
    check("no key anywhere, and the model here is the one to ask",
          not styler._has_cloud_provider() and styler._local_cleanup()
          and notes_llm.resolve_provider({}) == "local")

    print("5. clean-up, through OpenAIStyler.style")
    samples = runpy.run_path(args.samples)["SAMPLES"] if args.samples else DICTATIONS
    t0 = time.perf_counter()
    warmed = local_llm.warm()
    print(f"   load {local_llm.model_status()['load_s']} s, load and long prompt read "
          f"{time.perf_counter() - t0:.1f} s ({'ok' if warmed else 'FAILED'}); {footprint_mb()}")
    times = []
    for s in samples:
        t0 = time.perf_counter()
        styled, usage = styler.style(s["text"])
        dt = time.perf_counter() - t0
        words = len(s["text"].split())
        asked = usage.get("provider") == local_llm.PROVIDER
        if asked:
            times.append((words, dt))
        print(f"\n   --- {s['id']}: {words} words, {dt:.2f} s, by {usage.get('provider') or 'no model (short take)'}"
              + (f", NOT USED: {usage.get('fallback_reason') or usage.get('truncation_guard')}"
                 if usage.get("provider") == "basic_clean" else ""))
        print("   " + styled.replace("\n", "\n   "))
        if words > 10:
            check(f"{s['id']} was tidied on this computer", asked)
    if times:
        per = sorted(dt for _w, dt in times)
        print(f"\n   {len(times)} tidied here: median {per[len(per) // 2]:.2f} s, slowest {per[-1]:.2f} s; "
              f"{footprint_mb()}")

    print("\n6. meeting notes, through write_notes")
    transcript = Path(args.transcript).read_text(encoding="utf-8") if args.transcript else MEETING
    template = next(p for tid, _n, p in BUILTIN_TEMPLATES if tid == "general")
    t0 = time.perf_counter()
    title, notes = notes_llm.write_notes(transcript, "", template, {}, when="Thursday 21 November 2024, 10:00")
    dt = time.perf_counter() - t0
    print(f"   {len(transcript.split())} words of transcript, {dt:.1f} s; {footprint_mb()}")
    print(f"   TITLE: {title}\n   " + notes.replace("\n", "\n   "))
    check("notes were written, with a title", bool(title) and len(notes.split()) > 20)

    print("\n7. notes and a dictation at the same moment")
    out = {}

    def write():
        t = time.perf_counter()
        out["notes"] = notes_llm.write_notes(transcript, "", template, {})
        out["notes_s"] = time.perf_counter() - t
    thread = threading.Thread(target=write)
    thread.start()
    time.sleep(4.0)                         # the notes are being written by now
    t0 = time.perf_counter()
    styled, usage = styler.style(DICTATIONS[0]["text"])
    waited = time.perf_counter() - t0
    thread.join(600)
    check("the dictation was tidied while notes were being written, without waiting for them",
          usage.get("provider") == local_llm.PROVIDER and waited < out.get("notes_s", 0),
          f"dictation {waited:.1f} s, notes {out.get('notes_s', 0):.1f} s in all")
    check("the notes were still written afterwards", bool(out.get("notes") and out["notes"][1]))

    print("8. idle unload, then remove")
    local_llm._wait_for_prime(60)
    local_llm.set_idle_unload(args.idle)
    local_llm.chat("Reply with one word.", [{"role": "user", "content": "Say hello."}], max_tokens=8)
    local_llm._wait_for_prime(60)
    end = time.time() + args.idle + 30
    while local_llm.is_loaded() and time.time() < end:
        time.sleep(0.5)
    check("released after the idle period", not local_llm.is_loaded(), footprint_mb())
    check("no network connection was attempted after the download", not attempts, str(attempts[:3]))
    if not args.keep:
        removed = local_llm.remove_model()
        check("removed", removed["ok"], f"{removed['freed_bytes'] / 1e9:.2f} GB freed")
        shutil.rmtree(data, ignore_errors=True)
    print("\nALL OK" if ok else "\nSOMETHING FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
