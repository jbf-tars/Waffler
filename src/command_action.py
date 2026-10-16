"""Running a command: from the held shortcut to the result.

    hold the shortcut         -> not while a dictation or another action is running
      (recording starts; the app in front, the selected text, or the text
       before the cursor, are noted while the person speaks)
    let go                    -> the recording stops, the spoken instruction is turned into text
      -> one call to the person's model: edit, draft or ask (src/command_mode.py)
      -> edit   the result replaces the selection (the safe paste)
         draft  the result is pasted at the cursor (or, if text was selected, put
                on the clipboard: a draft never replaces a selection)
         ask    the browser opens at the person's search engine with the question
      -> a Journal entry, and a toast that says what happened and how to undo it

It is one run of its own, on its own thread, and it keeps out of the
dictation's way: the dictation state (``is_recording``, ``_process``) is never
touched. After the person lets go every step that can block goes through
``run.call`` under a watchdog of its own (src/pipeline_watchdog.py), with 30
seconds in all from letting go to the model's answer. Esc and the pill's X stop
it until the paste begins. While the person is still holding the keys, the
recording is not under the watchdog (there is no telling how long an
instruction takes to say), only under a 60 second cap in case the key-up never
arrives.

The person's text is never lost. Until the paste starts it is exactly where
it was; if the paste fails the result is on the clipboard (the safe paste
leaves it there) and in the Journal. If the person has moved to another app or
changed the selection while the model was thinking, nothing is pasted.

The selected text is read twice at most: at key-down through Accessibility
(no keystroke is needed, so it can be done while the shortcut's keys are
held), and, for an app that does not share it (a browser, an Electron app,
anything on Windows), by pressing Copy once the keys are up, because a Copy
sent with the shortcut's own modifier keys down would reach the app as another
shortcut.

This module knows nothing of the window, the overlay, the microphone or the
clipboard itself. app.py hands it a ``Hooks`` made of the pipeline's own
methods, so the tests run the whole flow with fakes.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import command_mode as cm
import history_meta
import pasteboard
import pipeline_watchdog as pw
import text_context
import transforms
import user_messages as um

# Where the time goes. The 30 seconds are counted from letting go to the end
# of the model call; speech to text gets at most half of it.
READ_DEADLINE_S = 6.0           # the Copy read after letting go
CAPTURE_READ_S = 2.5            # the Accessibility reads while holding
CAPTURE_WAIT_S = 3.0            # how long, after letting go, to wait for them
STT_DEADLINE_S = 15.0
TOTAL_DEADLINE_S = 30.0
MIN_ASK_S = 5.0                 # the model is never given less than this
ASK_MARGIN_S = 2.0              # the provider chain stops this long before the watchdog does
CHECK_DEADLINE_S = 3.0          # "is the selection still there?"
READ_REPLY_S = 3.0              # reading the model's reply (pure text work: a second at the very most)
OPEN_DEADLINE_S = 5.0
PASTE_DEADLINE_S = pw.PASTE_DEADLINE_S
MAX_HOLD_S = 60.0               # a key-up that never arrives must not record for ever
TAP_S = 0.3                     # holding for less than this was a brush of the keys

# Toast kinds (src/notifier.py): where each message is shown and how often.
RESULT, BUSY, FAILED = "command_result", "command_busy", "command_failed"
NOTICE_S = 6.0
DONE_S = 8.0

STARTED, ALREADY_RUNNING, BUSY_ELSEWHERE, OFF = "started", "busy", "busy_elsewhere", "off"
SHOW_ACTION = "show_command"            # the toast's "Show in Journal" (app.py _on_toast_action)
SETTINGS_ACTION = "open_ax_settings"

# A run's phases.
ARMING, RECORDING, PROCESSING, FINISHED = "arming", "recording", "processing", "finished"

# Windows of 0.25 s (4,000 samples at 16 kHz), as the dictation's silence test uses.
_WINDOW = 4000
_MIN_RMS = 12.0


def audio_problem(audio_bytes, held_s: float) -> Optional[str]:
    """Is there nothing to turn into text? ``"tap"`` (the keys were held for
    less than 0.3 s: a brush, said nothing about), ``"none"`` (no audio),
    ``"silent"`` (no 0.25 s stretch of it was louder than room tone), or None.
    The silence test is the dictation's own (RMS 12 in any window)."""
    if held_s < TAP_S:
        return "tap"
    if not audio_bytes or len(audio_bytes) <= 44:
        return "none"
    try:
        import numpy as np
        data = np.frombuffer(audio_bytes[44:len(audio_bytes) - (len(audio_bytes) - 44) % 2],
                             dtype=np.int16).astype(np.float32)
        for i in range(0, len(data), _WINDOW):
            window = data[i:i + _WINDOW]
            if len(window) < 400:
                break
            if float(np.sqrt(np.mean(window ** 2))) >= _MIN_RMS:
                return None
        return "silent"
    except Exception:      # noqa: BLE001 - if it cannot be measured, let the speech service decide
        return None


def _noop(*_a, **_k):
    return None


