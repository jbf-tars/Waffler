"""The longest take is 20 minutes (it was 12), S11b.

Synthetic audio and fake providers only: no microphone, no network, no
private data. The audio is a minute of speech-like noise (phrases with pauses)
repeated, so a 20-minute take is a 38 MB array made in a moment.

What is checked, by the places that used to say 12 minutes:

* the limit is one constant (src/take_limit.py) that the recording loop, the
  messages and the docs read, and nothing still says 12;
* one warning a minute before it, through the notifier, then the stop, which
  processes the take as if the key had been let go;
* a recording over the single-upload size is cut at pauses into pieces after
  it ends (the existing size-triggered splitter), and a retry on the other
  provider goes in the same pieces;
* the deadlines grow with the take (speech to text, the offer, the clean-up)
  and the watchdog does not give up on a run that is still inside them;
* Try again and Redo give a long take the same time;
* the numbers behind the Journal's chips do not misfire on a long take;
* the memory a long take needs is bounded.
"""
import io
import re
import sys
import threading
import time
import tracemalloc
import types
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import notifier  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import quality  # noqa: E402
import style_openai  # noqa: E402
import take_limit  # noqa: E402
import transcribe_whisper as tw  # noqa: E402
import user_messages  # noqa: E402
from _pipeline_harness import (  # noqa: E402
    FakeTranscriber, fast_limits, history, make_pipeline, wait_for,
)

RATE = 16000


# ── synthetic audio ──────────────────────────────────────────────────────────

def _minute(seed=1, amp=2500):
    """60 s of speech-like noise: phrases of 3 to 8 s at a syllable rate, with
    pauses between (so the splitter has quiet places to cut)."""
    rng = np.random.default_rng(seed)
    n = 60 * RATE
    t = np.arange(n) / RATE
    env = (np.sin(2 * np.pi * 4.0 * t) * 0.5 + 0.5) ** 1.5
    gate = np.zeros(n, dtype=np.float32)
    pos = 0
    while pos < n:
        talk = int(rng.uniform(3, 8) * RATE)
        gate[pos:pos + talk] = 1.0
        pos += talk + int(rng.uniform(0.6, 1.5) * RATE)
    carrier = np.convolve(rng.standard_normal(n).astype(np.float32), np.ones(4, np.float32) / 4, mode="same")
    sig = carrier * env.astype(np.float32) * gate * amp + rng.standard_normal(n).astype(np.float32) * 4
    return np.clip(sig, -32768, 32767).astype(np.int16)


def long_wav(seconds, amp=2500):
    """A mono 16 kHz WAV of ``seconds`` seconds (a whole number of minutes
    repeated, then trimmed)."""
    minute = _minute(amp=amp)
    pcm = np.tile(minute, int(np.ceil(seconds / 60.0)))[: int(seconds * RATE)]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def seconds_of(wav):
    with wave.open(io.BytesIO(wav), "rb") as w:
        return w.getnframes() / float(w.getframerate())


@pytest.fixture(scope="module")
def wav20():
    return long_wav(20 * 60)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ── the limit is one constant ────────────────────────────────────────────────

def test_the_limit_is_twenty_minutes_with_a_minute_of_warning():
    assert take_limit.MAX_TAKE_S == 20 * 60 == 1200
    assert take_limit.WARN_BEFORE_S == 60
    assert take_limit.WARN_AT_S == 19 * 60
    assert take_limit.minutes() == 20


def test_a_take_at_the_limit_is_ten_pieces_past_the_single_upload_gate():
    size = take_limit.MAX_TAKE_S * take_limit.BYTES_PER_SECOND
    gate = tw._split_audio_on_silence.__defaults__[4]
    assert gate == 18 * 1024 * 1024
    assert size == 38_400_000 and size > gate


def _shipped_text():
    """Every file that ships to a person, as text (not the tests, not the
    changelog, which tells the history)."""
    files = [ROOT / "app.py", ROOT / "README.md"]
    files += sorted((ROOT / "src").glob("*.py"))
    files += sorted((ROOT / "ui").glob("*.js")) + sorted((ROOT / "ui").glob("*.html"))
    files += sorted((ROOT / "prompts").glob("*.txt"))
    for f in files:
        yield f, f.read_text(encoding="utf-8")


