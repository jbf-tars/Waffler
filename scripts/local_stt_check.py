#!/usr/bin/env python3
"""A real end-to-end check of on-device speech to text (src/local_stt.py).

Not part of the test suite: it downloads the speech model (about 0.6 GB on
Apple Silicon) and runs it. Everything goes into a temporary data folder that
is deleted at the end; the real ~/.waffler-hosted is never read or written.

    python scripts/local_stt_check.py                 # this machine's engine
    python scripts/local_stt_check.py --engine faster-whisper --model small
    python scripts/local_stt_check.py --keep --data-dir /tmp/stt-check

What it does, in order:
  1. status: not downloaded
  2. starts the download, cancels it part-way, checks the part-file is kept
  3. downloads again: it must carry on from the part-file, then verify
  4. transcribes a ~10 s spoken clip (first run loads the model) and a ~60 s one
  5. runs a dictation and a "meeting" piece at the same moment
  6. waits for the idle unload, then removes the model

The clips are spoken by macOS (`say`) and converted with `afconvert`, so the
words are known and the word accuracy can be printed. Pass --wav FILE to use a
recording of your own instead (16-bit PCM WAV).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SHORT_TEXT = ("Hi Sarah, thanks for sending the draft over. I had a look this morning and I think "
              "the second section needs a clearer example. Could we meet on Thursday at three to "
              "go through it together?")
LONG_TEXT = (
    "Okay, let's get started. First item is the kitchen refit on Maple Road. The client approved "
    "the quote of eight thousand four hundred pounds on Monday, and the cabinets arrive on the "
    "fourteenth of January. Tom, can you confirm the electrician is booked for the week before? "
    "We also need to order the worktops by Friday, otherwise the supplier says the lead time goes "
    "from two weeks to five. Second item is the invoice for the Henderson job. It is still unpaid "
    "after forty five days, so I will call them tomorrow morning and send a reminder in writing. "
    "If we hear nothing by the end of the month we will pause work on the extension. Third, the "
    "new van. The lease quote came back at three hundred and twenty pounds a month over four "
    "years, which is about ten percent more than we budgeted. I would like to compare it with "
    "buying a two year old one outright before we decide. Finally, holidays. Please put your "
    "dates in the shared calendar by Wednesday so that we can plan cover for the last two weeks "
    "of December. That is everything from me. Any questions before we finish?")

_NUMBERS = {"three": "3", "two": "2", "five": "5", "four": "4", "ten": "10"}


def speak(text: str, folder: Path, name: str) -> Path:
    aiff, wav = folder / f"{name}.aiff", folder / f"{name}.wav"
    subprocess.run(["say", "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
                   check=True)
    return wav


def wav_seconds(path: Path) -> float:
    import wave
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def words(text: str) -> list:
    out = re.findall(r"[a-z0-9']+", text.lower().replace("£", " pounds ").replace("%", " percent "))
    return [_NUMBERS.get(w, w) for w in out]


def word_accuracy(said: str, heard: str) -> float:
    """Share of the spoken words found, in order, in what was heard. Rough:
    "8,400" for "eight thousand four hundred" counts against it."""
    import difflib
    a, b = words(said), words(heard)
    same = sum(m.size for m in difflib.SequenceMatcher(None, a, b).get_matching_blocks())
    return same / max(1, len(a))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", choices=("whispercpp", "faster-whisper"))
    ap.add_argument("--model", help="a model name from src/local_stt.py MODELS")
    ap.add_argument("--data-dir", help="use this folder instead of a temporary one")
    ap.add_argument("--keep", action="store_true", help="leave the data folder (and model) behind")
    ap.add_argument("--wav", help="a 16-bit PCM WAV to transcribe instead of the spoken clips")
    ap.add_argument("--no-interrupt", action="store_true", help="skip the cancel-and-resume step")
    args = ap.parse_args()

    data = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="waffler-stt-check-"))
    data.mkdir(parents=True, exist_ok=True)
    # Before anything of Waffler's is imported: nothing may touch the real data folder.
    os.environ["WAFFLER_DATA_DIR"] = str(data)
    if args.engine:
        os.environ["WAFFLER_LOCAL_STT_ENGINE"] = args.engine
    sys.path.insert(0, str(ROOT))
    from src import local_stt

    if args.model:
        (data / "settings.json").write_text('{"local_stt_model": "%s"}' % args.model, encoding="utf-8")
    failures = []

    def check(ok: bool, what: str) -> None:
        print(("  ok    " if ok else "  FAIL  ") + what)
        if not ok:
            failures.append(what)

    try:
        st = local_stt.model_status()
        print(f"data folder : {data}")
        print(f"engine      : {st['engine']}  (in this Python: {st['available']})")
        print(f"model       : {st['model']}  ({st['bytes_total'] / 1e6:.0f} MB to download)")
        if not st["available"]:
            print("The engine's package is not installed in this Python.")
            return 2
        check(st["state"] in ("not_downloaded", "ready"), f"status before: {st['state']}")

        # ── download, with a cancel and a resume ────────────────────────────
        if st["state"] != "ready" and not args.no_interrupt:
            def stop_at_a_fifth(percent, done, total):
                if percent >= 20:
                    local_stt.cancel_download()
            r = local_stt.download(stop_at_a_fifth)
            check(r.get("cancelled") is True and r["state"] == "not_downloaded",
                  f"cancelled part-way: state {r['state']}, {r['size_on_disk'] / 1e6:.0f} MB kept")
        resumed_from = local_stt.model_status()["size_on_disk"]
        seen = []
        t0 = time.perf_counter()
        r = local_stt.download(lambda percent, done, total: seen.append((percent, done)))
        download_s = time.perf_counter() - t0
        check(r["ok"] and r["state"] == "ready", f"download finished and verified: {r.get('error') or 'ready'}")
        if seen and resumed_from:
            check(seen[0][1] >= resumed_from * 0.9,
                  f"carried on from the part-file ({resumed_from / 1e6:.0f} MB already here, "
                  f"first progress at {seen[0][1] / 1e6:.0f} MB)")
        st = local_stt.model_status()
        print(f"size on disk: {st['size_on_disk'] / 1e6:.0f} MB   "
              f"(download step took {download_s:.1f} s)")

        # ── the clips ───────────────────────────────────────────────────────
        clips = []
        if args.wav:
            clips.append(("yours", Path(args.wav), ""))
        elif sys.platform == "darwin":
            clips.append(("short", speak(SHORT_TEXT, data, "short"), SHORT_TEXT))
            clips.append(("long", speak(LONG_TEXT, data, "long"), LONG_TEXT))
        else:
            print("No `say` on this system: pass --wav FILE.")
            return 2

        first = True
        for name, path, said in clips:
            audio = path.read_bytes()
            seconds = wav_seconds(path)
            t0 = time.perf_counter()
            text = local_stt.transcribe(audio, language="en")
            took = time.perf_counter() - t0
            st = local_stt.model_status()
            print(f"\n[{name}] {seconds:.1f} s of audio")
            if first:
                print(f"  first load      : {st['load_s']:.2f} s (on {st['device']})")
                print(f"  first transcribe: {st['transcribe_s']:.2f} s   (load + transcribe: {took:.2f} s)")
                t0 = time.perf_counter()
                again = local_stt.transcribe(audio, language="en")
                print(f"  again, loaded   : {time.perf_counter() - t0:.2f} s")
                check(again == text, "the same clip gives the same text twice")
                first = False
            else:
                print(f"  transcribe      : {took:.2f} s  ({seconds / took:.0f}x real time)")
            print(f"  text: {text}")
            check(bool(text.strip()), f"{name}: text came back")
            if said:
                acc = word_accuracy(said, text)
                print(f"  word accuracy   : {acc * 100:.0f}%")
                check(acc >= 0.85, f"{name}: at least 85% of the spoken words")

        # ── a dictation and a piece of a meeting at the same moment ─────────
        audio = clips[0][1].read_bytes()
        results, errors = {}, []

        def run(key, background):
            try:
                results[key] = local_stt.transcribe(audio, language="en", background=background)
            except Exception as e:      # noqa: BLE001
                errors.append(f"{key}: {type(e).__name__}: {e}")
        threads = [threading.Thread(target=run, args=("meeting", True)),
                   threading.Thread(target=run, args=("dictation", False))]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join(300)
        check(not errors and results.get("meeting") == results.get("dictation") and bool(results),
              f"two callers at once both got the text ({time.perf_counter() - t0:.2f} s for both)"
              + (f" {errors}" if errors else ""))

        # ── idle unload, then removal ───────────────────────────────────────
        local_stt.set_idle_unload(0.5)
        deadline = time.time() + 10
        while local_stt.model_status()["loaded"] and time.time() < deadline:
            time.sleep(0.1)
        check(not local_stt.model_status()["loaded"], "released from memory after the idle period")
        check(bool(local_stt.transcribe(audio, language="en")), "loads again by itself afterwards")
        if not args.keep:
            r = local_stt.remove_model()
            check(r["ok"] and r["state"] == "not_downloaded",
                  f"removed: {r['freed_bytes'] / 1e6:.0f} MB given back")
    finally:
        if not args.keep and not args.data_dir:
            shutil.rmtree(data, ignore_errors=True)

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