@dataclass
class Hooks:
    """Everything with a side effect, as plain functions. The ones with no
    default are the ones a command cannot run without."""

    settings: Callable[[], dict]                                # settings.json, now
    busy_elsewhere: Callable[[], bool]                          # a dictation, a transform
    start_audio: Callable[[], None]
    stop_audio: Callable[[], bytes]
    transcribe: Callable[[bytes], Any]                          # -> (text, info)
    read_selection: Callable[[bool], Any]                       # (allow_copy) -> text_context.SelectionResult (blocking)
    chat: Callable[..., Any]                                    # styler._chat(system, user, ...) -> ChatReply
    paste: Callable[..., Any]                                   # (text, window, restore, ticket) -> PasteOutcome
    settle_paste: Callable[[Any, Any], tuple]                   # (CallResult, ticket) -> (on_clipboard, pasted)
    copy: Callable[[str], bool]
    save_history: Callable[[dict], bool]                        # stamps the entry's id
    say: Callable[..., None]                                    # (kind, style, heading, body, buttons=None, timeout=None)
    open_url: Callable[[str], bool]
    permissions_ok: Callable[[], bool] = lambda: True           # announces itself when it says no
    read_context: Callable[[int], tuple] = lambda chars: ("", "")   # the text before and after the caret
    level: Callable[[], float] = lambda: 0.0
    audio_problem: Callable[[Any, float], Optional[str]] = audio_problem
    apply_vocab: Callable[[str], str] = lambda text: text
    classify_speech_error: Callable[[Any], str] = lambda error: "error"
    speech_provider: Callable[[], str] = lambda: ""
    front_app: Callable[[], Any] = lambda: text_context.UNKNOWN
    window: Callable[[], Any] = lambda: None                    # Windows: the target window
    reread_selection: Callable[[str], Any] = lambda method: None    # the selection again, read the way it was first
    dashes: Optional[Callable[[str], str]] = None               # the styler's own helpers
    profanity: Optional[Callable[[str, str], str]] = None
    remember: Callable[[str], None] = _noop                     # "Copy again" must copy the result
    record_usage: Callable[[Any], None] = _noop                 # the model call
    record_speech: Callable[[float, Any], None] = _noop         # (seconds of audio, info)
    announce_item: Callable[[dict], None] = _noop               # the Journal card appears
    cue: Callable[[str], None] = _noop                          # "start" / "stop"
    no_audio: Callable[[], None] = _noop                        # "We couldn't hear you", through the governor
    ui_begin: Callable[[Any], None] = _noop                     # recording: the pill, the status, Esc
    ui_level: Callable[[float], None] = _noop
    ui_working: Callable[[Any], None] = _noop                   # let go: the pill turns to its working look
    ui_tick: Callable[[Any, int], None] = _noop
    ui_end: Callable[[Any, str], None] = _noop
    log: Callable[[str], None] = _noop
    now_iso: Callable[[], str] = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")
    monotonic: Callable[[], float] = time.monotonic
    platform: str = "darwin"
    spawn: Callable[[Callable[[], None]], None] = field(
        default=lambda fn: threading.Thread(target=fn, daemon=True, name="Command").start())


class CommandRun:
    """One command, from key-down to its end."""

    def __init__(self, ident: int, now: float):
        self.id = ident
        self.phase = ARMING
        self.pressed = now
        self.released_at: Optional[float] = None
        self.release = threading.Event()        # the keys came up, or the run must stop holding
        self.cancelled = False
        self.aborted = False                    # the reads at key-down found a reason to stop (already said)
        self.wd: Optional[pw.DictationRun] = None
        self.ctx = text_context.UNKNOWN
        self.window = None
        self.selection = None                   # text_context.SelectionResult from the Accessibility read
        self.needs_copy = False                 # Accessibility could not say: Copy it once the keys are up
        self.empty_by_copy = False              # "nothing selected" was found by a Copy that copied nothing
        self.before = ""                        # the text before the cursor, when nothing is selected
        self.captured = threading.Event()
        self.lock = threading.Lock()
        self.capture_open = True                # the key-down reads may still report; closed once they are used
        self.audio_started = False
        self.audio_stopped = False
        self.ui_ended = False
        self.shown = ""                         # what the pill is: "", "recording" or "working"


def _bounded(fn: Callable[[], Any], timeout: float):
    """Run ``fn`` on a thread of its own and wait up to ``timeout`` seconds.
    Returns ``(True, value)``, or ``(False, None)`` if it raised or was still
    running (it is then left to finish by itself)."""
    box: dict = {}
    done = threading.Event()

    def work():
        try:
            box["value"] = fn()
        except BaseException as e:      # noqa: BLE001
            box["error"] = e
        finally:
            done.set()

    threading.Thread(target=work, daemon=True, name="CommandRead").start()
    if done.wait(timeout) and "error" not in box:
        return True, box.get("value")
    return False, None


