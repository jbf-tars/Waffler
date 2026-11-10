#!/usr/bin/env python3
"""What happened to each dictation, read from app.log: counts and timings only.

For "it didn't paste", "the pill was late", "it feels slower since the update":
questions app.log can answer, but only by following one dictation at a time
through about forty lines from four threads. This does that for every dictation
in the log and prints, for each version of Waffler found in it:

- how each recording ended (pasted, nothing heard, discarded tap, cancelled,
  kept as Not sent, never finished) and how often;
- how long the steps took (key to pill, speech to text, clean-up, paste, key up
  to words), as a median, a 90th centile and the worst;
- what the microphone was doing (how loud speech arrived, the stream being
  opened again at a press, the system's input changing, the Mac waking);
- provider failures, retries and the messages shown;
- what the log itself is made of (bytes a day, by kind of line).

It prints numbers and never a line of the log: app.log can hold words from a
dictation (vocabulary corrections, and transcripts when that setting is on),
and nothing here reads them back out. It only reads; it never writes to the
data folder.

Usage:
    python scripts/dictation_timeline.py                     # ~/.waffler-hosted
    python scripts/dictation_timeline.py app.log.1 app.log   # oldest first
    python scripts/dictation_timeline.py --list              # one row per dictation
    python scripts/dictation_timeline.py --version 3.16.5    # one version only

A time in app.log is HH:MM:SS with no date. The date comes from the pill's
"[overlay-dbg] ... t=<seconds>" lines, which carry the clock; between them the
day is counted from the time going backwards.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

LINE = re.compile(r"^(\d\d):(\d\d):(\d\d)  (.*)$")
T_EPOCH = re.compile(r"\bt=(\d{9,11}\.\d+)")
STARTING = re.compile(r"=== Waffler starting === \(v([0-9][0-9A-Za-z.\-]*)")
NUM = r"(-?\d+(?:\.\d+)?)"

# A recording that never reached "Recording stopped" is one of these.
PASTED, EMPTY, TOO_QUIET, DEAD, NO_AUDIO, TAP = (
    "pasted", "nothing transcribed", "too quiet", "dead stream", "no audio", "tap discarded")
CANCELLED_REC, CANCELLED_WORK, SUPERSEDED, UNSENT, NO_PASTE, UNFINISHED, START_FAILED = (
    "cancelled while recording", "cancelled while working", "superseded (Journal only)",
    "kept as Not sent", "words kept, paste did not finish", "never finished (in the log)",
    "recording did not start")
OUTCOME_ORDER = [PASTED, EMPTY, TOO_QUIET, DEAD, NO_AUDIO, TAP, CANCELLED_REC, CANCELLED_WORK,
                 SUPERSEDED, UNSENT, NO_PASTE, START_FAILED, UNFINISHED]


@dataclass
class Dictation:
    version: str
    started: float                       # seconds since the epoch (local clock), 1 s steps
    line_no: int = 0
    outcome: Optional[str] = None
    hands_free: bool = False
    reopened_mic: Optional[str] = None   # why the stream was opened again at the press
    seconds_since_device_change: Optional[float] = None
    pill_ms: Optional[float] = None      # show asked for -> pill on screen
    held_s: Optional[float] = None       # "Recording duration"
    audio_s: Optional[float] = None      # what the recorder handed back
    silence_pct: Optional[float] = None
    dead_run_s: Optional[float] = None
    speech_db: Optional[float] = None
    boost_db: Optional[float] = None
    chunks: Optional[int] = None
    words: Optional[int] = None
    asr_ms: Optional[float] = None
    style_ms: Optional[float] = None
    style_by: Optional[str] = None
    paste_ms: Optional[float] = None
    total_ms: Optional[float] = None
    working_at: Optional[float] = None   # the pill told "working" (key up), to the millisecond
    done_at: Optional[float] = None      # the pill told the work is over
    paste_outcome: Optional[str] = None
    paste_reason: Optional[str] = None
    done_logged_at: float = 0.0
    flags: List[str] = field(default_factory=list)

    @property
    def keyup_to_done_ms(self) -> Optional[float]:
        if self.working_at is None or self.done_at is None:
            return None
        return (self.done_at - self.working_at) * 1000.0

    @property
    def lead_s(self) -> Optional[float]:
        """Audio handed back, less the time the key was held: about +0.6 s on a
        warm stream (the half second kept before the press, and the tail). Much
        less means the start of the take was not recorded."""
        if self.audio_s is None or self.held_s is None:
            return None
        return self.audio_s - self.held_s


@dataclass
class Report:
    dictations: List[Dictation] = field(default_factory=list)
    counts: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    sessions: List[tuple] = field(default_factory=list)      # (version, started, last line seen)
    bytes_by_kind: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    wake_gaps: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))


def _kind_of(msg: str) -> str:
    """The family a line belongs to, for "what is the log made of"."""
    if "[overlay-dbg]" in msg or "[overlay_mac]" in msg:
        return "pill tracing"
    if msg.startswith("[HOTKEY"):
        return "hotkey"
    if msg.startswith("[audio-monitor] the computer woke"):
        return "wake"
    m = re.match(r"\[([a-zA-Z\-_. ]+)\]", msg)
    if m:
        return m.group(1).split()[0].lower()
    return "other"


def _dated(lines: Iterable[str]):
    """Each line as ``(seconds since the epoch or None, message, size)``; None
    for a line with no time (a traceback). ``t=`` values on the pill's lines
    say what day it is; between them a time that goes back by more than an
    hour is the next day. Lines before the first ``t=`` are dated back from it,
    and a log with none at all is dated from 1970 (the times apart still hold)."""
    rows = []                    # (tod or None, message, size, day, t= or None)
    day, last_tod = 0, None
    for raw in lines:
        size = len(raw.encode("utf-8", "replace"))
        m = LINE.match(raw.rstrip("\n"))
        if not m:
            rows.append((None, "", size, day, None))
            continue
        tod = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        if last_tod is not None and tod < last_tod - 3600:
            day += 1
        last_tod = tod
        msg = m.group(4)
        tm = T_EPOCH.search(msg)
        rows.append((tod, msg, size, day, float(tm.group(1)) if tm else None))

    def base_of(tod, day_no, t):
        # Local midnight of day 0, to the quarter hour every time zone is a multiple of.
        return round((t - tod) / 900.0) * 900.0 - day_no * 86400.0

    base = next((base_of(tod, d, t) for tod, _m, _s, d, t in rows if t is not None), 0.0)
    for tod, msg, size, day_no, t in rows:
        if tod is None:
            yield None, msg, size
            continue
        if t is not None:
            base = base_of(tod, day_no, t)      # days with nothing logged are caught up here
        yield base + day_no * 86400.0 + tod, msg, size


def parse(lines: Iterable[str]) -> Report:
    rep = Report()
    version = "?"
    rec: Optional[Dictation] = None          # being recorded
    working: List[Dictation] = []            # recorded, being turned into text
    await_paste: List[Dictation] = []        # pasted, the paste's own line still to come
    last_device_change: Optional[float] = None
    pending_press = False                    # "starting push-to-talk" with no "Recording started" yet
    show_asked_at: Optional[float] = None
    press_at: Optional[float] = None
    early_paste: Optional[tuple] = None      # a paste's line that came before its "Done"

    def bump(name: str, n: int = 1) -> None:
        rep.counts[version][name] += n

    def finish(d: Optional[Dictation], outcome: str) -> None:
        if d is not None and d.outcome is None:
            d.outcome = outcome

    def oldest() -> Optional[Dictation]:
        for d in working:
            if d.outcome is None:
                return d
        return None

    def newest() -> Optional[Dictation]:
        """The last take handed over: a line the recorder writes as the key
        comes up belongs to it, however many are still being worked on."""
        return working[-1] if working and working[-1].outcome is None else oldest()

    for line_no, (now, msg, size) in enumerate(_dated(lines), 1):
        if now is None:
            rep.bytes_by_kind[version]["no timestamp (tracebacks)"] += size
            continue

        s = STARTING.search(msg)
        if s:
            # A start ends whatever the last run left open.
            finish(rec, UNFINISHED)
            for d in working:
                finish(d, UNFINISHED)
            rec, working, pending_press = None, [], False
            version = s.group(1)
            rep.sessions.append([version, now, now])
            bump("starts")
        rep.bytes_by_kind[version][_kind_of(msg)] += size
        if rep.sessions:
            rep.sessions[-1][2] = now

        tm = T_EPOCH.search(msg)
        precise = float(tm.group(1)) if tm else None

        # ── the key ─────────────────────────────────────────────────────
        if msg.startswith("[HOTKEY"):
            if "press fired" in msg and "key=esc" not in msg:
                bump("key presses")
            elif "→ starting push-to-talk" in msg:
                if pending_press:
                    bump("presses that started no recording")
                pending_press = True
                press_at = now
            elif "press IGNORED (already recording)" in msg:
                # The press that ends a hands-free take is logged as "IGNORED"
                # by the listener (the app ends the take from the same press).
                # In a hands-free take this is the hotkey going down again for
                # hotkey+Space, which ends it; outside one it is a press lost.
                if rec is not None and rec.hands_free:
                    bump("hotkey presses during a hands-free take")
                else:
                    bump("presses ignored while recording")
            elif "release entered sticky=True" in msg or "release IGNORED (recording=True sticky=True)" in msg:
                if rec is not None:
                    rec.hands_free = True
            elif "hands-free ON" in msg or "STICKY" in msg:
                if rec is not None:
                    rec.hands_free = True
            elif "Fn release CANCELLED" in msg:
                bump("Fn releases taken back (key bounce)")
            elif "Esc → CANCEL" in msg:
                bump("Esc cancels (listener)")
            continue

        # ── the pill ────────────────────────────────────────────────────
        if "[overlay-dbg]" in msg:
            if "event=parent.show.enter" in msg and precise is not None:
                show_asked_at = precise
            elif "event=show.posted" in msg and precise is not None:
                if rec is not None and rec.pill_ms is None and show_asked_at is not None:
                    rec.pill_ms = (precise - show_asked_at) * 1000.0
            elif "event=retry." in msg and "retry.ok" not in msg:
                bump("pill had to be shown again (" + msg.split("event=")[1].split()[0] + ")")
            elif "type=working_end" in msg and precise is not None:
                for d in reversed(working + await_paste):
                    if d.working_at is not None and d.done_at is None:
                        d.done_at = precise
                        break
            elif "type=working " in msg and precise is not None:
                d = newest()
                if d is not None and d.working_at is None:
                    d.working_at = precise
            continue

        # ── the recording ───────────────────────────────────────────────
        if msg == "Recording started":
            finish(rec, UNFINISHED)
            pending_press = False
            rec = Dictation(version=version, started=now, line_no=line_no)
            if press_at is not None and now - press_at >= 2:
                # Whole seconds are all the log has: two or more apart is a real wait.
                bump("recordings that began 2 s or more after the press")
            press_at = None
            if last_device_change is not None:
                rec.seconds_since_device_change = now - last_device_change
            rep.dictations.append(rec)
            continue
        if msg.startswith("[audio] opening the microphone again:"):
            if rec is not None:
                rec.reopened_mic = msg.split(":", 1)[1].strip()
            bump("microphone opened again at a press")
            continue
        if msg.startswith("[audio] opened the microphone again between presses"):
            bump("microphone opened again between presses")
            continue
        if msg.startswith("[audio] could not open the microphone again"):
            bump("microphone could not be opened again between presses")
            continue
        if msg.startswith("[pipeline] starting the recording took over") or \
                msg.startswith("[pipeline] the recording did not start"):
            finish(rec, START_FAILED)
            rec = None
            continue
        if msg == "Recording cancelled by user":
            finish(rec, CANCELLED_REC)
            rec = None
            continue
        if msg == "Recording stopped, processing":
            if rec is not None:
                working.append(rec)
                rec = None
            continue

        d = oldest()
        if msg.startswith("Recording duration:"):
            d = newest()
            v = re.search(NUM + "s", msg)
            if d is not None and v:
                d.held_s = float(v.group(1))
            continue
        if msg.startswith("Short press") and "discarding" in msg:
            finish(newest(), TAP)
            continue
        if msg.startswith("Short press") and "transcribing it" in msg:
            if newest() is not None:
                newest().flags.append("short press kept")
            continue
        if msg.startswith("[pipeline] audio captured:"):
            v = re.search(r"(\d+) bytes", msg)
            if d is not None and v:
                d.audio_s = max(0, int(v.group(1)) - 44) / 32000.0
            continue
        if msg.startswith("[pipeline] audio diag:"):
            v = re.search(r"digital-silence=" + NUM + r"%.*longest-dead-run=" + NUM + "s", msg)
            if d is not None and v:
                d.silence_pct, d.dead_run_s = float(v.group(1)), float(v.group(2))
            continue
        if msg.startswith("[whisper] quiet recording:"):
            v = re.search(r"speech at " + NUM + r" dBFS, boosted by " + NUM, msg)
            if d is not None and v:
                d.speech_db, d.boost_db = float(v.group(1)), float(v.group(2))
            continue
        if msg.startswith("[whisper] clip="):
            v = re.search(r"-> (\d+) chunk", msg)
            if d is not None and v:
                d.chunks = int(v.group(1))
            continue
        if msg.startswith("[whisper] single-shot ->") or re.match(r"\[whisper\] chunk \d+/\d+ ->", msg):
            v = re.search(r"-> (\d+) words", msg)
            if d is not None and v:
                d.words = (d.words or 0) + int(v.group(1))
            continue
        if msg.startswith("[whisper] SUSPICIOUS transcript"):
            bump("speech to text sent again (looked short)")
            if d is not None:
                d.flags.append("sent again")
            continue
        if msg.startswith("[whisper] retry no better"):
            bump("  second answer no better")
            continue
        if "segments cover" in msg and "no retry" in msg:
            bump("second request held back (segments cover the voice)")
            continue
        if msg.startswith("[whisper] no retry check on segments"):
            bump("segment check could not run")
            continue
        if msg.startswith("[whisper] dropped an invented ending"):
            bump("invented ending dropped")
            continue
        if msg.startswith("[whisper]") and ("Error" in msg or "retry " in msg):
            bump("speech to text request errors")
            continue
        if msg.startswith("[net]"):
            bump("provider failures ([net] lines)")
            continue
        if msg.startswith("[styling]") and "FAILED" in msg:
            bump("clean-up provider failures")
            continue
        if msg.startswith("[pipeline] transcription:"):
            v = re.search(NUM + "ms", msg)
            if d is not None and v:
                d.asr_ms = float(v.group(1))
            continue
        if msg.startswith("[pipeline] styling ("):
            v = re.match(r"\[pipeline\] styling \(([^)]*)\): " + NUM + "ms", msg)
            if d is not None and v:
                d.style_by, d.style_ms = v.group(1), float(v.group(2))
            continue
        if msg.startswith("[pipeline] styling fell back"):
            bump("clean-up fell back to the basic one")
            continue
        if msg.startswith("[pipeline] clipboard+paste:"):
            v = re.search(NUM + "ms", msg)
            if d is not None and v:
                d.paste_ms = float(v.group(1))
            continue
        if msg.startswith("[pipeline] TOTAL:"):
            v = re.search(NUM + "ms", msg)
            if d is not None and v:
                d.total_ms = float(v.group(1))
            continue
        if msg.startswith("[paste] modifier keys still down"):
            bump("pasted with a modifier key still down")
            if d is not None:
                d.flags.append("modifier down")
            continue
        if msg.startswith("[pipeline] paste did not finish"):
            finish(d, NO_PASTE)
            continue
        if msg.startswith("Done:"):
            if d is not None:
                finish(d, PASTED)
                d.done_logged_at = now
                if early_paste is not None and now - early_paste[0] <= 4:
                    d.paste_outcome, d.paste_reason = early_paste[1], early_paste[2]
                else:
                    await_paste.append(d)
                early_paste = None
            continue
        if msg.startswith("[paste] outcome="):
            # The paste's own line comes up to a second after "Done" (since
            # 3.16.3) or just before it (earlier), and not for every paste, so
            # it is matched to a dictation by time and never by order.
            v = re.match(r"\[paste\] outcome=(\S+) reason=(\S+)", msg)
            if v:
                await_paste[:] = [p for p in await_paste if now - p.done_logged_at <= 4]
                if await_paste:
                    p = await_paste.pop(0)
                    p.paste_outcome, p.paste_reason = v.group(1), v.group(2)
                else:
                    early_paste = (now, v.group(1), v.group(2))
            continue
        if msg == "Empty transcription result":
            finish(d, EMPTY)
            continue
        if msg == "No audio bytes captured":
            finish(d, NO_AUDIO)
            continue
        if msg.startswith("Audio too quiet"):
            finish(d, TOO_QUIET)
            continue
        if msg.startswith("Dead audio stream"):
            if d is None:                      # logged after "Audio too quiet" ended it
                for prev in reversed(working):
                    if prev.outcome == TOO_QUIET:
                        prev.outcome = DEAD
                        break
            else:
                finish(d, DEAD)
            continue
        if msg.startswith("Showing 'couldn"):
            bump("\"couldn't hear you\" shown")
            continue
        if msg.startswith("Suppressing error toast"):
            bump("message held back after a quick tap")
            continue
        if re.match(r"Processing \d+ (cancelled|aborted|discarded)", msg) or \
                re.match(r"Dictation \d+ (cancelled|stopped)", msg):
            if msg.startswith("Processing"):
                finish(d, CANCELLED_WORK)
            continue
        if "not pasted (superseded)" in msg:
            finish(d, SUPERSEDED)
            continue
        if msg.startswith("[pipeline] transcription not finished") or \
                msg.startswith("[pipeline] saved unsent recording"):
            finish(d, UNSENT)
            continue
        if msg.startswith("[pipeline] stopping the recording took over"):
            bump("stopping the microphone was slow")
            if d is not None:
                d.flags.append("slow stop")
            continue
        if msg.startswith("[watchdog]") and "offering choices" in msg:
            bump("\"still working\" choices offered")
            continue
        if msg.startswith("[quality] low"):
            bump("quality check: low")
            continue

        # ── the microphone and the machine ──────────────────────────────
        if msg.startswith("[audio-monitor] default input changed"):
            bump("system input changed")
            last_device_change = now
            if rec is not None:
                rec.flags.append("input changed during the take")
            continue
        if msg.startswith("[audio-monitor] monitoring stream restarted"):
            bump("stream restarted for it while idle")
            continue
        if msg.startswith("[audio-monitor] device changed to") and "take" in msg:
            bump("  restart put off until the take ended")
            continue
        if msg.startswith("[audio-monitor] the computer woke up after"):
            v = re.search(r"after (\d+)s", msg)
            if v:
                rep.wake_gaps[version].append(float(v.group(1)))
            continue
        if msg.startswith("[mic] told the person the input volume is low"):
            bump("quiet-microphone tip shown")
            continue
        if msg.startswith("[mic] dictations are quiet"):
            bump("input volume read (dictations were quiet)")
            continue
        if msg.startswith("[overlay.py] show_toast:"):
            v = re.search(r"style=(\w+)", msg)
            bump("messages shown: " + (v.group(1) if v else "?"))
            continue
        if msg.startswith("[overlay] ✓ Subprocess started"):
            bump("pill process started")
            continue
        if msg.startswith("[update]"):
            bump("update lines")
            continue

    finish(rec, UNFINISHED)
    for d in working:
        finish(d, UNFINISHED)
    return rep


# ── printing ────────────────────────────────────────────────────────────

def _pct(values: List[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    i = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[i]


def _spread(values: List[Optional[float]], unit: str = "ms", digits: int = 0) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "no readings"
    return (f"median {_pct(vals, 0.5):.{digits}f} {unit}, 90% under {_pct(vals, 0.9):.{digits}f}, "
            f"worst {max(vals):.{digits}f}  (n={len(vals)})")


def _day(t: Optional[float]) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t)) if t else "?"


def version_key(v: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"[.\-]", v)]


def render(rep: Report, only: Optional[str] = None, list_rows: bool = False) -> str:
    out: List[str] = []
    versions = sorted({d.version for d in rep.dictations} | set(rep.counts), key=version_key)
    for v in versions:
        if only and v != only:
            continue
        ds = [d for d in rep.dictations if d.version == v]
        sess = [s for s in rep.sessions if s[0] == v]
        span = ""
        if sess:
            first, last = min(s[1] for s in sess), max(s[2] for s in sess)
            days = max((last - first) / 86400.0, 1 / 24)
            span = f"{_day(first)} to {_day(last)}, {days:.1f} days, {len(sess)} start(s)"
        out.append(f"== v{v}  ({span})")
        c = rep.counts.get(v, Counter())
        out.append(f"  recordings: {len(ds)}   key presses: {c.get('key presses', 0)}")
        if ds:
            ends = Counter(d.outcome for d in ds)
            out.append("  how they ended:")
            for name in OUTCOME_ORDER:
                if ends.get(name):
                    out.append(f"    {name:<34} {ends[name]:>4}  {100.0 * ends[name] / len(ds):5.1f}%")
            pasted = [d for d in ds if d.outcome == PASTED]
            po = Counter((d.paste_outcome or "not logged") + ("/" + d.paste_reason
                                                              if d.paste_reason not in (None, "-") else "")
                         for d in pasted)
            out.append("  the paste, as Waffler could tell: " + ", ".join(f"{k} {n}" for k, n in po.most_common()))
            hf = sum(1 for d in ds if d.hands_free)
            out.append(f"  hands-free takes: {hf} of {len(ds)}")
            out.append("  timings:")
            out.append("    key to pill on screen       " + _spread([d.pill_ms for d in ds]))
            out.append("    speech to text              " + _spread([d.asr_ms for d in pasted]))
            out.append("    clean-up (a provider)       " + _spread(
                [d.style_ms for d in pasted if d.style_by not in (None, "local")]))
            out.append("    paste step                  " + _spread([d.paste_ms for d in pasted]))
            out.append("    key up to done              " + _spread([d.keyup_to_done_ms for d in pasted]))
            out.append("    length of a take            " + _spread([d.held_s for d in ds], "s", 1))
            out.append("  microphone:")
            out.append("    speech arrived at           " + _spread([d.speech_db for d in ds], "dBFS", 0))
            out.append("    lift applied                " + _spread([d.boost_db for d in ds], "dB", 1))
            warm = [d.lead_s for d in ds if d.reopened_mic is None]
            cold = [d.lead_s for d in ds if d.reopened_mic is not None]
            out.append("    audio kept beyond the hold  warm: " + _spread(warm, "s", 2))
            out.append("                                opened again at the press: " + _spread(cold, "s", 2))
            near = [d for d in ds if d.seconds_since_device_change is not None
                    and d.seconds_since_device_change <= 30]
            if near:
                out.append(f"    takes begun within 30 s of the system's input changing: {len(near)}: "
                           + ", ".join(f"{k} {n}" for k, n in Counter(d.outcome for d in near).most_common()))
            dead = sum(1 for d in ds if (d.silence_pct or 0) >= 30 or (d.dead_run_s or 0) >= 2)
            out.append(f"    takes with 30% digital silence or a 2 s dead run: {dead}")
        gaps = rep.wake_gaps.get(v) or []
        if gaps:
            out.append(f"  \"the computer woke up\" lines: {len(gaps)} "
                       f"(under 2 minutes asleep: {sum(1 for g in gaps if g < 120)})")
        skip = {"key presses", "starts"}
        rest = [(k, n) for k, n in sorted(c.items()) if k not in skip]
        if rest:
            out.append("  counted:")
            for k, n in rest:
                out.append(f"    {k:<52} {n:>5}")
        kinds = rep.bytes_by_kind.get(v)
        if kinds and sess:
            total = sum(kinds.values())
            days = max((max(s[2] for s in sess) - min(s[1] for s in sess)) / 86400.0, 1 / 24)
            top = ", ".join(f"{k} {100.0 * n / total:.0f}%" for k, n in kinds.most_common(5))
            out.append(f"  log: {total / 1024:.0f} KB, {total / 1024 / days:.0f} KB a day; {top}")
        if list_rows:
            out.append("  when                 held  audio  dBFS  words  asr   style paste  end")
            for d in ds:
                def n(x, f="{:.0f}"):
                    return f.format(x) if x is not None else "-"
                out.append("  " + " ".join([
                    time.strftime("%m-%d %H:%M:%S", time.localtime(d.started)),
                    ("HF" if d.hands_free else "  "),
                    n(d.held_s, "{:6.1f}"), n(d.audio_s, "{:6.1f}"), n(d.speech_db, "{:5.0f}"),
                    n(d.words, "{:5d}"), n(d.asr_ms, "{:5.0f}"), n(d.style_ms, "{:5.0f}"),
                    n(d.paste_ms, "{:5.0f}"), " " + str(d.outcome),
                    ("[" + "; ".join(d.flags + ([f"mic opened again: {d.reopened_mic}"]
                                               if d.reopened_mic else [])) + "]")
                    if (d.flags or d.reopened_mic) else ""]))
        out.append("")
    return "\n".join(out)


def read_logs(paths: List[Path]) -> Iterable[str]:
    for p in paths:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                yield line


def default_logs() -> List[Path]:
    home = Path.home() / ".waffler-hosted"
    return [p for p in (home / "app.log.1", home / "app.log") if p.exists()]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("logs", nargs="*", type=Path, help="app.log files, oldest first")
    ap.add_argument("--list", action="store_true", help="one row of numbers per dictation")
    ap.add_argument("--version", help="only this version of Waffler")
    args = ap.parse_args(argv)
    paths = args.logs or default_logs()
    if not paths:
        print("No app.log found. Give the path to one.", file=sys.stderr)
        return 2
    print(render(parse(read_logs(paths)), only=args.version, list_rows=args.list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
