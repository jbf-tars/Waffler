"""One watchdog for a dictation, from key release to a tick or a plain error.

Why this exists
---------------
After the hotkey was released nothing on screen said Waffler was working: the
pill was hidden first, and the "Transcribing" label was then drawn on the
hidden pill. There was no overall deadline either. One speech request could
run for 240 s, a fallback and a retry could chain three of them, and Esc only
worked while recording. Working and hung looked the same, and a dictation
could sit on "Processing" for as long as a socket stayed open.

What it does
------------
Every blocking step of a dictation (reading the recording, speech to text,
clean-up, copy and paste) runs through ``DictationRun.call``. The step runs on
its own worker thread and the dictation's thread waits for whichever comes
first:

  * the step finishes, or raises (an exception becomes a result, never a
    crash in a thread nobody watches);
  * the user decides: Cancel (Esc or the pill's X), "Send later" while
    speech to text is slow, or "Paste as is" while the clean-up is slow;
  * the step's deadline passes.

A step that is given up on keeps running on its daemon thread until its own
network timeout, and its result is thrown away. Nothing waits for it.

``PipelineWatchdog`` ticks every quarter second while a dictation is being
processed. It keeps the pill's elapsed time moving, offers the choices once
the wait passes max(8 s, 0.4 x the recording's length), and has a last line
of defence: if a stage runs past its limit with no bounded step to stop it
(something outside ``call`` hung), it gives up on the dictation, says so, and
returns the UI to Ready. Nothing can sit on "Processing" forever.

The module has no Waffler imports and no UI code: the pipeline hands it
callbacks, so the tests drive it with fakes.
"""

import threading
import time

# ── Limits ──────────────────────────────────────────────────────────────────
# The offer ("still working, what now?") comes after max(8 s, 0.4 x audio).
# Most dictations finish in 2-3 s, so 8 s means something is wrong upstream;
# a long recording legitimately takes longer to upload and transcribe.
OFFER_MIN_S = 8.0
OFFER_AUDIO_FACTOR = 0.4
# A stage must have run this long before its offer shows, so an offer never
# flashes up just as the stage is finishing.
OFFER_MIN_STAGE_AGE_S = 2.0

# Speech to text, all attempts together. The per-request timeout alone is up
# to 240 s for a large upload, and a fallback plus a retry can chain three
# requests. 45 s covers any normal dictation many times over; long recordings
# get more time because the upload itself is slow.
#   10 s of audio -> 45 s | 60 s -> 60 s | 300 s -> 180 s | 12 min -> 330 s
TRANSCRIBE_DEADLINE_MIN_S = 45.0
TRANSCRIBE_DEADLINE_BASE_S = 30.0
TRANSCRIBE_DEADLINE_PER_AUDIO_S = 0.5
TRANSCRIBE_DEADLINE_MAX_S = 330.0

# The styler stops itself at 30 s (style_openai._STYLE_DEADLINE_CAP_S). This
# is the backstop in case it does not.
STYLE_DEADLINE_S = 40.0
# Stopping the recording, the clipboard write and the paste keystroke each
# take milliseconds when healthy.
PREPARE_DEADLINE_S = 15.0
PASTE_DEADLINE_S = 8.0
# History and the quality log: local file writes, retried for under a second.
SAVE_LIMIT_S = 20.0
# How far past a stage's limit the watchdog waits before it gives up on the
# whole dictation. Only reached when code outside ``call`` hangs.
STUCK_GRACE_S = 10.0

# ── Stages ──────────────────────────────────────────────────────────────────
PREPARING = "preparing"        # stopping the recording, checking for speech
TRANSCRIBING = "transcribing"  # speech to text
STYLING = "styling"            # the clean-up
PASTING = "pasting"            # clipboard and paste keystroke
SAVING = "saving"              # History and the quality log

# ── What can end a wait early ───────────────────────────────────────────────
KEEP_WAITING = "keep_waiting"
PASTE_RAW = "paste_raw"
SEND_LATER = "send_later"
CANCEL = "cancel"
DEADLINE = "deadline"