def test_nothing_that_ships_still_says_twelve_minutes():
    # A line that says the limit is 12 minutes (a history of the old figure, or
    # a data point such as "12 min -> 390 s", is not that).
    stale = re.compile(r"(?:stop|stops|stopped|limit|maximum|cap|up to|longest|at)\b[^.\n]{0,40}\b(?:12|twelve)[- ]min"
                       r"|\b(?:12|twelve)[- ]min[a-z]*[- ](?:limit|maximum|cap)\b"
                       r"|elapsed\s*>=\s*(?:600|720)\b",
                       re.IGNORECASE)
    hits = []
    for f, text in _shipped_text():
        for n, line in enumerate(text.splitlines(), 1):
            if stale.search(line):
                hits.append(f"{f.name}:{n}: {line.strip()[:100]}")
    assert not hits, "\n".join(hits)


def test_the_recording_loop_reads_the_constant_not_a_number():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    loop = src[src.index("    def _level_loop(self):"):src.index("    def _show_no_audio_toast(self):")]
    assert "_take_limit." in loop
    assert not re.search(r"\b(600|720|1200|1140)\b", loop)


# ── the warning and the stop, with an injected clock ─────────────────────────

def test_the_watch_warns_once_at_nineteen_minutes_and_stops_once_at_twenty():
    clock = Clock(1000.0)
    watch = take_limit.TakeWatch(1000.0, clock=clock)
    seen = []
    for elapsed in (0, 60, 600, 1139.9, 1140, 1140.1, 1150, 1199.9, 1200, 1200.1, 1300):
        clock.t = 1000.0 + elapsed
        seen.append((elapsed, watch.check()))
    assert seen == [(0, "nothing"), (60, "nothing"), (600, "nothing"), (1139.9, "nothing"),
                    (1140, "warn"), (1140.1, "nothing"), (1150, "nothing"), (1199.9, "nothing"),
                    (1200, "stop"), (1200.1, "nothing"), (1300, "nothing")]


def test_a_clock_that_jumps_past_the_limit_stops_without_a_warning_after_the_end():
    clock = Clock(0.0)
    watch = take_limit.TakeWatch(0.0, clock=clock)
    clock.t = 5000.0                      # the computer slept
    assert watch.check() == "stop"
    assert watch.check() == "nothing"


def test_a_clock_that_goes_backwards_is_not_a_negative_take():
    watch = take_limit.TakeWatch(1000.0, clock=Clock(900.0))
    assert watch.elapsed() == 0.0 and watch.check() == "nothing"


def test_the_limit_can_be_given_to_a_watch_for_a_shorter_take():
    clock = Clock(0.0)
    watch = take_limit.TakeWatch(0.0, clock=clock, max_s=100, warn_before_s=10)
    clock.t = 89
    assert watch.check() == "nothing"
    clock.t = 90
    assert watch.check() == "warn"
    clock.t = 100
    assert watch.check() == "stop"


# ── the recording loop (the real _level_loop from app.py) ────────────────────