class CommandRunner:
    def __init__(self, hooks: Hooks, *, read_deadline_s: float = READ_DEADLINE_S,
                 stt_deadline_s: float = STT_DEADLINE_S, total_deadline_s: float = TOTAL_DEADLINE_S,
                 paste_deadline_s: float = PASTE_DEADLINE_S, max_hold_s: float = MAX_HOLD_S,
                 capture_read_s: float = CAPTURE_READ_S, capture_wait_s: float = CAPTURE_WAIT_S,
                 min_ask_s: float = MIN_ASK_S, watchdog: Optional[pw.PipelineWatchdog] = None):
        self.h = hooks
        self.read_deadline_s = read_deadline_s
        self.stt_deadline_s = stt_deadline_s
        self.total_deadline_s = total_deadline_s
        self.paste_deadline_s = paste_deadline_s
        self.max_hold_s = max_hold_s
        self.capture_read_s = capture_read_s
        self.capture_wait_s = capture_wait_s
        self.min_ask_s = min_ask_s
        self._lock = threading.Lock()
        self._active = False
        self._run: Optional[CommandRun] = None
        self._ids = itertools.count(1)
        self._watchdog = watchdog or pw.PipelineWatchdog(
            on_begin=lambda wd: self._ui_working(wd),
            on_working=lambda wd, seconds: hooks.ui_tick(self._owner(wd), seconds),
            on_finished=lambda wd, outcome: self._ui_finished(self._owner(wd), outcome),
            log=hooks.log)

    # ── asked by the rest of the app ──────────────────────────────────────

    def busy(self) -> bool:
        """A command is recording or running (so a dictation must wait)."""
        return self._active

    def down(self) -> str:
        """The shortcut's keys went down. Starts a command if one may run now
        and returns at once: the recording and everything after are on a
        thread of their own. Called from the shortcut's own thread."""
        h = self.h
        try:
            enabled = cm.settings(h.settings())["enabled"]
        except Exception as e:      # noqa: BLE001
            h.log(f"[command] settings unreadable: {type(e).__name__}")
            enabled = False
        if not enabled:
            h.log("[command] asked while Command mode is off: ignored")
            return OFF
        refuse = None
        run = None
        with self._lock:
            if self._active:
                refuse = (ALREADY_RUNNING, um.COMMAND_BUSY_ALREADY)
            elif self._busy_elsewhere():
                refuse = (BUSY_ELSEWHERE, um.COMMAND_BUSY_ELSEWHERE)
            else:
                run = CommandRun(next(self._ids), h.monotonic())
                self._run, self._active = run, True
        if refuse is not None:
            self._say_busy(refuse[1])
            return refuse[0]
        try:
            h.spawn(lambda: self._thread(run))
        except Exception as e:      # noqa: BLE001 - out of threads
            with self._lock:
                self._run, self._active = None, False
            h.log(f"[command] could not start: {type(e).__name__}")
            raise
        return STARTED

    def up(self) -> None:
        """The keys came up (any one of them). The recording ends and the
        command is carried out. Ignored when nothing is being held."""
        with self._lock:
            run = self._run
        if run is None or run.phase not in (ARMING, RECORDING):
            self.h.log("[command] keys up with no command to end: ignored")
            return
        run.released_at = self.h.monotonic()
        run.release.set()

    def cancel(self) -> bool:
        """Esc or the pill's X. True when a command is running (the key is then
        ours); it stops at once unless the paste has begun."""
        with self._lock:
            run = self._run
            active = self._active
        if run is None:
            return active
        run.cancelled = True
        run.release.set()
        wd = run.wd
        if wd is not None:
            if wd.decide(pw.CANCEL):
                self.h.log("[command] cancelled by the user")
            else:
                self.h.log("[command] cancel came too late: the paste has begun")
        else:
            self.h.log("[command] cancelled while holding")
        return active

    def refuse_dictation(self) -> None:
        """The dictation hotkey went down while a command runs."""
        self._say_busy(um.COMMAND_BLOCKS_DICTATION)

    # ── the run ───────────────────────────────────────────────────────────

    def _busy_elsewhere(self) -> bool:
        try:
            return bool(self.h.busy_elsewhere())
        except Exception:      # noqa: BLE001 - if it cannot be told, do not guess "free"
            return True

    def _say_busy(self, message) -> None:
        self._say(BUSY, "info", message, timeout=NOTICE_S)

    def _say(self, kind, style, message, buttons=None, timeout=None) -> None:
        heading, body = message
        try:
            self.h.say(kind, style, heading, body, buttons, timeout)
        except Exception as e:      # noqa: BLE001 - a toast must never end a run badly
            self.h.log(f"[command] message not shown: {type(e).__name__}")

    def _owner(self, _wd=None) -> Optional[CommandRun]:
        """The command the watchdog's run belongs to: there is only ever one."""
        with self._lock:
            return self._run

    def _ui_working(self, wd) -> None:
        run = self._owner(wd)
        if run is None:
            return
        run.shown = "working"
        try:
            self.h.ui_working(run)
        except Exception as e:      # noqa: BLE001
            self.h.log(f"[command] working look not shown: {type(e).__name__}")

    def _ui_finished(self, run: Optional[CommandRun], outcome: str) -> None:
        """The pill, the status and Esc go back, exactly once per run."""
        if run is None or run.ui_ended:
            return
        run.ui_ended = True
        run.phase = FINISHED
        try:
            self.h.ui_end(run, outcome)
        except Exception as e:      # noqa: BLE001
            self.h.log(f"[command] pill not put back: {type(e).__name__}")

    def _thread(self, run: CommandRun) -> None:
        outcome = pw.ERROR
        try:
            outcome = self._work(run)
        except Exception as e:      # noqa: BLE001 - one line, never the text
            self.h.log(f"[command] failed: {type(e).__name__}")
            self._say(FAILED, "warn", ("Couldn't run that", "Something went wrong. Your text is unchanged."),
                      timeout=NOTICE_S)
        finally:
            # The recording is stopped if it still runs, then the pill and the
            # status are put back first, while this run still counts as
            # running: a new one cannot start (and show its pill) just before
            # the old one's end hides it.
            try:
                self._stop_recording(run)
                self._ui_finished(run, outcome)
                if run.wd is not None:
                    self._watchdog.end(run.wd, outcome)
            finally:
                with self._lock:
                    self._run = None
                    self._active = False

    def _stop_recording(self, run: CommandRun) -> bytes:
        """Stop the recorder if this run started it and nobody has. Bounded:
        the recorder's lock can be held while a device is rebuilt."""
        if not run.audio_started or run.audio_stopped:
            return b""
        run.audio_stopped = True
        ok, value = _bounded(self.h.stop_audio, pw.PREPARE_DEADLINE_S)
        if not ok:
            self.h.log("[command] the recorder did not stop in time")
            return b""
        return value or b""

    def _front(self):
        try:
            return self.h.front_app()
        except Exception:      # noqa: BLE001
            return text_context.UNKNOWN

    # ── holding ───────────────────────────────────────────────────────────

    def _work(self, run: CommandRun) -> str:
        h = self.h
        cfg = cm.settings(h.settings())

        # Microphone and Accessibility, before anything starts: a switched-off
        # permission gets one message with a button to the right pane.
        try:
            allowed = h.permissions_ok()
        except Exception as e:      # noqa: BLE001 - a check that errors does not stop the command
            h.log(f"[command] permission check skipped: {type(e).__name__}")
            allowed = True
        if not allowed:
            return pw.NOTHING

        # Where the person is, noted before the pill appears (as a dictation
        # notes it): the instruction is for this app and this window.
        run.ctx = self._front()
        try:
            run.window = h.window()
        except Exception:      # noqa: BLE001
            run.window = None

        run.phase = RECORDING
        try:
            h.start_audio()
        except Exception as e:      # noqa: BLE001
            h.log(f"[command] the recording did not start: {type(e).__name__}")
            self._say(FAILED, "warn", ("Couldn't record", "Waffler couldn't use your microphone. "
                                       "Check it is connected and try again."), timeout=NOTICE_S)
            return pw.ERROR
        run.audio_started = True
        run.shown = "recording"
        h.ui_begin(run)
        h.cue("start")
        capture = threading.Thread(target=self._capture, args=(run,), daemon=True, name="CommandCapture")
        capture.start()

        # Hold: until the keys come up, Esc, a reason to stop, or the cap.
        while not run.release.wait(0.033):
            try:
                h.ui_level(h.level())
            except Exception:      # noqa: BLE001 - the meter is cosmetic
                pass
            if h.monotonic() - run.pressed > self.max_hold_s:
                h.log(f"[command] held for {self.max_hold_s:.0f}s with no key-up: treated as let go")
                break
        held = h.monotonic() - run.pressed
        if run.released_at is None:
            run.released_at = h.monotonic()
        if run.cancelled or run.aborted:
            self._stop_recording(run)
            if run.cancelled:
                self._say(RESULT, "info", um.COMMAND_CANCELLED, timeout=NOTICE_S)
                return pw.CANCELLED
            return pw.NOTHING
        return self._process(run, cfg, held, capture)

    def _capture(self, run: CommandRun) -> None:
        """While the person speaks: the selected text through Accessibility
        (no keystroke), and, when nothing is selected, the text before the
        cursor as context. What it finds is reported once, at the end, and
        only if the run is still waiting for it: a read that hung and answers
        after the keys are up must not change a run that has gone on without
        it. Anything that cannot go on is said at once."""
        h = self.h
        found = {"selection": None, "needs_copy": False, "before": "", "abort": None}
        try:
            ok, sel = _bounded(lambda: h.read_selection(False), self.capture_read_s)
            if not ok or sel is None:
                found["needs_copy"] = True          # could not say in time: Copy it once the keys are up
                return
            found["selection"] = sel
            text = sel.text or ""
            if text.strip():
                if transforms.selection_problem(text) == transforms.TOO_LONG:
                    found["abort"] = (um.TRANSFORM_TOO_LONG, None, NOTICE_S)
                return
            reason = sel.reason
            if reason == text_context.TOO_LARGE:
                found["abort"] = (um.TRANSFORM_TOO_LONG, None, NOTICE_S)
            elif reason == text_context.SECURE_FIELD:
                found["abort"] = (um.transform_read_message(reason), None, NOTICE_S)
            elif reason == text_context.ACCESSIBILITY_OFF:
                found["abort"] = (um.transform_read_message(reason), [
                    {"label": "Open Settings", "action": SETTINGS_ACTION, "kind": "primary"},
                    {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}], 12)
            elif reason == text_context.NO_SELECTION:
                got, pair = _bounded(lambda: h.read_context(cm.CONTEXT_CHARS), self.capture_read_s)
                if got and isinstance(pair, (tuple, list)) and pair:
                    found["before"] = str(pair[0] or "")
            else:
                found["needs_copy"] = True          # Accessibility could not say
        except Exception as e:      # noqa: BLE001
            h.log(f"[command] reading the selection failed: {type(e).__name__}")
            found["needs_copy"] = True
        finally:
            with run.lock:
                if run.capture_open:
                    run.selection, run.needs_copy, run.before = found["selection"], found["needs_copy"], found["before"]
                    run.captured.set()
                    abort = found["abort"]
                else:
                    abort = None
            if abort is not None:
                self._abort(run, abort[0], buttons=abort[1], timeout=abort[2])

    def _abort(self, run: CommandRun, message, buttons=None, timeout=NOTICE_S) -> None:
        run.aborted = True
        self.h.log("[command] stopped before the instruction: the selection cannot be used")
        self._say(FAILED, "warn", message, buttons, timeout=timeout)
        run.release.set()

    # ── processing ────────────────────────────────────────────────────────

    def _remaining(self, run: CommandRun) -> float:
        return max(self.min_ask_s, run.released_at + self.total_deadline_s - self.h.monotonic())

    def _process(self, run: CommandRun, cfg: dict, held: float, capture: threading.Thread) -> str:
        h = self.h
        run.phase = PROCESSING
        wd = run.wd = self._watchdog.begin(run.id)
        if run.cancelled:
            wd.decide(pw.CANCEL)

        # 1. The recording. It is stopped whatever else has happened: a cancel
        # that landed while the working pill was starting must not leave the
        # recorder running, which is why this is not a step Cancel can skip.
        run.audio_stopped = True
        ok, audio = _bounded(h.stop_audio, pw.PREPARE_DEADLINE_S)
        if not ok:
            h.log("[command] the recording was not read")
            self._say(FAILED, "warn", ("Your mic was slow to stop",
                                       "Nothing was changed. Try the command again."), timeout=NOTICE_S)
            return pw.ERROR
        if run.cancelled:
            return self._cancelled()
        h.cue("stop")
        wd.set_audio_seconds(len(audio or b"") / 32000.0)
        problem = h.audio_problem(audio, held)
        if problem == "tap":
            h.log(f"[command] a brush of the keys ({held:.2f}s): nothing to do")
            return pw.NOTHING
        if problem:
            h.log(f"[command] nothing to hear in the recording ({problem})")
            h.no_audio()
            return pw.NOTHING

        # 2. The selection, if Accessibility could not say while the keys were down.
        capture.join(self.capture_wait_s)
        with run.lock:
            run.capture_open = False                # whatever answers from here on is too late to count
            heard_back = run.captured.is_set()
        if run.aborted:
            return pw.NOTHING
        if not heard_back:
            run.needs_copy = True                   # the read is still hanging: ask by copying
        if run.needs_copy:
            got = wd.call(lambda: h.read_selection(True), stage=pw.PREPARING, deadline=self.read_deadline_s)
            if got.status == pw.CANCEL:
                return self._cancelled()
            if not got.ok or got.value is None:
                self._say(FAILED, "warn", um.transform_read_message("timed_out"), timeout=NOTICE_S)
                return pw.ERROR
            sel = got.value
            if sel.text.strip() and sel.reason in ("", text_context.OK):
                problem = transforms.selection_problem(sel.text)
                if problem:
                    self._say(RESULT, "info", um.TRANSFORM_TOO_LONG, timeout=NOTICE_S)
                    return pw.NOTHING
                run.selection = sel
            elif sel.reason in (text_context.NO_SELECTION,):
                run.selection = sel
                run.empty_by_copy = True
            elif sel.reason == text_context.TOO_LARGE:
                self._say(RESULT, "info", um.TRANSFORM_TOO_LONG, timeout=NOTICE_S)
                return pw.NOTHING
            else:
                return self._refuse_read(sel)
        selection = run.selection.text if run.selection is not None and run.selection.reason in ("", text_context.OK) else ""
        method = run.selection.method if selection else text_context.METHOD_NONE

        # 3. The instruction.
        asr_deadline = min(self.stt_deadline_s, self._remaining(run))
        heard = wd.call(h.transcribe, audio, stage=pw.TRANSCRIBING, deadline=asr_deadline)
        if heard.status == pw.CANCEL:
            return self._cancelled()
        if heard.status == pw.DEADLINE:
            h.log(f"[command] speech to text did not answer in {asr_deadline:.0f}s")
            self._say(FAILED, "warn", um.COMMAND_TOO_SLOW, timeout=NOTICE_S)
            return pw.ERROR
        if not heard.ok:
            kind = "error"
            try:
                kind = h.classify_speech_error(heard.error)
            except Exception:      # noqa: BLE001
                pass
            h.log(f"[command] speech to text failed: {type(heard.error).__name__} ({kind})")
            self._say(FAILED, "warn", um.command_speech_failed_message(kind, h.speech_provider()), timeout=NOTICE_S)
            return pw.ERROR
        text, info = heard.value if isinstance(heard.value, tuple) else (heard.value, {})
        try:
            h.record_speech(len(audio or b"") / 32000.0, info)
        except Exception as e:      # noqa: BLE001
            h.log(f"[command] speech usage not recorded: {type(e).__name__}")
        try:
            text = h.apply_vocab(text or "")
        except Exception as e:      # noqa: BLE001 - a vocabulary that cannot be applied must not cost the command
            h.log(f"[command] vocabulary not applied: {type(e).__name__}")
        instruction = cm.tidy_instruction(text)
        bad = cm.instruction_problem(instruction)
        if bad == "empty":
            h.log("[command] no words were heard")
            h.no_audio()
            return pw.NOTHING
        if bad == "too_long":
            self._say(RESULT, "info", um.COMMAND_TOO_LONG, timeout=NOTICE_S)
            return pw.NOTHING
        if run.cancelled:
            return self._cancelled()

        # 4. The model decides.
        request = cm.build_request(instruction, selection, "" if selection else run.before)
        remaining = self._remaining(run)
        deadline_at = h.monotonic() + max(1.0, remaining - ASK_MARGIN_S)

        def ask():
            return h.chat(request.system, request.user, max_tokens=request.max_tokens,
                          temperature=request.temperature, deadline=deadline_at, purpose="command")

        asked = wd.call(ask, stage=pw.STYLING, deadline=remaining)
        if asked.status != "ok":
            return self._ask_failed(asked, remaining)
        reply = asked.value
        self._count(reply)
        if run.cancelled:
            return self._cancelled()
        # Reading the reply is only text work, but it holds the interpreter while it runs: under
        # the watchdog, so a Cancel or the deadline frees the window even if it ever drags.
        reading = wd.call(lambda: cm.parse_reply(getattr(reply, "text", ""), has_selection=bool(selection),
                                                 instruction=instruction, own_text=selection or run.before,
                                                 max_chars=cm.reply_limit(request.max_tokens)),
                          stage=pw.STYLING, deadline=READ_REPLY_S)
        if reading.status == pw.CANCEL:
            return self._cancelled()
        if not reading.ok:
            return self._reply_unreadable(reading)
        parsed = reading.value
        h.log(f"[command] the model chose {parsed.action}"
              f"{'' if parsed.tagged else ' (the reply had no tag: guessed)'}"
              f"{', as a draft because nothing was selected' if parsed.changed else ''}")
        started = run.released_at
        common = dict(run=run, cfg=cfg, wd=wd, instruction=instruction, selection=selection, method=method,
                      reply=reply, started=started)
        if parsed.action == cm.ASK:
            return self._do_ask(parsed=parsed, **common)
        if parsed.action == cm.EDIT:
            return self._do_edit(parsed=parsed, **common)
        return self._do_draft(parsed=parsed, **common)

    # ── the three actions ─────────────────────────────────────────────────

    def _do_ask(self, *, run, cfg, wd, instruction, selection, method, reply, started, parsed) -> str:
        h = self.h
        engine = cm.engine_for(cfg["web_engine"])
        query = cm.ask_query(parsed.payload, instruction)
        url = cm.search_url(engine.id, query)
        wd.enter(pw.PASTING)                    # from here on Cancel is too late, and says so
        if run.cancelled:
            return self._cancelled()
        opened = wd.call(h.open_url, url, stage=pw.PASTING, deadline=OPEN_DEADLINE_S)
        if not opened.ok or opened.value is False:
            h.log(f"[command] the browser was not opened ({opened.status})")
            self._say(FAILED, "warn", um.COMMAND_OPEN_FAILED, timeout=NOTICE_S)
            return pw.ERROR
        entry = self._save(cm.ASK, run, instruction, reply, started, "", query=query, engine=engine.id)
        self._say(RESULT, "info", um.command_searching_message(engine.label), self._journal_buttons(entry),
                  timeout=DONE_S)
        return pw.DONE

    def _do_edit(self, *, run, cfg, wd, instruction, selection, method, reply, started, parsed) -> str:
        h = self.h
        finished = wd.call(cm.finish_edit, selection, parsed.payload, stage=pw.STYLING, deadline=READ_REPLY_S,
                           finish_reason=self._finish_reason(reply, parsed), tagged=parsed.tagged,
                           dashes=h.dashes, profanity=h.profanity)
        if finished.status == pw.CANCEL:
            return self._cancelled()
        if not finished.ok:
            return self._reply_unreadable(finished)
        result = finished.value
        if result.status != transforms.REPLACE:
            return self._not_replaced(result)
        after = result.text
        if run.cancelled:
            return self._cancelled()
        # Still the same place?
        moved = wd.call(lambda: self._moved(run.ctx, selection, method), stage=pw.STYLING,
                        deadline=CHECK_DEADLINE_S)
        if moved.status == pw.CANCEL:
            return self._cancelled()
        if not moved.ok or moved.value:
            return self._not_pasted(cm.EDIT, run, instruction, selection, after, reply, started, sure=moved.ok)
        return self._paste_in(cm.EDIT, run, cfg, wd, instruction, selection, after, reply, started)

    def _do_draft(self, *, run, cfg, wd, instruction, selection, method, reply, started, parsed) -> str:
        h = self.h
        finished = wd.call(cm.finish_draft, parsed.payload, instruction, "" if selection else run.before,
                           stage=pw.STYLING, deadline=READ_REPLY_S,
                           finish_reason=self._finish_reason(reply, parsed), dashes=h.dashes, profanity=h.profanity)
        if finished.status == pw.CANCEL:
            return self._cancelled()
        if not finished.ok:
            return self._reply_unreadable(finished)
        result = finished.value
        if result.status != transforms.REPLACE:
            return self._not_replaced(result)
        after = result.text
        if run.cancelled:
            return self._cancelled()
        if selection:
            # A draft never replaces what is selected (pasting would): it is
            # put on the clipboard for the person to place.
            return self._copied(cm.DRAFT, run, instruction, "", after, reply, started, selected=True)
        moved = wd.call(lambda: self._moved(run.ctx, "", text_context.METHOD_NONE), stage=pw.STYLING,
                        deadline=CHECK_DEADLINE_S)
        if moved.status == pw.CANCEL:
            return self._cancelled()
        if not moved.ok or moved.value:
            return self._not_pasted(cm.DRAFT, run, instruction, "", after, reply, started, sure=moved.ok)
        if run.empty_by_copy:
            # Accessibility could not say, and the Copy that found "nothing
            # selected" may only have been slow or ignored. Pasting over a
            # selection that is there would lose it, so look once more.
            appeared = wd.call(self._selection_appeared, stage=pw.STYLING, deadline=CHECK_DEADLINE_S)
            if appeared.status == pw.CANCEL:
                return self._cancelled()
            if not appeared.ok or appeared.value:
                h.log("[command] text turned out to be selected after all: the draft is put on the clipboard")
                return self._copied(cm.DRAFT, run, instruction, "", after, reply, started, selected=True)
        return self._paste_in(cm.DRAFT, run, cfg, wd, instruction, "", after, reply, started)

    def _paste_in(self, action, run, cfg, wd, instruction, before, after, reply, started) -> str:
        h = self.h
        stored = h.settings()
        if stored.get("auto_paste", True) is False:
            return self._copied(action, run, instruction, before, after, reply, started)
        wd.enter(pw.PASTING)            # from here on Cancel is too late, and says so
        if run.cancelled:
            return self._cancelled()
        ticket = pasteboard.PasteTicket()
        restore = pasteboard.restore_after_paste(stored)
        pasted = wd.call(h.paste, after, run.window, restore, ticket, stage=pw.PASTING,
                         deadline=self.paste_deadline_s)
        try:
            _on_clipboard, went_in = h.settle_paste(pasted, ticket)
        except Exception as e:      # noqa: BLE001 - what became of the paste is unknown, so say it went in
            h.log(f"[command] paste outcome not read: {type(e).__name__}")
            went_in = True
        label = history_meta.paste_label(pasted, went_in)
        if not went_in:
            h.remember(after)               # so the failed-paste message's "Copy again" copies the result
        entry = self._save(action, run, instruction, reply, started, label, before=before, after=after,
                           stored=stored)
        if went_in:
            self._say(RESULT, "info", um.command_done_message(action, h.platform),
                      self._journal_buttons(entry), timeout=DONE_S)
        return pw.DONE if went_in else pw.ERROR

    # ── the parts ─────────────────────────────────────────────────────────

    def _cancelled(self) -> str:
        self._say(RESULT, "info", um.COMMAND_CANCELLED, timeout=NOTICE_S)
        return pw.CANCELLED

    def _refuse_read(self, sel) -> str:
        reason = sel.reason
        if reason == text_context.ACCESSIBILITY_OFF:
            buttons = [{"label": "Open Settings", "action": SETTINGS_ACTION, "kind": "primary"},
                       {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}]
            self._say(FAILED, "warn", um.transform_read_message(reason), buttons, timeout=12)
        else:
            self._say(FAILED, "warn", um.transform_read_message(reason), timeout=NOTICE_S)
        self.h.log(f"[command] the selection could not be read ({reason})")
        return pw.NOTHING

    def _ask_failed(self, asked, remaining: float) -> str:
        h = self.h
        if asked.status == pw.CANCEL:
            self._count_late(asked.late)
            return self._cancelled()
        if asked.status == pw.DEADLINE:
            self._count_late(asked.late)
            h.log(f"[command] the model did not answer in {remaining:.0f}s")
            self._say(FAILED, "warn", um.COMMAND_TOO_SLOW, timeout=NOTICE_S)
            return pw.ERROR
        error = asked.error
        reason = getattr(error, "reason", None)
        if reason:
            h.log(f"[command] no provider answered: {str(reason)[:120]}")
            self._say(FAILED, "warn", um.command_failed_message(reason), timeout=NOTICE_S)
        else:
            h.log(f"[command] the model call failed: {type(error).__name__}")
            self._say(FAILED, "warn", um.command_failed_message(""), timeout=NOTICE_S)
        return pw.ERROR

    @staticmethod
    def _finish_reason(reply, parsed):
        """A reply longer than any reply can be was cut by the reader: it counts as cut off."""
        return "length" if getattr(parsed, "cut", False) else getattr(reply, "finish_reason", None)

    def _reply_unreadable(self, reading) -> str:
        """The reply could not be read in time (or failed): nothing is used."""
        error = reading.error
        self.h.log(f"[command] the reply could not be read ({reading.status}"
                   f"{': ' + type(error).__name__ if error is not None else ''})")
        self._say(FAILED, "warn", um.command_failed_message(""), timeout=NOTICE_S)
        return pw.ERROR

    def _not_replaced(self, result) -> str:
        status = result.status
        self.h.log(f"[command] not used ({status}; {result.words_in} words in, {result.words_out} out)")
        if status == transforms.NO_CHANGE:
            self._say(RESULT, "info", um.COMMAND_NO_CHANGE, timeout=NOTICE_S)
            return pw.NOTHING
        message = {transforms.TRUNCATED: um.COMMAND_CUT_SHORT,
                   transforms.EMPTY_ANSWER: um.COMMAND_NO_ANSWER,
                   transforms.UNRELATED: um.TRANSFORM_UNRELATED,
                   cm.REFUSED: um.COMMAND_REFUSED}.get(status, um.COMMAND_BAD_LENGTH)
        self._say(FAILED, "warn", message, timeout=NOTICE_S)
        return pw.NOTHING

    def _count(self, reply) -> None:
        try:
            self.h.record_usage(reply)
        except Exception as e:      # noqa: BLE001 - a usage row never fails a command
            self.h.log(f"[command] usage not recorded: {type(e).__name__}")

    def _count_late(self, late) -> None:
        """A model call that was given up on is usually billed all the same:
        count it when it finishes."""
        if late is None:
            return

        def done(value, error):
            if error is None and value is not None:
                self._count(value)
        late.when_done(done)

    def _moved(self, ctx, source: str, method: str) -> bool:
        """Has the person moved on while the model was thinking? True when
        another app is in front now, or (for an edit) the selection is gone or
        has changed. When the app does not answer, no (the caller treats a
        check that timed out or failed as "not sure": see ``_do_edit``)."""
        h = self.h
        now = self._front()
        if ctx.known and now.known and (now.bundle_id, now.name) != (ctx.bundle_id, ctx.name):
            return True
        if not source:
            return False
        # The selection again, the way it was first read: through Accessibility
        # where the app shares it, by copying where it did not.
        again = h.reread_selection(method)
        if again is None:
            return False
        if again.reason == text_context.NO_SELECTION:
            return True
        if getattr(again, "method", "") in (text_context.METHOD_AX, text_context.METHOD_COPY) and again.text:
            return transforms.normalise(again.text) != transforms.normalise(source)
        return False

    def _selection_appeared(self) -> bool:
        """Is something selected now, by a Copy (the way "nothing selected" was
        found)? A read that fails counts as yes: not knowing is a reason not to
        paste over what might be selected. (No reader at all is no.)"""
        if self.h.platform == "win32":
            # A second Ctrl+C is "stop the program" in a terminal that holds
            # nothing selected; the one that found nothing selected stands.
            return False
        again = self.h.reread_selection(text_context.METHOD_COPY)
        if again is None:
            return False
        if getattr(again, "reason", "") in ("", text_context.OK):
            return bool((getattr(again, "text", "") or "").strip())
        return again.reason != text_context.NO_SELECTION

    def _journal_buttons(self, entry: dict):
        if not entry or not entry.get("id"):
            return None
        return [{"label": "Show in Journal", "action": f"{SHOW_ACTION}:{entry['id']}", "kind": "primary"},
                {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}]

    def _save(self, action, run, instruction, reply, started, paste_label, *, before="", after="", query="",
              engine="", stored=None) -> dict:
        h = self.h
        entry: dict = {}
        try:
            stored = stored if stored is not None else h.settings()
            ctx = run.ctx
            fields = history_meta.take_fields(
                language=(stored or {}).get("language", "en"), paste=paste_label or None,
                app=getattr(ctx, "name", ""), app_bundle=getattr(ctx, "bundle_id", ""),
                category=getattr(ctx, "category", ""))
            entry = cm.history_entry(
                action, instruction, before=before, after=after, query=query, engine=engine,
                timestamp=h.now_iso(), provider=getattr(reply, "provider", ""),
                model=getattr(reply, "model", ""), took_s=h.monotonic() - started, fields=fields)
            if h.save_history(entry):
                h.announce_item(entry)
            else:
                entry = {}
        except Exception as e:      # noqa: BLE001 - the Journal never fails a command
            h.log(f"[command] not saved to the Journal: {type(e).__name__}")
            entry = {}
        return entry

    def _not_pasted(self, action, run, instruction, before, after, reply, started, sure: bool = True) -> str:
        """The person moved on (or it could not be checked in time): the result
        goes on the clipboard and in the Journal, and is not pasted anywhere."""
        h = self.h
        copied = False
        try:
            copied = bool(h.copy(after))
        except Exception:      # noqa: BLE001
            copied = False
        if copied:
            h.remember(after)
        label = history_meta.PASTE_COPIED if copied else history_meta.PASTE_NOT_PASTED
        entry = self._save(action, run, instruction, reply, started, label, before=before, after=after)
        h.log(f"[command] the person moved on: result {'copied' if copied else 'kept in the Journal'}")
        if copied:
            message = (um.transform_not_pasted_message(h.platform) if sure
                       else um.transform_not_sure_message(h.platform))
            self._say(RESULT, "info", message, self._journal_buttons(entry), timeout=DONE_S)
        else:
            self._say(FAILED, "warn", um.TRANSFORM_MOVED_NOT_COPIED, self._journal_buttons(entry), timeout=DONE_S)
        return pw.DONE

    def _copied(self, action, run, instruction, before, after, reply, started, selected: bool = False) -> str:
        """The result is only copied: auto-paste is off in Settings, or it is a
        draft and text is selected."""
        h = self.h
        copied = False
        try:
            copied = bool(h.copy(after))
        except Exception:      # noqa: BLE001
            copied = False
        if copied:
            h.remember(after)
        label = history_meta.PASTE_COPIED if copied else history_meta.PASTE_NOT_PASTED
        entry = self._save(action, run, instruction, reply, started, label, before=before, after=after)
        if copied:
            self._say(RESULT, "info", um.COMMAND_DRAFT_COPIED if selected else um.COMMAND_COPIED,
                      self._journal_buttons(entry), timeout=DONE_S)
        else:
            self._say(FAILED, "warn", um.TRANSFORM_MOVED_NOT_COPIED, self._journal_buttons(entry), timeout=DONE_S)
        return pw.DONE