# Which choices each stage accepts. Pasting and saving cannot be cancelled:
# by then the words exist, and the paste may already have happened.
STAGE_CHOICES = {
    PREPARING: (CANCEL,),
    TRANSCRIBING: (SEND_LATER, CANCEL),
    STYLING: (PASTE_RAW, CANCEL),
    PASTING: (),
    SAVING: (),
}

# How a finished dictation ended. The pipeline turns these into a tick, a
# plain message, or just "Ready" again.
DONE = "done"
CANCELLED = "cancelled"
NOT_SENT = "not_sent"
NOTHING = "nothing"            # a brushed key, silence, or no words heard
ERROR = "error"
STUCK = "stuck"


def offer_after_s(audio_seconds: float) -> float:
    """Seconds after release before the pill offers a choice."""
    return max(OFFER_MIN_S, OFFER_AUDIO_FACTOR * max(0.0, float(audio_seconds or 0.0)))


def transcribe_deadline_s(audio_seconds: float) -> float:
    """Overall speech-to-text budget for a recording of this length."""
    scaled = (TRANSCRIBE_DEADLINE_BASE_S
              + TRANSCRIBE_DEADLINE_PER_AUDIO_S * max(0.0, float(audio_seconds or 0.0)))
    return min(TRANSCRIBE_DEADLINE_MAX_S, max(TRANSCRIBE_DEADLINE_MIN_S, scaled))


def elapsed_label(seconds: float) -> str:
    """The pill's elapsed time: "7s" under a minute, then "1:05"."""
    s = max(0, int(seconds))
    return f"{s}s" if s < 60 else f"{s // 60}:{s % 60:02d}"


class CallResult:
    """What ``DictationRun.call`` came back with.

    ``status`` is "ok" (``value`` holds the step's return value), "error"
    (``error`` holds the exception), ``DEADLINE``, or the user's choice:
    ``CANCEL``, ``SEND_LATER`` or ``PASTE_RAW``.
    """

    __slots__ = ("status", "value", "error")

    def __init__(self, status, value=None, error=None):
        self.status = status
        self.value = value
        self.error = error

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def __repr__(self):
        return f"CallResult({self.status!r})"


