"""Running a transform: from the shortcut to the replaced text.

    shortcut or menu item
      -> not while a dictation is recording or being processed
      -> read the selected text (Accessibility, else a Copy, clipboard put back)
      -> ask the person's model for the rewrite (the clean-up's own provider chain)
      -> check the answer (src/transforms.py): no change, looks wrong, or good
      -> still the same app and selection? paste over it (the safe paste)
      -> a Journal entry with before and after, and a toast that says how to undo

It is one run of its own, on its own thread, watched like a dictation is
(src/pipeline_watchdog.py): every step that can block goes through
``run.call`` with a deadline, Esc and the pill's X cancel it until the paste
starts, and a run that stops making progress is given up on. It never runs
beside a dictation, in either direction. Nothing here blocks the hotkey's
thread, the menu's thread or the window's: ``start`` returns at once.

The text is never lost. Until the paste starts the person's text is exactly
where it was. If the paste fails the rewrite is on the clipboard (the safe
paste leaves it there) and in the Journal. If the person has moved to another
app or changed the selection while the model was thinking, nothing is pasted:
the rewrite goes on the clipboard and in the Journal instead.

This module knows nothing of the window, the overlay or the clipboard itself.
app.py hands it a ``Hooks`` made of the pipeline's own methods, so the tests
run the whole flow with fakes.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import history_meta
import pasteboard
import pipeline_watchdog as pw
import text_context
import transforms
import user_messages as um

# The watchdog stage a step runs in decides whether Cancel reaches it.
# Reading the selection and asking the model can be cancelled; the paste
# cannot (the words may already be going in).
READ_DEADLINE_S = 6.0           # the read itself gives up after about 1.5 s, the copy needs a little more
ASK_DEADLINE_S = 30.0           # the whole model call, every provider tried
ASK_MARGIN_S = 2.0              # the provider chain stops this long before the watchdog does
CHECK_DEADLINE_S = 3.0          # "is the selection still there?"
PASTE_DEADLINE_S = pw.PASTE_DEADLINE_S

# Toast kinds (src/notifier.py): where each message is shown and how often.
RESULT, BUSY, FAILED = "transform_result", "transform_busy", "transform_failed"
# How long a neutral message stays up.
NOTICE_S = 6.0
DONE_S = 8.0

STARTED, ALREADY_RUNNING, DICTATING, OFF = "started", "busy", "dictating", "off"
SHOW_ACTION = "show_transform"          # the toast's "Show in Journal" (app.py _on_toast_action)
SETTINGS_ACTION = "open_ax_settings"


def _noop(*_a, **_k):
    return None


@dataclass
class Hooks:
    """Everything with a side effect, as plain functions. The ones with no
    default are the ones a transform cannot run without."""

    settings: Callable[[], dict]                                # settings.json, now
    dictation_busy: Callable[[], bool]                          # recording or processing
    read_selection: Callable[[], Any]                           # -> text_context.SelectionResult (blocking)
    chat: Callable[..., Any]                                    # styler._chat(system, user, ...) -> ChatReply
    paste: Callable[..., Any]                                   # (text, window, restore, ticket) -> PasteOutcome
    settle_paste: Callable[[Any, Any], tuple]                   # (CallResult, ticket) -> (on_clipboard, pasted)
    copy: Callable[[str], bool]
    save_history: Callable[[dict], bool]                        # stamps the entry's id
    say: Callable[..., None]                                    # (kind, style, heading, body, buttons=None, timeout=None)
    front_app: Callable[[], Any] = lambda: text_context.UNKNOWN
    window: Callable[[], Any] = lambda: None                    # Windows: the target window
    reread_selection: Callable[[str], Any] = lambda method: None    # the selection again, read the way it was first
    refocus: Callable[[], None] = _noop                         # Windows tray: back to the last app
    dashes: Optional[Callable[[str], str]] = None               # the styler's own helpers
    profanity: Optional[Callable[[str, str], str]] = None
    remember: Callable[[str], None] = _noop                     # "Copy again" must copy the rewrite
    record_usage: Callable[[Any], None] = _noop
    announce_item: Callable[[dict], None] = _noop               # the Journal card appears
    ui_begin: Callable[[Any], None] = _noop                     # the pill, the status, Esc
    ui_tick: Callable[[Any, int], None] = _noop
    ui_end: Callable[[Any, str], None] = _noop
    log: Callable[[str], None] = _noop
    now_iso: Callable[[], str] = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")
    monotonic: Callable[[], float] = time.monotonic
    platform: str = "darwin"
    spawn: Callable[[Callable[[], None]], None] = field(
        default=lambda fn: threading.Thread(target=fn, daemon=True, name="Transform").start())


class TransformRunner:
    def __init__(self, hooks: Hooks, *, read_deadline_s: float = READ_DEADLINE_S,
                 ask_deadline_s: float = ASK_DEADLINE_S, paste_deadline_s: float = PASTE_DEADLINE_S,
                 watchdog: Optional[pw.PipelineWatchdog] = None):
        self.h = hooks
        self.read_deadline_s = read_deadline_s
        self.ask_deadline_s = ask_deadline_s
        self.paste_deadline_s = paste_deadline_s
        self._lock = threading.Lock()
        self._active = False
        self._cancel_early = False
        self._run: Optional[pw.DictationRun] = None
        self._ids = itertools.count(1)
        self._watchdog = watchdog or pw.PipelineWatchdog(
            on_begin=lambda run: hooks.ui_begin(run),
            on_working=lambda run, seconds: hooks.ui_tick(run, seconds),
            on_finished=lambda run, outcome: hooks.ui_end(run, outcome),
            log=hooks.log)

    # ── asked by the rest of the app ──────────────────────────────────────

    def busy(self) -> bool:
        """A transform is running (so a dictation must wait)."""
        return self._active

    def start(self, origin: str = "shortcut") -> str:
        """Begin a transform if one may run now, and return at once. Called
        from the shortcut's thread or the menu's. ``origin`` is "shortcut" or
        "menu" (on Windows the menu leaves a window of ours in front, so the
        last app is brought back first)."""
        h = self.h
        try:
            enabled = transforms.settings(h.settings())["enabled"]
        except Exception as e:      # noqa: BLE001
            h.log(f"[transform] settings unreadable: {type(e).__name__}")
            enabled = False
        if not enabled:
            h.log("[transform] asked while Transforms is off: ignored")
            return OFF
        refuse = None
        with self._lock:
            if self._active:
                refuse = (ALREADY_RUNNING, um.TRANSFORM_BUSY_ALREADY)
            elif self._dictation_busy():
                refuse = (DICTATING, um.TRANSFORM_BUSY_DICTATING)
            else:
                self._active = True
                self._cancel_early = False
        if refuse is not None:
            self._say_busy(refuse[1])
            return refuse[0]
        try:
            h.spawn(lambda: self._thread(origin))
        except Exception as e:      # noqa: BLE001 - out of threads
            self._active = False
            h.log(f"[transform] could not start: {type(e).__name__}")
            raise
        return STARTED

    def cancel(self) -> bool:
        """Esc or the pill's X. True when a transform is running (the key is
        then ours); the run stops at once unless the paste has begun."""
        with self._lock:
            run = self._run
            active = self._active
            if active and run is None:
                self._cancel_early = True       # the run is just starting
        if run is not None:
            if run.decide(pw.CANCEL):
                self.h.log("[transform] cancelled by the user")
            else:
                self.h.log("[transform] cancel came too late: the paste has begun")
        return active

    def refuse_dictation(self) -> None:
        """The dictation hotkey went down while a transform runs."""
        self._say_busy(um.TRANSFORM_BLOCKS_DICTATION)

    # ── the run ───────────────────────────────────────────────────────────

    def _dictation_busy(self) -> bool:
        try:
            return bool(self.h.dictation_busy())
        except Exception:      # noqa: BLE001 - if it cannot be told, do not guess "free"
            return True

    def _say_busy(self, message) -> None:
        self._say(BUSY, "info", message, timeout=NOTICE_S)

    def _say(self, kind, style, message, buttons=None, timeout=None) -> None:
        heading, body = message
        try:
            self.h.say(kind, style, heading, body, buttons, timeout)
        except Exception as e:      # noqa: BLE001 - a toast must never end a run badly
            self.h.log(f"[transform] message not shown: {type(e).__name__}")

    def _thread(self, origin: str) -> None:
        run = None
        outcome = pw.ERROR
        try:
            # Where the person is, noted before the pill appears (as a
            # dictation notes it). From the menu it is the last app, once
            # it has been brought back (_work).
            seen = None if origin == "menu" else (self._front(), self.h.window())
            run = self._watchdog.begin(next(self._ids))
            with self._lock:
                self._run = run
                early = self._cancel_early
            if early:
                run.decide(pw.CANCEL)
            outcome = self._work(run, origin, seen)
        except Exception as e:      # noqa: BLE001 - one line, never the text
            self.h.log(f"[transform] failed: {type(e).__name__}")
            self._say(FAILED, "warn", ("Couldn't rewrite",
                                       "Something went wrong. Your text is unchanged."), timeout=NOTICE_S)
        finally:
            # The pill and the status are put back first, while this run still
            # counts as running: a new one cannot start (and show its pill)
            # just before the old one's end hides it.
            try:
                if run is not None:
                    self._watchdog.end(run, outcome)
            finally:
                with self._lock:
                    self._run = None
                    self._active = False

    def _work(self, run: pw.DictationRun, origin: str, seen=None) -> str:
        h = self.h
        stored = h.settings()
        spec = transforms.spec_for(stored)
        started = h.monotonic()

        if seen is None:
            if origin == "menu":
                run.call(h.refocus, stage=pw.PREPARING, deadline=2.0)
            seen = (self._front(), h.window())
        ctx, window = seen

        # 1. The selection.
        got = run.call(h.read_selection, stage=pw.PREPARING, deadline=self.read_deadline_s)
        if got.status == pw.CANCEL:
            return self._cancelled()
        if not got.ok or got.value is None:
            self._say(FAILED, "warn", um.transform_read_message("timed_out"), timeout=NOTICE_S)
            return pw.ERROR
        selection = got.value
        text = selection.text or ""
        problem = transforms.selection_problem(text)
        if problem or selection.reason not in ("", text_context.OK):
            return self._refuse_selection(selection, problem)

        # 2. The rewrite.
        request = transforms.build_request(spec, text)
        deadline_at = h.monotonic() + max(1.0, self.ask_deadline_s - ASK_MARGIN_S)

        def ask():
            return h.chat(request.system, request.user, max_tokens=request.max_tokens,
                          temperature=request.temperature, deadline=deadline_at,
                          purpose="transform")

        asked = run.call(ask, stage=pw.STYLING, deadline=self.ask_deadline_s)
        if asked.status != "ok":
            return self._ask_failed(asked)
        reply = asked.value
        self._count(reply)

        # 3. Is it worth using?
        result = transforms.finish(text, reply.text, spec,
                                   finish_reason=getattr(reply, "finish_reason", None),
                                   dashes=h.dashes, profanity=h.profanity)
        if result.status != transforms.REPLACE:
            return self._not_replaced(result)
        if run.cancelled:
            return self._cancelled()

        # 4. Still the same place?
        after = result.text
        moved = run.call(lambda: self._moved(ctx, text, selection), stage=pw.STYLING,
                         deadline=CHECK_DEADLINE_S)
        if moved.status == pw.CANCEL:
            return self._cancelled()
        if not moved.ok or moved.value:
            # Moved on, or not able to tell in time (the app did not answer):
            # a paste into the wrong place costs more than a Cmd+V.
            return self._not_pasted(spec, ctx, text, after, reply, started, sure=moved.ok)

        # 5. Put it in.
        if stored.get("auto_paste", True) is False:
            return self._copied(spec, ctx, text, after, reply, started)
        run.enter(pw.PASTING)           # from here on Cancel is too late, and says so
        if run.cancelled:
            return self._cancelled()
        ticket = pasteboard.PasteTicket()
        restore = pasteboard.restore_after_paste(stored)
        pasted = run.call(h.paste, after, window, restore, ticket,
                          stage=pw.PASTING, deadline=self.paste_deadline_s)
        try:
            _on_clipboard, went_in = h.settle_paste(pasted, ticket)
        except Exception as e:      # noqa: BLE001 - what became of the paste is unknown, so say it went in
            h.log(f"[transform] paste outcome not read: {type(e).__name__}")
            went_in = True
        label = history_meta.paste_label(pasted, went_in)
        if not went_in:
            h.remember(after)               # so the failed-paste message's "Copy again" copies the rewrite
        entry = self._save(spec, ctx, text, after, reply, started, label, stored)
        if went_in:
            buttons = self._journal_buttons(entry)
            self._say(RESULT, "info", um.transform_done_message(spec.name, h.platform),
                      buttons, timeout=DONE_S)
        return pw.DONE if went_in else pw.ERROR

    # ── the parts ─────────────────────────────────────────────────────────

    def _front(self):
        try:
            return self.h.front_app()
        except Exception:      # noqa: BLE001
            return text_context.UNKNOWN

    def _refuse_selection(self, selection, problem) -> str:
        reason = selection.reason
        if problem == transforms.TOO_LONG or reason == text_context.TOO_LARGE:
            self._say(RESULT, "info", um.TRANSFORM_TOO_LONG, timeout=NOTICE_S)
        elif problem == transforms.EMPTY and reason in ("", text_context.OK, text_context.NO_SELECTION):
            self._say(RESULT, "info", um.TRANSFORM_NO_SELECTION, timeout=NOTICE_S)
        elif reason == text_context.ACCESSIBILITY_OFF:
            buttons = [{"label": "Open Settings", "action": SETTINGS_ACTION, "kind": "primary"},
                       {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}]
            self._say(FAILED, "warn", um.transform_read_message(reason), buttons, timeout=12)
        else:
            self._say(FAILED, "warn", um.transform_read_message(reason), timeout=NOTICE_S)
        self.h.log(f"[transform] nothing to rewrite ({reason or problem})")
        return pw.NOTHING

    def _ask_failed(self, asked) -> str:
        h = self.h
        if asked.status == pw.CANCEL:
            self._count_late(asked.late)
            return self._cancelled()
        if asked.status == pw.DEADLINE:
            self._count_late(asked.late)
            h.log(f"[transform] the model did not answer in {self.ask_deadline_s:.0f}s")
            self._say(FAILED, "warn", um.TRANSFORM_TOO_SLOW, timeout=NOTICE_S)
            return pw.ERROR
        error = asked.error
        reason = getattr(error, "reason", None)
        if reason:
            h.log(f"[transform] no provider answered: {str(reason)[:120]}")
            self._say(FAILED, "warn", um.transform_failed_message(reason), timeout=NOTICE_S)
        else:
            h.log(f"[transform] the model call failed: {type(error).__name__}")
            self._say(FAILED, "warn", um.transform_failed_message(""), timeout=NOTICE_S)
        return pw.ERROR

    def _not_replaced(self, result) -> str:
        status = result.status
        self.h.log(f"[transform] not replaced ({status}; {result.words_in} words in, "
                   f"{result.words_out} out)")
        if status == transforms.NO_CHANGE:
            self._say(RESULT, "info", um.TRANSFORM_NO_CHANGE, timeout=NOTICE_S)
            return pw.NOTHING
        message = {transforms.TRUNCATED: um.TRANSFORM_CUT_SHORT,
                   transforms.UNRELATED: um.TRANSFORM_UNRELATED,
                   transforms.EMPTY_ANSWER: um.TRANSFORM_NO_ANSWER}.get(status, um.TRANSFORM_BAD_LENGTH)
        self._say(FAILED, "warn", message, timeout=NOTICE_S)
        return pw.NOTHING

    def _cancelled(self) -> str:
        self._say(RESULT, "info", um.TRANSFORM_CANCELLED, timeout=NOTICE_S)
        return pw.CANCELLED

    def _count(self, reply) -> None:
        try:
            self.h.record_usage(reply)
        except Exception as e:      # noqa: BLE001 - a usage row never fails a transform
            self.h.log(f"[transform] usage not recorded: {type(e).__name__}")

    def _count_late(self, late) -> None:
        """A model call that was given up on is usually billed all the same:
        count it when it finishes."""
        if late is None:
            return

        def done(value, error):
            if error is None and value is not None:
                self._count(value)
        late.when_done(done)

    def _moved(self, ctx, source: str, selection) -> bool:
        """Has the person moved on while the model was thinking? True when
        another app is in front now, or the selection is gone or has changed.
        When the app does not answer, no (the caller treats a check that
        timed out or failed as "not sure": see ``_work``)."""
        h = self.h
        now = self._front()
        if ctx.known and now.known and (now.bundle_id, now.name) != (ctx.bundle_id, ctx.name):
            return True
        # The selection again, the way it was first read: through Accessibility
        # where the app shares it, by copying where it did not (browsers and
        # most Electron apps, where a tab switch or a keystroke would not show).
        again = h.reread_selection(selection.method)
        if again is None:
            return False
        if again.reason == text_context.NO_SELECTION:
            return True
        if getattr(again, "method", "") in (text_context.METHOD_AX, text_context.METHOD_COPY) and again.text:
            return transforms.normalise(again.text) != transforms.normalise(source)
        return False

    def _journal_buttons(self, entry: dict):
        if not entry or not entry.get("id"):
            return None
        return [{"label": "Show in Journal", "action": f"{SHOW_ACTION}:{entry['id']}", "kind": "primary"},
                {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}]

    def _save(self, spec, ctx, before, after, reply, started, paste_label, stored) -> dict:
        h = self.h
        entry: dict = {}
        try:
            fields = history_meta.take_fields(
                language=(stored or {}).get("language", "en"), paste=paste_label,
                app=getattr(ctx, "name", ""), app_bundle=getattr(ctx, "bundle_id", ""),
                category=getattr(ctx, "category", ""))
            entry = transforms.history_entry(
                spec, before, after, timestamp=h.now_iso(), provider=getattr(reply, "provider", ""),
                model=getattr(reply, "model", ""), took_s=h.monotonic() - started, fields=fields)
            if h.save_history(entry):
                h.announce_item(entry)
            else:
                entry = {}
        except Exception as e:      # noqa: BLE001 - the Journal never fails a transform
            h.log(f"[transform] not saved to the Journal: {type(e).__name__}")
            entry = {}
        return entry

    def _not_pasted(self, spec, ctx, before, after, reply, started, sure: bool = True) -> str:
        """The person moved on (or it could not be checked in time): the
        rewrite goes on the clipboard and in the Journal, and is not pasted
        anywhere."""
        h = self.h
        copied = False
        try:
            copied = bool(h.copy(after))
        except Exception:      # noqa: BLE001
            copied = False
        if copied:
            h.remember(after)
        label = history_meta.PASTE_COPIED if copied else history_meta.PASTE_NOT_PASTED
        entry = self._save(spec, ctx, before, after, reply, started, label, h.settings())
        h.log(f"[transform] the person moved on: result {'copied' if copied else 'kept in the Journal'}")
        if copied:
            message = (um.transform_not_pasted_message(h.platform) if sure
                       else um.transform_not_sure_message(h.platform))
            self._say(RESULT, "info", message, self._journal_buttons(entry), timeout=DONE_S)
        else:
            self._say(FAILED, "warn", um.TRANSFORM_MOVED_NOT_COPIED,
                      self._journal_buttons(entry), timeout=DONE_S)
        return pw.DONE

    def _copied(self, spec, ctx, before, after, reply, started) -> str:
        """Auto-paste is off in Settings: the rewrite is only copied."""
        h = self.h
        copied = bool(h.copy(after))
        if copied:
            h.remember(after)
        label = history_meta.PASTE_COPIED if copied else history_meta.PASTE_NOT_PASTED
        entry = self._save(spec, ctx, before, after, reply, started, label, h.settings())
        if copied:
            self._say(RESULT, "info", um.TRANSFORM_COPIED, self._journal_buttons(entry), timeout=DONE_S)
        else:
            self._say(FAILED, "warn", um.TRANSFORM_MOVED_NOT_COPIED, self._journal_buttons(entry), timeout=DONE_S)
        return pw.DONE


# ── Windows: the tray menu leaves a window of ours in front ─────────────────
# Clicking the tray icon makes one of Waffler's own hidden windows the
# foreground window, so the selection (and the Copy that reads it) would be
# in the wrong place. The last app the person was in is the topmost ordinary
# window that is not ours. ``pick_window`` decides from a list the caller reads
# in Z order, topmost first; ``refocus_last_app`` does the reading and the
# switch, and can only be run on Windows.

SHELL_CLASSES = frozenset({
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
    "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
    "XamlExplorerHostIslandWindow", "Windows.UI.Core.CoreWindow",
})


def pick_window(windows, skip_pids=()):
    """The handle of the window to go back to, or None. ``windows`` is a list
    of dicts with ``hwnd``, ``pid``, ``cls``, ``title``, ``visible``,
    ``tool`` (a tool window) and ``cloaked`` (on another virtual desktop,
    or hidden by the shell), topmost first."""
    skip = set(skip_pids)
    for w in windows:
        if w.get("pid") in skip or not w.get("visible") or w.get("tool") or w.get("cloaked"):
            continue
        if not str(w.get("title") or "").strip() or w.get("cls") in SHELL_CLASSES:
            continue
        return w.get("hwnd")
    return None


def refocus_last_app(skip_pids=(), log=_noop) -> bool:
    """Windows only: bring the last app the person was in back to the front,
    and give it a moment. False when there is none or this is not Windows."""
    import sys
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes as wt
        user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
        found = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def each(hwnd, _lparam):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, cls, 128)
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cloaked = ctypes.c_int(0)
            dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))   # DWMWA_CLOAKED
            ex_style = user32.GetWindowLongW(hwnd, -20)                                          # GWL_EXSTYLE
            found.append({"hwnd": hwnd, "pid": pid.value, "cls": cls.value, "title": buf.value,
                          "visible": bool(user32.IsWindowVisible(hwnd)),
                          "tool": bool(ex_style & 0x80), "cloaked": bool(cloaked.value)})
            return True

        user32.EnumWindows(each, 0)           # Z order, topmost first
        hwnd = pick_window(found, set(skip_pids) | {os_getpid()})
        if not hwnd:
            return False
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.25)                      # let the window take the focus
        return True
    except Exception as e:      # noqa: BLE001
        log(f"[transform] could not go back to the last app: {type(e).__name__}")
        return False


def os_getpid() -> int:
    import os
    return os.getpid()