class LoopRig:
    """The pipeline's own _level_loop running on a thread, with a clock the
    test turns. ``released`` is how many times the stop was asked for."""

    def __init__(self, tmp_path):
        self.p = make_pipeline(tmp_path)
        self.clock = Clock(5000.0)
        self.p._recording_start_time = 5000.0
        self.p._take_watch = lambda: take_limit.TakeWatch(5000.0, clock=self.clock)
        self.p._level_loop = types.MethodType(self.p.ns["_level_loop"], self.p)
        self.released = []
        self.p.on_hotkey_release = lambda: (self.released.append(1), setattr(self.p, "is_recording", False))
        self.resets = []
        self.p.hotkey_listener.reset_state = lambda: self.resets.append(1)
        self.p.is_recording = True
        self.thread = threading.Thread(target=self.p._level_loop, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def at(self, elapsed):
        self.clock.t = 5000.0 + elapsed

    def toasts(self):
        return self.p.overlay.toasts()


def test_one_warning_a_minute_before_the_limit_through_the_notifier(tmp_path):
    rig = LoopRig(tmp_path).start()
    rig.at(600)
    time.sleep(0.15)
    assert rig.toasts() == []                          # the old 10-minute toast is gone
    rig.at(1140)
    assert wait_for(lambda: len(rig.toasts()) == 1)
    time.sleep(0.15)
    rig.at(1160)
    time.sleep(0.15)
    toasts = rig.toasts()
    assert len(toasts) == 1                            # once
    assert toasts[0]["heading"] == "Long recording"
    assert toasts[0]["body"] == "One minute left, then Waffler will stop and clean up what you said."
    assert toasts[0]["style"] == "warn" and toasts[0]["timeout"] == 6
    assert rig.released == []                          # a warning stops nothing
    rig.p.is_recording = False
    rig.thread.join(2)
    assert not rig.thread.is_alive()


def test_at_twenty_minutes_it_stops_and_processes_as_if_the_key_were_let_go(tmp_path):
    rig = LoopRig(tmp_path).start()
    rig.at(1140)
    assert wait_for(lambda: len(rig.toasts()) == 1)
    rig.at(1200)
    assert wait_for(lambda: rig.released == [1])
    rig.thread.join(2)
    assert not rig.thread.is_alive()                   # the loop ends with the take
    toasts = rig.toasts()
    assert len(toasts) == 2
    assert toasts[1]["heading"] == "Recording stopped"
    assert toasts[1]["body"] == ("That was 20 minutes, the longest one take can be. "
                                 "Waffler is cleaning up what you said.")
    assert rig.resets == [1]                           # a hands-free take does not linger in the hotkey
    assert rig.released == [1]                         # asked once


def test_a_jump_past_the_limit_still_stops_it(tmp_path):
    rig = LoopRig(tmp_path).start()
    rig.at(4000)
    assert wait_for(lambda: rig.released == [1])
    rig.thread.join(2)
    assert [t["heading"] for t in rig.toasts()] == ["Recording stopped"]


def test_a_newer_recording_ends_the_older_loop_without_a_stop(tmp_path):
    rig = LoopRig(tmp_path).start()
    time.sleep(0.1)
    rig.p._recording_session += 1                      # another take has begun
    rig.thread.join(2)
    assert not rig.thread.is_alive()
    assert rig.released == [] and rig.toasts() == []


def test_the_levels_still_reach_the_pill(tmp_path):
    rig = LoopRig(tmp_path).start()
    assert wait_for(lambda: "update_level" in rig.p.overlay.names())
    rig.p.is_recording = False
    rig.thread.join(2)


def test_the_toasts_use_the_central_words(tmp_path):
    assert user_messages.take_limit_warning() == (
        "Long recording", "One minute left, then Waffler will stop and clean up what you said.")
    heading, body = user_messages.take_limit_stopped(20)
    assert heading == "Recording stopped" and "20 minutes" in body
    for text in (*user_messages.take_limit_warning(), heading, body):
        assert "—" not in text and "–" not in text and "--" not in text


def test_the_notifier_knows_both_messages():
    for kind in ("long_recording", "recording_limit"):
        assert notifier.KINDS[kind].severity == notifier.INFO
    n = notifier.Notifier(store=notifier.MemoryStore(), context=lambda: notifier.Context(recording=True),
                          clock=Clock(100.0))
    # Information, not a tip: it is said while recording, which a tip never is.
    assert n.request("long_recording").show


# ── auto-stop at the limit processes the take end to end ─────────────────────

def test_the_take_stopped_at_the_limit_is_transcribed_cleaned_pasted_and_kept(tmp_path, wav20, monkeypatch):
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=20.0, TRANSCRIBE_DEADLINE_BASE_S=20.0,
                TRANSCRIBE_DEADLINE_MAX_S=20.0, STYLE_DEADLINE_S=20.0, OFFER_MIN_S=30.0)
    p = make_pipeline(tmp_path, wav=wav20)
    p.is_recording = True
    p._recording_start_time = time.time() - take_limit.MAX_TAKE_S
    p._processing_id = p._current_press_id = 7
    p.on_hotkey_release()                              # what the level loop calls at the limit
    assert wait_for(lambda: len(history(p)) == 1, timeout=30)
    entry = history(p)[0]
    # On a failure say what the entry and the log hold (the text is synthetic): this test
    # failed once, on the Windows runner only, with an empty text and nothing else to go on.
    seen = ({k: v for k, v in entry.items() if k not in ("before", "after")}, list(p.logged)[-20:])
    assert entry["text"] == "ok so ship it on monday", seen
    assert p.clipboard.pastes, "the take was pasted"
    assert entry["duration_s"] == pytest.approx(1200.0, abs=0.5)
    assert p.transcriber.calls == 1
    assert (tmp_path / "debug_audio").is_dir() and any((tmp_path / "debug_audio").iterdir())
    assert not entry.get("failed")