class DictationRun:
    """One dictation's processing. Made by ``PipelineWatchdog.begin``."""

    def __init__(self, generation, clock=time.monotonic):
        self.generation = generation
        self._clock = clock
        self.started = clock()
        self.audio_seconds = 0.0
        self.stage = PREPARING
        self.stage_started = self.started
        self.stage_deadline = PREPARE_DEADLINE_S
        # The offer for the current stage: shown, and answered "keep waiting".
        self.offered = False
        self.offer_dismissed = False
        self.withdraw_pending = False
        self.outcome = None
        # Set when the watchdog gave up on this run. The pipeline must then
        # not paste, whatever it produces later.
        self.abandoned = False
        # Evidence for the stuck handler: what exists of the user's words.
        self.audio_bytes = None
        self.transcript = None
        self.saved_as_unsent = False
        # Last whole second the pill was told about.
        self.shown_second = -1
        self._decision = None
        self._cancelled = False
        self._lock = threading.Lock()
        self._wake = threading.Event()

    # ── time ──────────────────────────────────────────────────────────────

    def elapsed(self) -> float:
        return max(0.0, self._clock() - self.started)

    def stage_elapsed(self) -> float:
        return max(0.0, self._clock() - self.stage_started)

    def set_audio_seconds(self, seconds: float):
        self.audio_seconds = max(0.0, float(seconds or 0.0))

    def offer_after(self) -> float:
        return offer_after_s(self.audio_seconds)

    # ── stages and choices ────────────────────────────────────────────────

    def stage_limit(self, stage: str) -> float:
        """How long ``stage`` may run in total. The watchdog gives up on the
        dictation once a stage outlives its limit by STUCK_GRACE_S."""
        return {
            PREPARING: PREPARE_DEADLINE_S,
            TRANSCRIBING: transcribe_deadline_s(self.audio_seconds),
            STYLING: STYLE_DEADLINE_S,
            PASTING: 2 * PASTE_DEADLINE_S,   # the copy, then the keystroke
            SAVING: SAVE_LIMIT_S,
        }.get(stage, SAVE_LIMIT_S)

    def enter(self, stage: str):
        """Move to ``stage``."""
        with self._lock:
            if stage != self.stage:
                if self.offered and not self.offer_dismissed:
                    # The offer was about the stage that just ended.
                    self.withdraw_pending = True
                self.stage = stage
                self.stage_started = self._clock()
                self.stage_deadline = self.stage_limit(stage)
                self.offered = False
                self.offer_dismissed = False
                # A choice made for the previous stage does not carry over,
                # except Cancel, which is remembered in _cancelled.
                self._decision = None

    def choices(self):
        return STAGE_CHOICES.get(self.stage, ())

    def decide(self, choice: str) -> bool:
        """Record the user's choice. Returns False when the current stage
        does not take it (for example Cancel while pasting)."""
        with self._lock:
            if choice == KEEP_WAITING:
                self.offer_dismissed = True
                return True
            if self.outcome is not None:
                return False
            if choice not in STAGE_CHOICES.get(self.stage, ()):
                return False
            if choice == CANCEL:
                self._cancelled = True
            self._decision = choice
        self._wake.set()
        return True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def finished(self) -> bool:
        return self.outcome is not None

    # ── the bounded wait ──────────────────────────────────────────────────

    def call(self, fn, *args, stage: str, deadline: float, **kwargs) -> CallResult:
        """Run ``fn(*args, **kwargs)`` on a worker thread and wait for it,
        a choice, or ``deadline`` seconds, whichever comes first."""
        self.enter(stage)
        allowed = STAGE_CHOICES.get(stage, ())
        if self._cancelled and CANCEL in allowed:
            return CallResult(CANCEL)

        box = {}
        done = threading.Event()

        def _work():
            try:
                box["value"] = fn(*args, **kwargs)
            except BaseException as e:  # noqa: BLE001 - handed to the caller
                box["error"] = e
            finally:
                done.set()
                self._wake.set()

        threading.Thread(target=_work, daemon=True,
                         name=f"Dictation{self.generation}-{stage}").start()
        t0 = self._clock()
        while True:
            # Clear before checking, so a wake-up between the checks and the
            # wait below is never lost.
            self._wake.clear()
            if done.is_set():
                if "error" in box:
                    return CallResult("error", error=box["error"])
                return CallResult("ok", value=box.get("value"))
            with self._lock:
                choice = self._decision
                if choice is not None and choice in allowed:
                    if choice != CANCEL:
                        self._decision = None
                    return CallResult(choice)
            remaining = deadline - (self._clock() - t0)
            if remaining <= 0:
                return CallResult(DEADLINE)
            self._wake.wait(min(0.25, remaining))


