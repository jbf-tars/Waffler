"""Looking after the microphone while Waffler runs (S12).

One object ties together what the other mic modules decide and what the app
can do about it, so app.py only has to hand it the pipeline's own methods
(``Hooks``) and call it at four moments:

  ``on_devices(devices)``   every couple of seconds, from the device monitor
                            (src/audio_device_monitor.py), with the inputs the
                            system lists. A new microphone is asked about
                            (src/mic_hotplug.py), and a change that alters
                            which ranked microphone is in use makes the next
                            dictation open the right one.
  ``take_started(id)``      when the key goes down. Starts the check for the
                            take and, on a Mac, looks at the lid.
  ``tick(id)``              once a tick while recording (the level loop that
                            already runs for the pill and the longest take).
                            Opens the stream again after 5 s of silence, says
                            so at 15 s, ends the take if the microphone left.
  ``announce_lost(id)``     when that dictation is finished, so the notice
                            comes after the words are in, not over the pill.

It does the deciding in the modules it uses (src/mic_watch.py,
src/mic_hotplug.py, src/mic_ranking.py, src/clamshell.py), all of which have
tests of their own; what is left here is the order things happen in, and
that nothing here may raise into the hotkey, the level loop or the monitor.

Races, by design:
  - Ending a take because the microphone left and the person letting go of the
    key are two callers of ``on_hotkey_release``, which is atomic: whichever
    comes second finds no recording and does nothing, so a take is turned
    into text once. The notice is attached to the take (its press id), not to
    who ended it.
  - A re-open runs on its own thread; the watch is told when it ends, so the
    15 s message is never raised while it is still working. The recorder holds
    its stream lock for the re-open, and the stop that follows the key coming
    up waits its turn and returns what there is.
  - The prompt for a new microphone is never shown during a take or while one
    is being turned into text; it waits and is asked again on the next tick.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

try:
    import clamshell
    import mic_devices as md
    import mic_hotplug as hp
    import mic_watch as mw
    import notifier
    import user_messages as um
except ImportError:  # imported as src.mic_supervisor
    from src import clamshell
    from src import mic_devices as md
    from src import mic_hotplug as hp
    from src import mic_watch as mw
    from src import notifier
    from src import user_messages as um

# Toast kinds (src/notifier.py).
DETECTED, DISCONNECTED, CANT_HEAR, LID_CLOSED = (
    "mic_detected", "mic_disconnected", "mic_cant_hear", "lid_closed")
# Toast actions (app.py _on_toast_action).
USE_ACTION, LATER_ACTION = "mic_use", "mic_later"
CHOOSE_ACTION = "choose_microphone"
# What ``tick`` returns when the take has been ended because the microphone left.
LOST = mw.LOST

PROMPT_S = 20.0                 # how long the "use it?" tip stays up
ASK_AGAIN_LIMIT = 45            # polls (about 90 s) a new microphone may wait for a quiet moment
LID_WAIT_S = 30.0               # how long the lid tip waits for the dictation to be over
LID_POLL_S = 0.5


def _noop(*_a, **_k):
    return None


@dataclass
class Hooks:
    """Everything with a side effect, as plain functions. The ones with no
    default are the ones the supervisor cannot run without."""

    settings: Callable[[], dict]                         # settings.json, now
    recording: Callable[[], bool]                        # a take is being recorded
    busy: Callable[[], bool]                             # a dictation is being turned into text
    toast_allowed: Callable[[str], bool]                 # the notifier's yes or no for a kind
    show_toast: Callable[..., None]                      # (style, heading, body, buttons=, timeout=)
    health: Callable[[], dict]                           # AudioRecorder.health
    reopen: Callable[[], bool]                           # AudioRecorder.reopen_stream
    opened_device: Callable[[], Optional[str]]           # AudioRecorder.opened_device
    stop_take: Callable[[], None]                        # end the take as the key coming up would
    ranking: Callable[[], List[dict]]                    # the saved ranking now
    promote: Callable[[dict], Any]                       # put an entry at rank 1 and save it
    note_changed: Callable[[], None]                     # AudioRecorder.note_preference_changed
    memory: hp.DeviceMemory
    note_devices_changed: Callable[[], None] = _noop     # AudioRecorder.note_devices_changed
    hide_toast: Callable[..., None] = _noop              # (style)
    release_toast: Callable[[str], None] = _noop         # the notifier's slot, by kind
    rebuild_idle: Callable[[], None] = _noop             # open the monitoring stream again, if idle
    rebuild_after_take: Callable[[], None] = _noop       # ... or as soon as the take that is under way ends
    lid_closed: Callable[[], Optional[bool]] = clamshell.lid_closed
    log: Callable[[str], None] = _noop
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    spawn: Optional[Callable[[Callable], None]] = None   # run a function on a thread (default: a daemon one)
    platform: str = "darwin"


class MicSupervisor:
    def __init__(self, hooks: Hooks):
        self.h = hooks
        self.tracker = hp.HotplugTracker()
        self._lock = threading.RLock()
        self._tokens: Dict[str, md.Device] = {}
        self._attempts: Dict[str, int] = {}
        self._present: Optional[set] = None           # keys of the inputs listed at the last look
        self._best_key: Optional[str] = None
        self._best_known = False
        self._watch: Optional[mw.MicWatch] = None
        self._watch_press = 0
        self._lost_press = 0
        self._told_press = 0

    # ── plumbing ───────────────────────────────────────────────────────────

    def _spawn(self, fn: Callable, *args) -> None:
        if self.h.spawn is not None:
            self.h.spawn(lambda: fn(*args))
            return
        threading.Thread(target=fn, args=args, daemon=True, name="MicSupervisor").start()

    def _say(self, line: str) -> None:
        try:
            self.h.log(line)
        except Exception:
            pass

    def _quiet(self) -> bool:
        return not (self.h.recording() or self.h.busy())

    # ── devices come and go ────────────────────────────────────────────────

    def on_devices(self, devices: List[md.Device]) -> None:
        """The system's inputs as they are now. Never raises."""
        try:
            self._on_devices(list(devices or ()))
        except Exception as e:
            self._say(f"[mic] device check skipped ({type(e).__name__})")

    def _on_devices(self, devices: List[md.Device]) -> None:
        listed = md.distinct(devices)
        ranking = self.h.ranking()
        with self._lock:
            before, self._present = self._present, {d.key for d in listed}
        self._note_if_the_open_one_left(before)
        self._follow_ranking(listed, ranking)
        for dev in self.tracker.update(listed):
            self._consider(dev, ranking)

    def _note_if_the_open_one_left(self, before: Optional[set]) -> None:
        """The microphone the stream is on was listed at the last look and is
        not now: the stream is on a device that is gone. Told to the recorder,
        so the next press opens a new one, once. Only that: a phone's
        microphone, a meeting app's driver or AirPods coming and going while
        another microphone is in use change nothing, and a press must not be a
        cold start every time they do. Nothing is torn down here, at the moment
        the device is going: that is when CoreAudio is busiest."""
        name = self.h.opened_device()
        if not name or before is None:
            return
        was_there = md.norm_name(name) in before or any(md.same_name(name, k) for k in before)
        if was_there and self._device_present(name) is False:
            self._say("[mic] the microphone the stream is on left; the next dictation opens it again")
            self.h.note_devices_changed()

    def _follow_ranking(self, listed: List[md.Device], ranking: List[dict]) -> None:
        """If a change of devices changes which ranked microphone is the best
        one that is here, the stream that is open is stale: the next
        dictation opens the right one (and, if nothing is being recorded, it
        is opened again now so the Settings meter agrees)."""
        best = md.first_present(ranking, listed)
        key = best.key if best is not None else None
        with self._lock:
            changed = self._best_known and key != self._best_key
            self._best_key, self._best_known = key, True
        if changed:
            self._say("[mic] the best ranked microphone changed; the next dictation will use it")
            self._refresh()

    def _refresh(self) -> None:
        self.h.note_changed()
        if not self.h.recording():
            self._spawn(self.h.rebuild_idle)
        else:
            # Not forgotten: the stream is on the wrong microphone until it is opened again.
            self._spawn(self.h.rebuild_after_take)

    def preferences_changed(self) -> None:
        """The ranking was saved. Never raises."""
        try:
            with self._lock:
                self._best_known = False
            self._refresh()
        except Exception as e:
            self._say(f"[mic] preference change not applied ({type(e).__name__})")

    def _consider(self, dev: md.Device, ranking: List[dict]) -> None:
        ranked = any(md.same_name(dev.name, e.get("name")) for e in ranking)
        verdict = hp.decide(dev, ranked=ranked, decided=self.h.memory.decision(dev),
                            recording=not self._quiet())
        if verdict == hp.PROMPT:
            if self._ask(dev):
                self.tracker.resolve(dev)
                return
        elif verdict != hp.WAIT:
            self.tracker.resolve(dev)
            return
        n = self._attempts.get(dev.key, 0) + 1
        self._attempts[dev.key] = n
        if n >= ASK_AGAIN_LIMIT:
            self._say("[mic] a new microphone was not asked about: no quiet moment came")
            self.tracker.resolve(dev)
            self._attempts.pop(dev.key, None)

    def _ask(self, dev: md.Device) -> bool:
        """The tip: "<name> detected. Use it for dictation?" True when it was
        shown, or can never be (tips are off), so the caller stops asking."""
        if not notifier.tips_enabled(self.h.settings()):
            return True
        if not self.h.toast_allowed(DETECTED):
            return False
        token = hashlib.sha256(dev.key.encode("utf-8")).hexdigest()[:10]
        with self._lock:
            self._tokens[token] = dev
        # At most once for each device, whatever happens to the tip.
        self.h.memory.record(dev, hp.SEEN)
        heading, body = um.mic_detected_message(dev.name)
        buttons = [{"label": "Use it", "action": f"{USE_ACTION}:{token}", "kind": "primary"},
                   {"label": "Not now", "action": f"{LATER_ACTION}:{token}", "kind": "secondary"}]
        self._say("[mic] a new microphone was found; asking whether to use it")
        self._spawn(self._show, "info", heading, body, buttons, PROMPT_S)
        return True

    def answer(self, action) -> bool:
        """A button of the "use it?" tip. True if the action was one of ours."""
        verb, sep, token = str(action or "").partition(":")
        if not sep or verb not in (USE_ACTION, LATER_ACTION):
            return False
        try:
            with self._lock:
                dev = self._tokens.pop(token, None)
            if dev is None:
                return True                                  # ours, but stale: nothing to do
            if verb == USE_ACTION:
                self.h.memory.record(dev, hp.USE)
                self.h.promote(dev.entry())
                self._say("[mic] the new microphone was put first")
                self.preferences_changed()
            else:
                self.h.memory.record(dev, hp.NOT_NOW)
        except Exception as e:
            self._say(f"[mic] answer not applied ({type(e).__name__})")
        return True

    # ── a take ─────────────────────────────────────────────────────────────

    def take_started(self, press_id: int) -> None:
        """The key went down. Never raises, never waits."""
        try:
            with self._lock:
                self._watch = mw.MicWatch(self.h.clock)
                self._watch_press = press_id
            if self.h.platform == "darwin":
                self._spawn(self._look_at_the_lid, press_id)
        except Exception as e:
            self._say(f"[mic] take check not started ({type(e).__name__})")

    def _device_present(self, name: Optional[str]) -> Optional[bool]:
        """Whether the system still lists the microphone the stream is on.
        None when that cannot be said (no name, no list yet)."""
        with self._lock:
            present = self._present
        if not name or present is None:
            return None
        return md.norm_name(name) in present or any(
            md.same_name(name, k) for k in present)

    def tick(self, press_id: int) -> Optional[str]:
        """One look at the take (``press_id``). Returns the event it acted on
        (``mic_watch.LOST`` means the take has been ended and the caller's loop
        should stop), else None. Never raises."""
        try:
            with self._lock:
                watch = self._watch if self._watch_press == press_id else None
            if watch is None:
                return None
            health = self.h.health()
            event = watch.check(health, self._device_present(health.get("device")))
            if event == mw.REOPEN:
                self._say("[mic] nothing from the microphone for 5 s: opening the stream again")
                self._spawn(self._reopen, watch)
            elif event == mw.NOTIFY:
                self._cant_hear(press_id)
            elif event == mw.RECOVERED:
                self._say("[mic] sound is back")
                self.h.hide_toast("error")
                self.h.release_toast(CANT_HEAR)
            elif event == mw.LOST:
                self._lost(press_id)
            return event
        except Exception as e:
            self._say(f"[mic] take check skipped ({type(e).__name__})")
            return None

    def _reopen(self, watch: mw.MicWatch) -> None:
        try:
            ok = self.h.reopen()
            self._say(f"[mic] the stream was opened again ({'running' if ok else 'not running'})")
        except Exception as e:
            self._say(f"[mic] opening the stream again failed ({type(e).__name__})")
        finally:
            watch.reopen_finished()

    def _cant_hear(self, press_id: int) -> None:
        self._say("[mic] still nothing after 15 s")
        if not self.h.toast_allowed(CANT_HEAR):
            return
        with self._lock:
            self._told_press = press_id
        heading, body = um.MIC_CANT_HEAR
        buttons = [{"label": um.CHOOSE_MICROPHONE, "action": CHOOSE_ACTION, "kind": "primary"},
                   {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}]
        self._spawn(self._show, "error", heading, body, buttons, 12)

    def told_cant_hear(self, press_id: int) -> bool:
        """Whether the person was told, during this take, that the microphone
        could not be heard: the message after it ("We couldn't hear you")
        would only say it twice."""
        with self._lock:
            return self._told_press == press_id

    def _lost(self, press_id: int) -> None:
        self._say("[mic] the microphone left during the take: ending it with what there is")
        with self._lock:
            self._lost_press = press_id
        self.h.stop_take()

    def lost_in(self, press_id: int) -> bool:
        with self._lock:
            return self._lost_press == press_id

    def announce_lost(self, press_id: int, produced_text: bool = True) -> bool:
        """The dictation is finished: if its microphone left, say so, once.
        ``produced_text`` is whether the take became words. When it did not
        (nothing usable was recorded), the message that says so is already
        shown, and "Waffler used what it had recorded" would not be true.
        True when a notice was started. Never raises."""
        try:
            with self._lock:
                if self._lost_press != press_id:
                    return False
                self._lost_press = 0
            if not produced_text:
                return False
            if not self.h.toast_allowed(DISCONNECTED):
                return False
            heading, body = um.MIC_DISCONNECTED
            self._spawn(self._show, "warn", heading, body, None, 8)
            return True
        except Exception as e:
            self._say(f"[mic] notice not shown ({type(e).__name__})")
            return False

    # ── the lid ────────────────────────────────────────────────────────────

    def _look_at_the_lid(self, press_id: int) -> None:
        """On its own thread, from the key going down: is the lid shut while
        the built-in microphone is in use? If so, a tip once the dictation is
        over. A tip cannot be shown during one."""
        try:
            closed = self.h.lid_closed()
            if closed is not True:
                return
            # The stream is opened a moment after the key goes down.
            for _ in range(6):
                name = self.h.opened_device()
                if name:
                    break
                self.h.sleep(0.1)
            else:
                name = self.h.opened_device()
            if not clamshell.hint_due(closed, name, self.h.platform):
                return
            waited = 0.0
            while not self._quiet():
                if waited >= LID_WAIT_S:
                    return
                self.h.sleep(LID_POLL_S)
                waited += LID_POLL_S
            if not self.h.toast_allowed(LID_CLOSED):
                return
            heading, body = um.MIC_LID_CLOSED
            buttons = [{"label": um.CHOOSE_MICROPHONE, "action": CHOOSE_ACTION, "kind": "primary"}] \
                + notifier.tip_buttons(LID_CLOSED)
            self._show("info", heading, body, buttons, 10)
        except Exception as e:
            self._say(f"[mic] lid check skipped ({type(e).__name__})")

    # ── showing ────────────────────────────────────────────────────────────

    def _show(self, style, heading, body, buttons, timeout) -> None:
        try:
            self.h.show_toast(style, heading, body, buttons=buttons, timeout=timeout)
        except Exception as e:
            self._say(f"[mic] message not shown ({type(e).__name__})")

    # ── delete my data ─────────────────────────────────────────────────────

    def forget(self) -> None:
        """Delete all my data: what was decided about devices, and what is
        waiting to be asked."""
        with self._lock:
            self._tokens.clear()
            self._attempts.clear()
        self.tracker.reset()
        self.h.memory.forget()