# ── a take at the limit is cut into pieces after it ends ─────────────────────

def test_a_twenty_minute_wav_is_cut_at_pauses_into_pieces_of_about_two_minutes(wav20):
    chunks = tw._split_audio_on_silence(wav20)
    assert 8 <= len(chunks) <= 11
    total = sum(seconds_of(c) for c in chunks)
    assert total == pytest.approx(1200.0, abs=0.1)     # nothing dropped
    assert all(len(c) < 18 * 1024 * 1024 for c in chunks)
    assert all(60 <= seconds_of(c) <= 151 for c in chunks[:-1])
    joined = b"".join(np.frombuffer(c[44:], dtype=np.int16).tobytes() for c in chunks)
    assert joined == np.frombuffer(wav20[44:], dtype=np.int16).tobytes()   # sample for sample


def test_a_ten_minute_wav_is_still_cut_and_a_nine_minute_one_is_not():
    assert len(tw._split_audio_on_silence(long_wav(10 * 60))) >= 4
    nine = long_wav(9 * 60)
    assert len(nine) < 18 * 1024 * 1024
    assert tw._split_audio_on_silence(nine) == [nine]       # one upload, as always


def test_a_two_minute_wav_is_one_upload():
    two = long_wav(120)
    assert tw._split_audio_on_silence(two) == [two]


class PieceRecorder:
    """A WhisperTranscriber with fake providers: it records each upload."""

    def __init__(self, words_per_piece=180, first_provider="groq"):
        t = tw.WhisperTranscriber.__new__(tw.WhisperTranscriber)
        t._backend = "groq"
        t._cloud_order = ["groq", "openai"]
        t.client = object()
        t._groq_client = object()
        t._last_cloud_provider = first_provider
        self.sent = []
        self.t = t
        words = words_per_piece

        def dispatch(_self, audio, exclude=None):
            self.sent.append((len(audio), exclude))
            _self._last_cloud_provider = "openai" if exclude == "groq" else "groq"
            return " ".join(["word"] * words)

        t.__class__ = type("FakeSTT", (tw.WhisperTranscriber,), {"_dispatch_one": dispatch})


def test_a_twenty_minute_take_goes_up_in_pieces_each_below_the_provider_limit(wav20):
    rec = PieceRecorder()
    out = rec.t.transcribe_sync(wav20)
    assert 8 <= len(rec.sent) <= 11
    assert max(size for size, _ in rec.sent) < 18 * 1024 * 1024
    assert len(out.split()) == 180 * len(rec.sent)           # every piece's words kept
    assert rec.t.last_speech_seconds > 600                   # measured once for the whole take
    assert rec.t.last_retry_fired is False


def test_a_retry_of_a_twenty_minute_take_goes_to_the_other_provider_in_pieces(wav20):
    """The suspicious-transcript retry used to send the whole 38 MB at once,
    which neither provider takes. It goes in the same pieces now."""
    rec = PieceRecorder(words_per_piece=60)                # ~0.6 words a second of speech: too few
    out = rec.t.transcribe_sync(wav20)
    first_pass = [s for s in rec.sent if s[1] is None]
    retry = [s for s in rec.sent if s[1] == "groq"]
    assert rec.t.last_retry_fired is True
    assert len(retry) == len(first_pass) >= 8              # the same pieces, to the other provider
    assert max(size for size, _ in rec.sent) < 18 * 1024 * 1024
    assert all(size < 18 * 1024 * 1024 for size, _ in retry)
    assert not out.startswith("word word word") or len(out.split()) >= 60 * len(first_pass)