class PipelineWatchdog:
    """Ticks while dictations are processed; see the module docstring.

    Callbacks (all optional, each called with the run first):
        on_begin(run)             processing has started (show the working pill)
        on_working(run, seconds)  once per whole second of processing
        on_offer(run, stage)      the wait passed the offer threshold
        on_withdraw_offer(run)    the offered stage ended before an answer
        on_finished(run, outcome) the run ended; called exactly once
        on_stuck(run)             the run was given up on (then on_finished
                                  follows with STUCK)
        is_current(run)           False once a newer recording has started;
                                  the UI callbacks are then skipped, so an
                                  older dictation never touches the pill that
                                  belongs to the new one
    """

    def __init__(self, *, on_begin=None, on_working=None, on_offer=None,
                 on_withdraw_offer=None, on_finished=None, on_stuck=None,
                 is_current=None, log=None, clock=time.monotonic, tick_s=0.25,
                 autostart=True):
        self._on_begin = on_begin
        self._on_working = on_working
        self._on_offer = on_offer
        self._on_withdraw_offer = on_withdraw_offer
        self._on_finished = on_finished
        self._on_stuck = on_stuck
        self._is_current = is_current or (lambda run: True)
        self._log = log or (lambda msg: None)
        self._clock = clock
        self._tick_s = tick_s
        # False: no ticking thread; the caller calls tick() (tests do, with
        # a fake clock).
        self._autostart = autostart
        self._runs = []
        self._lock = threading.Lock()
        self._thread = None

    # ── lifecycle ─────────────────────────────────────────────────────────

    def begin(self, generation) -> DictationRun:
        run = DictationRun(generation, clock=self._clock)
        with self._lock:
            self._runs.append(run)
            start = self._autostart and (self._thread is None or not self._thread.is_alive())
            if start:
                self._thread = threading.Thread(target=self._loop, daemon=True,
                                                name="PipelineWatchdog")
                try:
                    self._thread.start()
                except RuntimeError as e:
                    # Out of threads. The run still works: every bounded step
                    # keeps its own deadline. Only the ticking is lost.
                    self._thread = None
                    self._log(f"[watchdog] could not start its thread: {e}")
        self._ui(self._on_begin, run)
        return run

    def active(self):
        with self._lock:
            return [r for r in self._runs if not r.finished]

    def current_run(self):
        """The newest unfinished run the UI belongs to, or None."""
        for run in reversed(self.active()):
            if self._safe_is_current(run):
                return run
        return None

    def end(self, run: DictationRun, outcome: str) -> bool:
        """Finish ``run`` with ``outcome``. Only the first call counts."""
        with run._lock:
            if run.outcome is not None:
                return False
            run.outcome = outcome
            offer_open = (run.offered and not run.offer_dismissed) or run.withdraw_pending
            run.withdraw_pending = False
        run._wake.set()
        with self._lock:
            if run in self._runs:
                self._runs.remove(run)
        if offer_open:
            self._ui(self._on_withdraw_offer, run)
        self._ui(self._on_finished, run, outcome)
        return True

    # ── the tick ──────────────────────────────────────────────────────────

    def tick(self):
        """One pass over the active runs. Public so tests can drive it."""
        for run in self.active():
            limit = run.stage_deadline
            if limit is not None and run.stage_elapsed() > limit + STUCK_GRACE_S:
                self._give_up(run)
                continue
            if not self._safe_is_current(run):
                continue
            second = int(run.elapsed())
            if second != run.shown_second:
                run.shown_second = second
                self._ui(self._on_working, run, second)
            with run._lock:
                withdraw = run.withdraw_pending
                run.withdraw_pending = False
                should_offer = bool(
                    not run.offered
                    and run.choices()
                    and run.elapsed() >= run.offer_after()
                    and run.stage_elapsed() >= OFFER_MIN_STAGE_AGE_S
                )
                if should_offer:
                    run.offered = True
                stage = run.stage
            if withdraw:
                self._ui(self._on_withdraw_offer, run)
            if should_offer:
                self._log(f"[watchdog] dictation {run.generation}: still {stage} "
                          f"after {run.elapsed():.1f}s, offering choices")
                self._ui(self._on_offer, run, stage)

    def _give_up(self, run: DictationRun):
        run.abandoned = True
        self._log(f"[watchdog] dictation {run.generation}: stuck in {run.stage} "
                  f"for {run.stage_elapsed():.0f}s (limit {run.stage_deadline:.0f}s), "
                  f"giving up so the app is usable again")
        # The stuck handler may touch files; it runs on its own thread so a
        # hang there cannot stop this watchdog ticking.
        if self._on_stuck is not None:
            threading.Thread(target=self._ui, args=(self._on_stuck, run),
                             daemon=True, name="PipelineWatchdogStuck").start()
        self.end(run, STUCK)

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception as e:  # never let the watchdog itself die
                self._log(f"[watchdog] tick failed: {type(e).__name__}: {e}")
            with self._lock:
                if not any(not r.finished for r in self._runs):
                    self._thread = None
                    return
            time.sleep(self._tick_s)

    # ── helpers ───────────────────────────────────────────────────────────

    def is_current(self, run) -> bool:
        """True while the UI belongs to ``run`` (no newer recording)."""
        return self._safe_is_current(run)

    def _safe_is_current(self, run) -> bool:
        try:
            return bool(self._is_current(run))
        except Exception:
            return False

    def _ui(self, fn, *args):
        if fn is None:
            return
        try:
            fn(*args)
        except Exception as e:
            self._log(f"[watchdog] {getattr(fn, '__name__', 'callback')} failed: "
                      f"{type(e).__name__}: {e}")
