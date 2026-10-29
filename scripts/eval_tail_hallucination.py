#!/usr/bin/env python3
"""Measure the end-of-take fixes (3.16.3) on real recordings: old path against new.

The old path is src/transcribe_whisper.py as it was at ``--old-ref`` (the last
release), loaded from git; the new path is the one in the working tree. Both
run their own ``transcribe_sync`` with one speech provider, on:

  real     the WAVs named on the command line, as recorded
  stress   each of them with 2 to 4 s of its OWN room noise appended (taken
           from its quietest stretches; the quiet-audio lift raises it with
           the rest, as in the app)
  tap      2 s of a recording's room noise alone (``--taps``)
  soft     each recording, a pause, its own last word 15 dB softer, then room
           noise: checked offline only, that the cut keeps that word

It prints, for each result, only its LAST FOUR WORDS, and then counts: results
ending in a thanks or outro, old and new; real last words that changed; the
provider's time; the bytes uploaded.

Safe to re-run. It reads the keys from the env file inside Python and never
prints them, and it refuses to run unless WAFFLER_DATA_DIR names a scratch
folder, so nothing it does (log lines, a vocabulary read) can touch the real
data folder. Requests are spaced ``--gap`` seconds apart, backed off on a rate
limit, and capped at ``--max-requests``.

    WAFFLER_DATA_DIR=/tmp/waffler-eval python scripts/eval_tail_hallucination.py \\
        --stress --taps 1 --providers groq,openai path/to/rec-*.wav

``--dry-run`` replaces the providers with a fake that answers instantly, to
check the script itself without a key or a request.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import statistics
import subprocess
import sys
import time
import types
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DATA_DIR = Path.home() / ".waffler-hosted"


def refuse_unless_scratch() -> Path:
    raw = os.environ.get("WAFFLER_DATA_DIR", "").strip()
    if not raw:
        sys.exit("Set WAFFLER_DATA_DIR to a scratch folder first: this script never "
                 "runs against the real data folder.")
    folder = Path(raw).expanduser().resolve()
    real = REAL_DATA_DIR.resolve()
    if folder == real or real in folder.parents:
        sys.exit("WAFFLER_DATA_DIR points at the real data folder. Use a scratch folder.")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def read_keys(env_file: Path) -> dict:
    keys = {}
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return keys


def load_old_module(ref: str):
    src = subprocess.run(["git", "show", f"{ref}:src/transcribe_whisper.py"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    mod = types.ModuleType("transcribe_whisper_old")
    mod.__file__ = f"<{ref}:src/transcribe_whisper.py>"
    sys.modules[mod.__name__] = mod
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


# ── audio ───────────────────────────────────────────────────────────────────

SR = 16000
WIN = 480


def read_wav(data: bytes):
    import numpy as np
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != SR:
            raise ValueError("expects 16 kHz mono 16-bit WAV")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)


def to_wav(a) -> bytes:
    import numpy as np
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.clip(a, -32768, 32767).astype(np.int16).tobytes())
    return b.getvalue()


def window_rms(a):
    import numpy as np
    n = len(a) // WIN
    return np.sqrt((a[: n * WIN].reshape(n, WIN) ** 2).mean(axis=1))


def room_noise(a, seconds: float):
    """``seconds`` of the recording's own room noise: its quietest stretches
    (150 ms or more under the 15th percentile window), joined with 10 ms
    crossfades and looped."""
    import numpy as np
    rms = window_rms(a)
    quiet = list(rms <= np.percentile(rms, 15)) + [False]
    runs, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if i - start >= 5:
                runs.append((start * WIN, i * WIN))
            start = None
    if not runs:
        runs = [(int(np.argmin(rms)) * WIN, (int(np.argmin(rms)) + 1) * WIN)]
    fade = int(SR * 0.01)
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    out = np.zeros(0, dtype=np.float32)
    need, k = int(seconds * SR), 0
    while len(out) < need + fade:
        s, e = runs[k % len(runs)]
        piece = a[s:e].copy()
        k += 1
        if len(out) >= fade and len(piece) > 2 * fade:
            out[-fade:] = out[-fade:] * (1 - ramp) + piece[:fade] * ramp
            out = np.concatenate([out, piece[fade:]])
        else:
            out = np.concatenate([out, piece])
    return out[:need]


def last_word(a):
    """The recording's last voiced stretch (up to 0.5 s), by the module's cut
    detector: where it ends and the samples of it."""
    import numpy as np
    rms = window_rms(a)
    sounding = rms[rms >= 1.0]
    floor = float(np.percentile(sounding, 10)) if sounding.size else 1.0
    voiced = np.nonzero(rms >= max(12.0, floor * 2.0))[0]
    end = (int(voiced[-1]) + 1) * WIN if voiced.size else len(a)
    return a[max(0, end - int(0.5 * SR)): end]


# ── what a result says ──────────────────────────────────────────────────────

_THANKS_END = re.compile(r"\b(?:thank\s+you|thanks)\b[^.!?]{0,40}[.!?]*\s*$", re.IGNORECASE)


def last4(text: str) -> str:
    return " ".join((text or "").split()[-4:])


def ends_in_outro(text: str, new_mod) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _THANKS_END.search(t):
        return True
    last_sentence = re.split(r"(?<=[.!?])\s+", t)[-1]
    return new_mod._is_outro_segment(last_sentence)


# ── running a path ──────────────────────────────────────────────────────────

class Budget:
    def __init__(self, cap: int, gap: float):
        self.cap, self.gap, self.used, self._last = cap, gap, 0, 0.0

    def take(self):
        if self.used >= self.cap:
            raise RuntimeError("request budget spent")
        wait = self._last + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.used += 1
        self._last = time.monotonic()


def make_transcriber(mod, provider: str, keys: dict, dry_run: bool):
    with contextlib.redirect_stdout(io.StringIO()):
        t = mod.WhisperTranscriber(
            api_key=keys.get("OPENAI_API_KEY", "") if provider == "openai" else "",
            groq_api_key=keys.get("GROQ_API_KEY", "") if provider == "groq" else "",
            provider_order=["groq", "openai"])
    if dry_run:
        def fake(audio_bytes):
            return "this is a dry run of the script Thank you."
        if provider == "groq":
            t._groq_client = object()
        else:
            t.client = object()
        setattr(t, "_transcribe_groq" if provider == "groq" else "_transcribe_api", fake)
    return t


def run_one(mod, provider, audio, keys, budget, dry_run):
    """transcribe_sync on ``audio``; returns (text, seconds in the provider,
    bytes uploaded). Backs off and tries again on a rate limit."""
    for attempt in range(4):
        t = make_transcriber(mod, provider, keys, dry_run)
        name = "_transcribe_groq" if provider == "groq" else "_transcribe_api"
        inner = getattr(t, name)
        sent = []

        def spy(audio_bytes, inner=inner, sent=sent):
            budget.take()
            t0 = time.perf_counter()
            try:
                return inner(audio_bytes)
            finally:
                sent.append((len(audio_bytes), time.perf_counter() - t0))

        setattr(t, name, spy)
        try:
            # The modules print some of what they discard; none of it is shown.
            with contextlib.redirect_stdout(io.StringIO()):
                text = t.transcribe_sync(audio)
            return text, sum(s for _, s in sent), sum(n for n, _ in sent)
        except Exception as e:      # noqa: BLE001 - reported, never the text
            limited = "429" in str(e) or type(e).__name__ == "RateLimitError"
            if not limited or attempt == 3:
                print(f"    {provider}: {type(e).__name__}; skipped")
                return None, 0.0, 0
            wait = 20.0 * (attempt + 1)
            print(f"    {provider}: rate limited, waiting {wait:.0f}s")
            time.sleep(wait)
    return None, 0.0, 0


def watch_segments(new_mod, seen: list):
    """Note, for every Groq answer the new path reads, the final segment's
    numbers (never its text): whether it is an outro, its no_speech_prob and
    the voice under it."""
    real = new_mod._groq_words

    def spy(response, audio_bytes):
        segs = new_mod._read_segments(response) if not isinstance(response, str) else None
        if segs:
            last = segs[-1]
            prof = new_mod._voice_profile(audio_bytes)
            start = last["start"]
            if start is not None and len(segs) > 1 and segs[-2]["end"] is not None:
                start = max(start, segs[-2]["end"])
            seen.append({"outro": new_mod._is_outro_segment(last["text"]),
                         "nsp": last["no_speech_prob"],
                         "voiced": new_mod._voiced_between(prof, start, last["end"]),
                         "span": None if last["end"] is None or start is None
                         else round(last["end"] - start, 2),
                         "all_nsp": [s["no_speech_prob"] for s in segs]})
        return real(response, audio_bytes)

    new_mod._groq_words = spy


# ── main ────────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("wavs", nargs="+", type=Path)
    ap.add_argument("--env", type=Path, default=REAL_DATA_DIR / ".env")
    ap.add_argument("--old-ref", default="v3.16.2")
    ap.add_argument("--providers", default="groq")
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--taps", type=int, default=0)
    ap.add_argument("--extra", type=Path, action="append", default=[],
                    help="one more WAV to run like the real ones (a made-up soft ending); "
                         "give it once for each")
    ap.add_argument("--openai-kinds", default="stress,tap",
                    help="which clip kinds go to OpenAI (it is the fallback; a real take "
                         "with no silent end uploads the same bytes on both paths)")
    ap.add_argument("--openai-max-seconds", type=float, default=30.0,
                    help="only recordings shorter than this go to OpenAI")
    ap.add_argument("--kinds", default="real,stress,tap,extra",
                    help="which clip kinds to send (to re-send only what a change touched)")
    ap.add_argument("--paths", default="old,new", help="old, new or both")
    ap.add_argument("--only", default="",
                    help="send only these recordings (file names, comma separated); the "
                         "rest are still read, so each stress clip is the same as before")
    ap.add_argument("--gap", type=float, default=4.0)
    ap.add_argument("--max-requests", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}
    paths = [p for p in ("old", "new") if p in {x.strip() for x in args.paths.split(",")}]

    refuse_unless_scratch()
    sys.path.insert(0, str(ROOT / "src"))
    import numpy as np
    import transcribe_whisper as new_mod
    old_mod = load_old_module(args.old_ref)
    keys = {} if args.dry_run else read_keys(args.env)
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    for p in providers:
        need = "GROQ_API_KEY" if p == "groq" else "OPENAI_API_KEY"
        if not args.dry_run and not keys.get(need):
            sys.exit(f"No {need} in {args.env}")

    lengths = [2.0, 2.5, 3.0, 3.5, 4.0]
    clips = []                       # (name, kind, wav bytes, reference name)
    raw = {}
    for i, p in enumerate(args.wavs):
        data = p.read_bytes()
        a = read_wav(data)
        raw[p.name] = a
        clips.append((p.name, "real", data, p.name))
        if args.stress:
            clips.append((p.name, "stress", to_wav(np.concatenate(
                [a, room_noise(a, lengths[i % len(lengths)])])), p.name))
    for p in args.wavs[: args.taps]:
        clips.append((p.name, "tap", to_wav(room_noise(raw[p.name], 2.0)), None))
    for p in args.extra:
        clips.append((p.name, "extra", p.read_bytes(), None))

    # Offline: a soft last word is never cut.
    soft_ok = 0
    for name, a in raw.items():
        word = last_word(a) * (10 ** (-15 / 20))
        pause, tail = room_noise(a, 0.4), room_noise(a, 2.0)
        made = np.concatenate([a, pause, word, tail])
        word_end = (len(a) + len(pause) + len(word)) / SR
        boosted = new_mod.boost_quiet_speech(to_wav(made))
        cut, _ = new_mod._trim_silent_tail(boosted, new_mod._voice_profile(to_wav(made)))
        with wave.open(io.BytesIO(cut), "rb") as w:
            kept_s = w.getnframes() / float(w.getframerate())
        ok = kept_s >= word_end
        soft_ok += ok
        print(f"soft last word  {name}: word ends {word_end:6.2f}s, kept {kept_s:6.2f}s "
              f"{'ok' if ok else 'CUT INTO IT'}")
    print(f"soft last word kept in {soft_ok} of {len(raw)} recordings\n")

    budget = Budget(args.max_requests, 0.0 if args.dry_run else args.gap)
    segments_seen = []
    watch_segments(new_mod, segments_seen)
    openai_kinds = {k.strip() for k in args.openai_kinds.split(",")}
    results = []                     # dicts
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    for prov in providers:
        for name, kind, data, ref in clips:
            if kind not in kinds or (only and kind != "extra" and name not in only):
                continue
            if prov == "openai" and (kind not in openai_kinds or (
                    name in raw and len(raw[name]) / SR >= args.openai_max_seconds)):
                continue
            for path, mod in (("old", old_mod), ("new", new_mod)):
                if path not in paths:
                    continue
                mark = len(segments_seen)
                text, secs, nbytes = run_one(mod, prov, data, keys, budget, args.dry_run)
                seg = segments_seen[mark] if path == "new" and len(segments_seen) > mark else None
                results.append({"clip": name, "kind": kind, "prov": prov, "path": path,
                                "text": text, "secs": secs, "bytes": nbytes, "ref": ref,
                                "seg": seg})
                shown = "(failed)" if text is None else f"...{last4(text)}" if text else "(empty)"
                extra = ""
                if seg is not None:
                    v = seg["voiced"]
                    extra = (f" | final seg outro={seg['outro']} nsp="
                             f"{'?' if seg['nsp'] is None else round(seg['nsp'], 3)} voiced="
                             f"{'?' if v is None else round(v, 2)}s span={seg['span']}")
                print(f"{prov:6s} {kind:6s} {path} {name[:24]:24s} {secs*1000:6.0f}ms "
                      f"{nbytes/1000:7.1f}kB  {shown}{extra}")

    # ── summary ─────────────────────────────────────────────────────────────
    print("\n== summary ==")
    print(f"requests made: {budget.used}")
    for prov in providers:
        for kind in ("real", "stress", "tap", "extra"):
            rows = [r for r in results if r["prov"] == prov and r["kind"] == kind]
            if not rows:
                continue
            line = []
            for path in ("old", "new"):
                got = [r for r in rows if r["path"] == path and r["text"] is not None]
                thanks = sum(ends_in_outro(r["text"], new_mod) for r in got)
                empty = sum(1 for r in got if not r["text"].strip())
                line.append(f"{path}: {thanks}/{len(got)} end in a thanks or outro, {empty} empty")
            print(f"{prov} {kind}: " + "; ".join(line))
        # Real last words: new against old on the same recording, and the
        # stress take's new result against the real recording's old one.
        lost = changed = 0
        for r in results:
            if r["prov"] != prov or r["path"] != "new" or r["text"] is None \
                    or r["kind"] not in ("real", "stress"):
                continue
            ref = next((x for x in results if x["prov"] == prov and x["kind"] == "real"
                        and x["path"] == "old" and x["clip"] == r["ref"]), None)
            if ref is None or ref["text"] is None:
                continue
            a, b = last4(ref["text"]), last4(r["text"])
            if a != b:
                changed += 1
                strip = lambda s: re.sub(r"[^\w\s']", "", s.lower()).split()   # noqa: E731
                if strip(a)[-1:] != strip(b)[-1:] and not ends_in_outro(ref["text"], new_mod):
                    lost += 1
                print(f"  last words differ ({r['kind']} {r['clip'][:24]}): old real "
                      f"'...{a}' / new '...{b}'")
        print(f"{prov}: last four words changed in {changed}, a real last word lost in {lost}")
        # The whole text, old against new on the same clip (compared here,
        # never shown): on a real take with no silent end the upload is the
        # same bytes, so any difference is the detailed answer's own.
        for kind in ("real", "stress"):
            same = total = 0
            for r in results:
                if r["prov"] != prov or r["kind"] != kind or r["path"] != "new" or r["text"] is None:
                    continue
                o = next((x for x in results if x["prov"] == prov and x["kind"] == kind
                          and x["path"] == "old" and x["clip"] == r["clip"]), None)
                if o is None or o["text"] is None:
                    continue
                total += 1
                same += o["text"] == r["text"]
            if total:
                print(f"{prov} {kind}: whole text identical old and new in {same} of {total}")
        for kind in ("real", "stress"):
            secs = {path: [r["secs"] for r in results if r["prov"] == prov and r["kind"] == kind
                           and r["path"] == path and r["text"] is not None]
                    for path in ("old", "new")}
            size = {path: sum(r["bytes"] for r in results if r["prov"] == prov and r["kind"] == kind
                              and r["path"] == path and r["text"] is not None)
                    for path in ("old", "new")}
            if secs["old"] and secs["new"]:
                print(f"{prov} {kind}: provider time median old {statistics.median(secs['old'])*1000:.0f}"
                      f" ms, new {statistics.median(secs['new'])*1000:.0f} ms; uploaded old "
                      f"{size['old']/1e6:.2f} MB, new {size['new']/1e6:.2f} MB")
    seen = [s for s in segments_seen if s["outro"]]
    print(f"final segments that were outros (new path): {len(seen)}")
    for s in seen:
        print(f"  nsp={s['nsp']} voiced={s['voiced']} span={s['span']}")
    nsps = [x for s in segments_seen for x in (s["all_nsp"] or []) if x is not None]
    if nsps:
        print(f"no_speech_prob over every segment seen: min {min(nsps):.3f}, "
              f"median {statistics.median(nsps):.3f}, max {max(nsps):.3f}")


if __name__ == "__main__":
    main()