def test_a_short_take_still_retries_whole():
    short = long_wav(100)
    rec = PieceRecorder(words_per_piece=3)
    rec.t.transcribe_sync(short)
    retry = [s for s in rec.sent if s[1] == "groq"]
    assert len(retry) == 1 and retry[0][0] == len(short) + 2 * RATE * 2 * 300 // 1000


def test_the_retry_reuses_the_speech_measured_for_the_first_pass(wav20, monkeypatch):
    calls = []
    real = tw._speech_seconds
    monkeypatch.setattr(tw, "_speech_seconds", lambda b: (calls.append(len(b)), real(b))[1])
    rec = PieceRecorder()
    rec.t.transcribe_sync(wav20)
    whole = [n for n in calls if n > 18 * 1024 * 1024]
    assert len(whole) == 1, "the whole take was measured more than once"


# ── the deadlines grow with the take ─────────────────────────────────────────

def test_the_speech_deadline_for_a_twenty_minute_take_is_ten_and_a_half_minutes():
    assert pw.transcribe_deadline_s(1200) == 630.0
    assert pw.transcribe_deadline_s(1200) > pw.transcribe_deadline_s(720) > pw.transcribe_deadline_s(300)
    assert pw.transcribe_deadline_s(0) == pw.TRANSCRIBE_DEADLINE_MIN_S == 45.0
    assert pw.transcribe_deadline_s(10) == 45.0                 # ordinary dictations, as they were
    assert pw.transcribe_deadline_s(60) == 60.0
    assert pw.transcribe_deadline_s(300) == 180.0


def test_the_speech_deadline_never_goes_down_and_is_bounded():
    values = [pw.transcribe_deadline_s(s) for s in range(0, 3000, 30)]
    assert values == sorted(values)
    assert max(values) == pw.TRANSCRIBE_DEADLINE_MAX_S == 660.0


def test_the_local_model_gets_twice_the_time():
    assert pw.transcribe_deadline_s(1200, local=True) == 1230.0
    assert pw.transcribe_deadline_s(10, local=True) == 90.0
    assert pw.transcribe_deadline_s(1200, True) > 2 * pw.transcribe_deadline_s(1200) - 100
    assert pw.transcribe_deadline_s(1200, local=False) == pw.transcribe_deadline_s(1200)


def test_the_offer_comes_at_the_old_time_up_to_six_minutes_and_at_two_and_a_half_after():
    assert pw.offer_after_s(60) == pytest.approx(24.0)
    assert pw.offer_after_s(300) == pytest.approx(120.0)
    assert pw.offer_after_s(375) == pytest.approx(150.0)
    assert pw.offer_after_s(720) == 150.0                # 4.8 minutes before
    assert pw.offer_after_s(1200) == 150.0               # 8 minutes before
    assert pw.offer_after_s(1200) < pw.transcribe_deadline_s(1200)


def test_the_clean_up_budget_is_not_scaled_by_the_take():
    """Speech to text grows with the take; the clean-up's own 30 s cap (and the
    40 s backstop) stay what tests/test_style_deadline.py pins. A 20-minute take
    is about 3,000 words: if a slow clean-up provider does not finish in time,
    the words are pasted as said, never lost."""
    assert style_openai._style_deadline_for(3000) == style_openai._STYLE_DEADLINE_CAP_S == 30.0
    run = pw.DictationRun(1)
    run.set_audio_seconds(1200)
    assert run.stage_limit(pw.STYLING) == pw.STYLE_DEADLINE_S == 40.0


def test_the_run_sizes_its_stage_limits_from_the_take():
    run = pw.DictationRun(1)
    run.set_audio_seconds(1200)
    assert run.stage_limit(pw.TRANSCRIBING) == 630.0
    run.local_stt = True
    assert run.stage_limit(pw.TRANSCRIBING) == 1230.0


class WClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _watch(clock):
    stuck = []
    wd = pw.PipelineWatchdog(clock=clock, autostart=False, on_stuck=stuck.append)
    return wd, stuck


def test_the_watchdog_does_not_give_up_on_a_long_take_inside_its_deadline():
    clock = WClock()
    wd, stuck = _watch(clock)
    run = wd.begin(1)
    run.set_audio_seconds(1200)
    run.enter(pw.TRANSCRIBING)
    # 330 s was the old cap (plus the 10 s grace): a 20-minute take is still fine long after.
    for t in (60, 200, 340, 341, 500, 639.9):
        clock.t = t
        wd.tick()
        assert not run.finished, f"given up at {t} s"
    assert stuck == []
    clock.t = 640.1                                     # 630 s and the grace
    wd.tick()
    assert run.finished and run.outcome == pw.STUCK


def test_the_watchdog_still_gives_up_on_an_ordinary_dictation_at_the_old_time():
    clock = WClock()
    wd, _ = _watch(clock)
    run = wd.begin(1)
    run.set_audio_seconds(10)
    run.enter(pw.TRANSCRIBING)
    clock.t = 54.9
    wd.tick()
    assert not run.finished
    clock.t = 55.1                                      # 45 s and the grace
    wd.tick()
    assert run.finished


def test_a_local_run_of_a_long_take_is_not_given_up_on_either():
    clock = WClock()
    wd, _ = _watch(clock)
    run = wd.begin(1)
    run.set_audio_seconds(1200)
    run.local_stt = True
    run.enter(pw.TRANSCRIBING)
    clock.t = 1000.0
    wd.tick()
    assert not run.finished


def test_the_offer_for_a_long_take_comes_after_two_and_a_half_minutes():
    clock = WClock()
    offers = []
    wd = pw.PipelineWatchdog(clock=clock, autostart=False, on_offer=lambda r, s: offers.append(s))
    run = wd.begin(1)
    run.set_audio_seconds(1200)
    run.enter(pw.TRANSCRIBING)
    clock.t = 149.0
    wd.tick()
    assert offers == []
    clock.t = 151.0
    wd.tick()
    assert offers == [pw.TRANSCRIBING]


# ── the pipeline gives a long take the long deadlines ────────────────────────

class CallSpy:
    """DictationRun.call, recording the stage, the deadline and the run."""

    def __init__(self, monkeypatch):
        self.calls = []
        real = pw.DictationRun.call

        def spy(run, fn, *args, stage, deadline, **kwargs):
            self.calls.append((stage, deadline, run.audio_seconds, run.local_stt))
            return real(run, fn, *args, stage=stage, deadline=deadline, **kwargs)

        monkeypatch.setattr(pw.DictationRun, "call", spy)

    def of(self, stage):
        return [c for c in self.calls if c[0] == stage]


def test_a_dictation_of_twenty_minutes_is_given_the_long_speech_deadline(tmp_path, wav20, monkeypatch):
    spy = CallSpy(monkeypatch)
    p = make_pipeline(tmp_path, wav=wav20, transcriber=FakeTranscriber(text=" ".join(["word"] * 3000)))
    p.is_recording = True
    p._recording_start_time = time.time() - 1200
    p.on_hotkey_release()
    assert wait_for(lambda: len(history(p)) == 1, timeout=30)
    (_, speech_deadline, secs, local), = spy.of(pw.TRANSCRIBING)
    assert secs == pytest.approx(1200.0, abs=0.1)
    assert speech_deadline == pytest.approx(630.0, abs=0.1) and local is False
    # The clean-up keeps its own, unchanged budget (its cap is pinned by tests/test_style_deadline.py).
    (_, style_deadline, _s, _l), = spy.of(pw.STYLING)
    assert style_deadline == pw.STYLE_DEADLINE_S == 40.0


def test_the_local_model_is_given_its_longer_deadline(tmp_path, monkeypatch):
    spy = CallSpy(monkeypatch)
    t = FakeTranscriber()
    t._backend = "faster"
    p = make_pipeline(tmp_path, transcriber=t)
    p.is_recording = True
    p.on_hotkey_release()
    assert wait_for(lambda: len(history(p)) == 1, timeout=10)
    (_, deadline, _s, local), = spy.of(pw.TRANSCRIBING)
    assert local is True and deadline == pw.transcribe_deadline_s(3.0, True) == 90.0
    assert p._stt_is_local() is True
    t._backend = "groq"
    assert p._stt_is_local() is False


def test_trying_again_on_a_not_sent_card_of_a_long_take_gives_it_the_long_deadline(tmp_path, wav20, monkeypatch):
    p = make_pipeline(tmp_path, wav=wav20)
    p.transcriber.error = ConnectionError("Connection error.")
    p.is_recording = True
    p.on_hotkey_release()
    assert wait_for(lambda: any(e.get("failed") for e in history(p)), timeout=30)
    card = [e for e in history(p) if e.get("failed")][-1]
    assert (tmp_path / "unsent" / card["unsent_id"]).stat().st_size == len(wav20)    # all 38 MB kept
    p.transcriber.error = None
    spy = CallSpy(monkeypatch)
    result = p.resend_unsent(card["unsent_id"])
    assert result["ok"] is True
    (_, deadline, secs, _l), = spy.of(pw.TRANSCRIBING)
    assert secs == pytest.approx(1200.0, abs=0.1) and deadline == pytest.approx(630.0, abs=0.1)


def test_redo_of_a_long_take_gives_it_the_long_deadline(tmp_path, wav20, monkeypatch):
    p = make_pipeline(tmp_path, wav=wav20)
    p.is_recording = True
    p.on_hotkey_release()
    assert wait_for(lambda: len(history(p)) == 1, timeout=30)
    entry = history(p)[0]
    assert entry.get("audio")
    spy = CallSpy(monkeypatch)
    p.transcriber.text = "second go at the same take"
    result = p.redo_take(entry.get("id"), entry["timestamp"], entry["text"])
    assert result["ok"] is True, result
    (_, deadline, secs, _l), = spy.of(pw.TRANSCRIBING)
    assert secs == pytest.approx(1200.0, abs=0.1) and deadline == pytest.approx(630.0, abs=0.1)


# ── the Journal's numbers do not misfire on a long take ──────────────────────

def _assess(**over):
    kw = dict(speech_seconds=900.0, transcript_words=2700, styled_words=2600, asr_filtered=False,
              styling_provider="groq", styled_text="A long, clean take that ends properly.")
    kw.update(over)
    return quality.assess(**kw)


def test_a_clean_twenty_minute_take_has_no_flags():
    r = _assess()
    assert r["level"] == "ok" and r["flags"] == []
    assert r["words_per_speech_second"] == pytest.approx(3.0)


def test_a_long_take_that_lost_most_of_its_words_is_still_flagged():
    r = _assess(transcript_words=500)
    assert r["level"] == "low" and "low_word_rate" in r["flags"]


def test_long_pauses_inside_a_long_take_do_not_read_as_a_dead_microphone():
    # 20 minutes in 0.25 s windows; speech with a few 20 s gaps of digital silence, then speech to the end.
    windows = [50.0] * 4800
    for start in (400, 1500, 2600, 3700):
        windows[start:start + 80] = [0.0] * 80
    sig = quality.mic_dropout_signal(windows, window_s=0.25)
    assert sig["suspected"] is False and sig["terminal"] is False


def test_a_microphone_that_dies_part_way_through_a_long_take_is_still_noticed():
    windows = [50.0] * 3000 + [0.0] * 1800                  # the last six minutes are dead
    sig = quality.mic_dropout_signal(windows, window_s=0.25)
    assert sig["suspected"] is True


# ── the memory a long take needs ─────────────────────────────────────────────

def _naive_rms(samples, win):
    n = len(samples) // win
    block = samples[: n * win].astype(np.float32).reshape(n, win)
    return np.sqrt(np.mean(block * block, axis=1))


def test_the_blockwise_window_rms_is_the_same_numbers_as_the_whole_array():
    for seconds in (0.2, 5, 130, 301):
        s = np.frombuffer(long_wav(seconds)[44:], dtype=np.int16)
        assert np.array_equal(tw._window_rms(s, 480), _naive_rms(s, 480)), seconds
    assert tw._window_rms(np.zeros(100, dtype=np.int16), 480).shape == (0,)


def test_measuring_and_cutting_a_twenty_minute_take_does_not_copy_it_whole(wav20):
    tracemalloc.start()
    tw._split_audio_on_silence(wav20)
    _, split_peak = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    tw._speech_seconds(wav20)
    _, speech_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # The take is 38 MB; a float copy of it would be 77 MB (twice, squared).
    assert speech_peak < 60e6, speech_peak
    assert split_peak < 120e6, split_peak          # the pieces (38 MB) and the frames (38 MB)


def test_lifting_a_quiet_twenty_minute_take_stays_well_below_the_old_peak():
    quiet = long_wav(20 * 60, amp=60)
    tracemalloc.start()
    out = tw.boost_quiet_speech(quiet)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert out != quiet                            # it was quiet enough to be lifted
    assert len(out) == len(quiet)
    assert peak < 260e6, peak                      # 449 MB before it was worked in blocks


def _old_boost(audio_bytes):
    """boost_quiet_speech as 3.15.1 wrote it, whole array at a time."""
    with wave.open(io.BytesIO(audio_bytes), "rb") as r:
        params = r.getparams()
        a = np.frombuffer(r.readframes(params.nframes), dtype=np.int16).astype(np.float32) / 32768.0
    peak = float(np.abs(a).max())
    frame = max(1, int(params.framerate * params.nchannels * 0.03))
    n = a.size // frame
    rms = np.sqrt((a[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    floor = float(np.percentile(rms, 10))
    voiced = rms[rms > max(floor * 4.0, 10 ** (-70 / 20))]
    level = float(np.median(voiced))
    gain = 10 ** ((tw.SPEECH_TARGET_DBFS - 20 * np.log10(level)) / 20)
    loud = max(float(np.percentile(np.abs(a), 99.9)), 1e-6)
    gain = min(gain, 10 ** (tw.SPEECH_MAX_GAIN_DB / 20), tw.SPEECH_PEAK_CEILING / loud)
    assert peak > 0
    out = a * gain
    over = np.abs(out) > tw.SPEECH_PEAK_CEILING
    if over.any():
        head = 1.0 - tw.SPEECH_PEAK_CEILING
        x = np.abs(out[over]) - tw.SPEECH_PEAK_CEILING
        out[over] = np.sign(out[over]) * (tw.SPEECH_PEAK_CEILING + head * np.tanh(x / head))
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setparams(params)
        w.writeframes((out * 32767.0).astype(np.int16).tobytes())
    return b.getvalue()


def test_working_the_lift_in_blocks_gives_the_same_bytes_as_before():
    quiet = np.frombuffer(long_wav(150, amp=100)[44:], dtype=np.int16).copy()
    quiet[::40000] = 30000                          # clicks that reach the limiter
    quiet[5::90000] = -32768
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(quiet.tobytes())
    clip = buf.getvalue()
    new = tw.boost_quiet_speech(clip)
    assert new != clip
    assert new == _old_boost(clip)
    # A take that is more than one block of 1M samples long, and one that is not.
    short = long_wav(3, amp=100)
    assert tw.boost_quiet_speech(short) == _old_boost(short)


def test_the_two_whole_take_arrays_of_the_silence_checks_are_let_go_in_the_pipeline():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src[src.index("    def _process(self, processing_id: int):"):src.index("    def _stt_is_local(self)")]
    assert "del audio_arr" in body and "del _arr" in body


# ── the disk a long take needs, and what it is called ────────────────────────

def test_the_recent_audio_buffer_keeps_ten_whatever_their_length(tmp_path):
    import recent_audio
    for i in range(12):
        recent_audio.keep(tmp_path, b"x" * 1000, {}, now=None)
        time.sleep(0.002)
    assert len(recent_audio.files(tmp_path)) == recent_audio.KEEP == 10


def test_the_readme_names_the_twenty_minutes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "20 minutes" in readme or "20-minute" in readme
