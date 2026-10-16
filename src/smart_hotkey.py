"""
Waffler Smart Hotkey

Supports ANY hotkey combination (Fn, Cmd+Shift+Space, Option+Space, etc.)

Hold hotkey                → Start recording (push-to-talk)
Release                    → Stop recording
Press Space while holding  → Sticky mode (hands-free recording)
Press hotkey again         → Stop sticky mode
Press Esc anywhere         → Universal sticky-mode cancel

Architecture (post v3.14.13)
----------------------------
A single ``MacEventTap`` runs ONE HID-level ``CGEventTap`` on ONE
``CFRunLoopRun()`` thread. All key detection (hotkey / Space-trigger / Esc-
cancel) is dispatched to lightweight handler objects from that single
callback.

Previously this class composed two or three independent ``MacHotkeyMonitor``
/ ``FnKeyMonitor`` instances, each with its own HID-level tap and its own
``CFRunLoopRun()`` thread. When the C callbacks for two of those taps fired
near-simultaneously (e.g. paste + key release at the end of a recording),
PyObjC's bridge state raced between the two C threads and macOS sent
SIGSEGV. That's the v3.14.x segfault. The fix is collapsing everything
into one tap, here.

Wizard / IPC backward-compat: ``app.py``'s ``get_fn_key_state`` digs into
``self.hotkey_listener._monitor._fn_pressed`` (Fn case) or
``self.hotkey_listener._monitor._hotkey_active`` (non-Fn case). We preserve
that surface by exposing ``_monitor`` as the relevant handler (which carries
both legacy attributes via property aliases).
"""

import itertools
import threading
import time
import mac_hotkey_monitor as _mhm
from mac_hotkey_monitor import (
    MacEventTap,
    FnHandler,
    SpaceHandler,
    GenericHotkeyHandler,
)
import action_hotkeys
import double_tap

try:
    from log_util import log as _diag_log
except ImportError:
    from src.log_util import log as _diag_log

# v3.14.30 diagnostic: unique id per SmartHotkeyListener so the log
# distinguishes multiple listeners if more than one is alive at once.
_LISTENER_ID_GEN = itertools.count(1)


class SmartHotkeyListener:

    def __init__(self, on_press, on_release, on_cancel=None, keys=None, on_lock=None,
                 cancel_key=None, double_tap=False, clock=None):
        """
        Args:
            on_press: Callback when recording should start
            on_release: Callback when recording should stop
            on_cancel: Optional — callback when Esc is pressed mid-recording.
                Receives no args. Used by app.py's pipeline to discard the
                recording WITHOUT firing on_release (which would transcribe
                + paste). Added v3.14.37 alongside Esc-cancel hotkey.
            keys: List of key names (default: ["fn"])
                 Examples: ["fn"], ["cmd", "shift", "space"], ["option", "space"]
            on_lock: Optional — callback when Space locks the recording on
                (hands-free). Used for the lock sound cue and to keep the
                "Don't tap, hold" hint away from takes that locked.
            cancel_key: Optional — the key that cancels, by name. Esc unless
                the person chose an F key in Settings (src/action_hotkeys.py
                CANCEL_KEYS); anything else is Esc. The only thing it changes
                is which key the cancel handler below listens for.
            double_tap: Optional — True to lock a take on when the hotkey is
                tapped twice quickly (src/double_tap.py); Settings, Shortcuts.
                Off, nothing below asks the module anything.
            clock: Optional — the monotonic clock double-tap reads (a test
                gives its own).
        """
        self._on_press_cb = on_press
        self._on_release_cb = on_release
        self._on_cancel_cb = on_cancel
        self._on_lock_cb = on_lock
        self._keys = keys or ["fn"]

        self._hotkey_held = False   # Hotkey currently down
        self._sticky = False        # Locked-on (toggle) mode active
        self._recording = False     # Are we recording right now?
        # True while the pipeline's "still working" offer is on screen for a
        # dictation being processed. Esc then stops that wait too
        # (set_processing, called by the pipeline).
        self._processing = False

        # Double-tap to go hands-free (src/double_tap.py): None while it is
        # off, and then no line of the state machine below behaves any
        # differently. ``_locked_by_double_tap`` is a take it locked: a press
        # ends that one even if the setting has since been switched off.
        self._double_tap = None
        self._locked_by_double_tap = False
        self._clock = clock

        # v3.14.30 diagnostic: each listener gets an id so the log says
        # "L01 fired press" / "L02 fired press" — instantly answers the
        # "one listener fanning out vs N listeners firing once" question.
        self._id = f"L{next(_LISTENER_ID_GEN):02d}"
        _diag_log(
            f"[HOTKEY/{self._id}] SmartHotkeyListener created keys={self._keys} "
            f"thread={threading.current_thread().name}"
        )

        # ── ONE event tap, multiple handlers ──────────────────────────
        self._tap = MacEventTap()

        # Handlers will be attached based on which hotkey we're tracking.
        # We keep references so the wizard can poll their state.
        self._fn_handler = None       # set if Fn is the hotkey
        self._hotkey_handler = None   # set if hotkey is anything other than Fn

        if self._keys == ["fn"]:
            print("[HOTKEY] Using single-tap Fn dispatch (FnHandler + SpaceHandler)")
            self._fn_handler = FnHandler(
                on_press=self._on_hotkey_press,
                on_release=self._on_hotkey_release,
            )
            self._tap.add_handler(self._fn_handler)
            # Space, with Fn-conditional suppression handled inside.
            self._tap.add_handler(
                SpaceHandler(
                    on_press=self._on_space_press,
                    fn_handler=self._fn_handler,
                )
            )
        else:
            print(f"[HOTKEY] Using single-tap generic dispatch for keys: {self._keys}")
            self._hotkey_handler = GenericHotkeyHandler(
                keys=self._keys,
                on_press=self._on_hotkey_press,
                on_release=self._on_hotkey_release,
                suppress=True,
            )
            self._tap.add_handler(self._hotkey_handler)
            # Space as a sticky trigger — never suppress so the user can
            # still type spaces.
            self._tap.add_handler(
                GenericHotkeyHandler(
                    keys=["space"],
                    on_press=self._on_space_press,
                    on_release=lambda: None,
                    suppress=False,
                )
            )

        # Esc cancel — universal escape hatch for sticky mode. Never
        # suppress; we just want to peek at Esc presses while it still
        # reaches other apps (dialog dismiss, vim, etc). A cancel key chosen
        # in Settings (an F key) takes Esc's place with the same handler.
        cancel = action_hotkeys.cancel_key_of({"cancel_key": cancel_key})
        if cancel == action_hotkeys.CANCEL_DEFAULT:
            self._esc_handler = GenericHotkeyHandler(
                keys=["esc"],
                on_press=self._on_esc_press,
                on_release=lambda: None,
                suppress=False,
            )
        else:
            self._esc_handler = GenericHotkeyHandler(
                keys=[cancel],
                on_press=self._on_esc_press,
                on_release=lambda: None,
                suppress=False,
                key_codes={k: v for k, v in action_hotkeys.MAC_KEYCODES.items() if k == cancel},
            )
        self._tap.add_handler(self._esc_handler)

        # Backward-compat surface for app.py's get_fn_key_state — see
        # docstring at top of this file. Whichever of the two handlers is
        # active for this hotkey carries the relevant `_fn_pressed` /
        # `_hotkey_active` attribute (as a property alias in mac_hotkey_monitor).
        self._monitor = self._fn_handler if self._fn_handler else self._hotkey_handler

        # Last, because the gate needs to know whether the hotkey is Fn.
        if double_tap:
            self.set_double_tap(True)

        print(f"[HOTKEY] SmartHotkeyListener initialized with keys: {self._keys}")

    # ── Key events ────────────────────────────────────────────────────

    def _on_hotkey_press(self):
        _diag_log(
            f"[HOTKEY/{self._id}] _on_hotkey_press entered "
            f"sticky={self._sticky} recording={self._recording}"
        )
        if not self._recording:
            self._hotkey_held = True
            self._recording = True
            dt = self._double_tap       # read once: Settings can switch it off between two reads
            if dt is not None:
                dt.press()
            _diag_log(f"[HOTKEY/{self._id}] → starting push-to-talk (fire_press)")
            self._fire_press()
        elif self._sticky and self._press_ends_hands_free():
            # Double-tap is on (or this take was locked by it): a press of the
            # hotkey ends a hands-free take, as the hotkey page says. Its
            # release, and the key bouncing, find nothing recording.
            self._hotkey_held = True
            self._sticky = False
            self._recording = False
            self._locked_by_double_tap = False
            self._double_tap_forget()
            _diag_log(f"[HOTKEY/{self._id}] → press ends the hands-free take (fire_release)")
            self._fire_visual_feedback("sticky_off_fn")
            self._fire_release()
        else:
            # Hotkey pressed while already recording — keep held flag in
            # sync so Fn+Space detection still works in sticky mode.
            self._hotkey_held = True
            _diag_log(
                f"[HOTKEY/{self._id}] → press IGNORED (already recording); "
                f"hotkey_held remains true"
            )

    def _on_hotkey_release(self):
        _diag_log(
            f"[HOTKEY/{self._id}] _on_hotkey_release entered "
            f"sticky={self._sticky} recording={self._recording}"
        )
        self._hotkey_held = False
        if self._recording and not self._sticky:
            dt = self._double_tap
            if dt is not None and dt.release():
                # The second tap of two: keep the take recording and lock it
                # on, as Space does (the lock cue, the hands-free pill).
                self._sticky = True
                self._locked_by_double_tap = True
                _diag_log(f"[HOTKEY/{self._id}] → double tap: hands-free ON (fire_lock)")
                self._fire_visual_feedback("sticky_on")
                self._fire_lock()
                return
            self._recording = False
            _diag_log(f"[HOTKEY/{self._id}] → stopping push-to-talk (fire_release)")
            self._fire_release()
        else:
            _diag_log(
                f"[HOTKEY/{self._id}] → release IGNORED "
                f"(recording={self._recording} sticky={self._sticky})"
            )

    def _on_space_press(self):
        print(
            f"[HOTKEY] Space pressed | hotkey_held={self._hotkey_held} "
            f"recording={self._recording} sticky={self._sticky}"
        )
        if self._hotkey_held and self._recording and not self._sticky:
            self._sticky = True
            dt = getattr(self, "_double_tap", None)     # a Space lock forgets any taps before it
            if dt is not None:
                dt.reset()
            print("📌 Hotkey+Space → Sticky mode ON! Press hotkey+Space again to stop.")
            self._fire_visual_feedback("sticky_on")
            self._fire_lock()
        elif self._hotkey_held and self._recording and self._sticky:
            self._sticky = False
            self._recording = False
            self._locked_by_double_tap = False
            dt = getattr(self, "_double_tap", None)
            if dt is not None:
                dt.reset()
            print("🛑 Hotkey+Space → Sticky mode OFF")
            self._fire_visual_feedback("sticky_off_fn")
            self._fire_release()

    def _on_esc_press(self):
        """Esc cancels an active recording.

        v3.14.37 — Esc is now the cancel hotkey: works in BOTH push-to-talk
        hold mode and sticky mode, and discards the recording without
        firing the normal release pipeline (no transcription, no paste).
        When the user isn't recording, Esc is a no-op so it stays
        available for dialogs, vim, etc.

        The cancel path goes through `_fire_cancel` → the pipeline's
        `_on_overlay_cancel`, which stops audio, hides the overlay,
        resets sticky state, clears the clipboard, and aborts any
        in-flight transcription thread. Same end-state as clicking the
        X on the overlay.
        """
        if not self._recording:
            if self._processing:
                # The pipeline arms this only while its "still working" offer
                # is on screen: Esc then stops the wait and the recording is
                # kept in the Journal. Straight after letting go Esc is left
                # alone (it closes the emoji picker Fn can open, for one).
                # Esc still reaches the app in front, as it always has.
                _diag_log(f"[HOTKEY/{self._id}] Esc → stop waiting for the dictation being processed")
                self._fire_cancel()
            return  # No-op outside recording so Esc still works in dialogs/vim
        _diag_log(
            f"[HOTKEY/{self._id}] Esc → CANCEL recording "
            f"(was sticky={self._sticky})"
        )
        self._sticky = False
        self._recording = False
        self._hotkey_held = False
        self._locked_by_double_tap = False
        self._double_tap_forget()
        self._fire_visual_feedback("sticky_off_esc")
        self._fire_cancel()

    # ── Double-tap to go hands-free (src/double_tap.py) ───────────────

    def set_double_tap(self, enabled: bool):
        """Switch double-tap on or off while the listener runs (Settings,
        Shortcuts). A hands-free take it has already locked is left alone
        and can still be ended by a press. Nothing else changes."""
        if enabled:
            if self._double_tap is None:
                # The Fn key says its release 150 ms late (hold-quiet timer),
                # so a tap is measured from when the finger left the key.
                lag = getattr(_mhm, "_FN_RELEASE_HOLD_S", 0.15) if self._fn_handler is not None else 0.0
                self._double_tap = double_tap.DoubleTapGate(
                    clock=self._clock or time.monotonic, release_lag_s=lag)
        else:
            self._double_tap = None

    @property
    def double_tap_enabled(self) -> bool:
        return self._double_tap is not None

    def _double_tap_forget(self):
        dt = self._double_tap
        if dt is not None:
            dt.reset()

    def _press_ends_hands_free(self) -> bool:
        """A press of the hotkey in a hands-free take: does it end it? Only
        with double-tap on (or for a take it locked), and not when it is the
        last release bouncing."""
        dt = self._double_tap
        if dt is None:
            return self._locked_by_double_tap
        return not dt.is_bounce()

    # ── Callbacks (run in a thread to avoid blocking the event tap) ──

    def _fire_press(self):
        threading.Thread(target=self._on_press_cb, daemon=True).start()

    def _fire_release(self):
        threading.Thread(target=self._on_release_cb, daemon=True).start()

    def _fire_cancel(self):
        """Esc-cancel — invoke the pipeline's cancel handler. No-op when
        the constructor wasn't given an `on_cancel` callback (e.g. for
        the wizard's test listeners)."""
        if self._on_cancel_cb is None:
            return
        threading.Thread(target=self._on_cancel_cb, daemon=True).start()

    def _fire_lock(self):
        """Hands-free just locked on. No-op without an `on_lock` callback."""
        if self._on_lock_cb is None:
            return
        threading.Thread(target=self._on_lock_cb, daemon=True).start()

    def _fire_visual_feedback(self, event_type):
        """Override in RecorderApp to push toast notifications. No-op by default."""
        pass

    def _dismiss_emoji_keyboard(self):
        """Aggressively dismiss emoji picker with multiple Escape attempts.

        The emoji picker on macOS 26.3.1 / M3 Max takes variable time to
        appear and accept input. A single Escape after 0.15s is not
        reliable. We send 4 Escape events with staggered delays (50ms,
        100ms, 200ms, 300ms) from a background thread.
        """
        def _send_escapes():
            import time
            try:
                from Quartz import CGEventPost, CGEventCreateKeyboardEvent, kCGHIDEventTap
                for delay in [0.05, 0.1, 0.2, 0.3]:
                    time.sleep(delay)
                    esc_down = CGEventCreateKeyboardEvent(None, 53, True)
                    esc_up = CGEventCreateKeyboardEvent(None, 53, False)
                    CGEventPost(kCGHIDEventTap, esc_down)
                    CGEventPost(kCGHIDEventTap, esc_up)
                print("[HOTKEY] Sent 4x Escape to dismiss emoji picker (650ms total)")
                self._fire_visual_feedback("sticky_off_fn")
            except Exception as e:
                print(f"[HOTKEY] Failed to dismiss emoji picker: {e}")

        threading.Thread(target=_send_escapes, daemon=True).start()

    # ── Observers of the shortcuts ────────────────────────────────────

    def attach_observer(self, handler):
        """Let one more handler see every key event on this listener's tap.

        The process may have only ONE event tap (see mac_hotkey_monitor.py:
        two taps crashed it), so anything else that needs to hear the keyboard
        joins this one. The handler is added last, after the hotkey's own. It
        answers True from ``handle`` only to keep the main key of a registered
        action shortcut from the app in front (src/action_hotkeys.py: the key
        and its repeats, never a modifier, never while the hotkey is held), and
        it must catch its own errors. It is used by the action shortcuts. The
        hotkey's own handlers and state are not touched.
        """
        self._tap.add_handler(handler)

    # ── State management ──────────────────────────────────────────────

    def set_processing(self, processing: bool):
        """The pipeline calls this while its "still working" offer is on
        screen, so Esc can stop that wait (WindowsHotkeyListener.set_processing,
        same). The rest of the time Esc belongs to the app in front."""
        self._processing = bool(processing)

    def reset_state(self):
        """Reset internal state — called when recording is stopped externally
        (e.g. manual stop button in the UI)."""
        print(f"[HOTKEY] reset_state() called | was: sticky={self._sticky} recording={self._recording}")
        self._hotkey_held = False
        self._sticky = False
        self._recording = False
        self._locked_by_double_tap = False
        self._double_tap_forget()
        print("[HOTKEY] → State reset to: sticky=False recording=False hotkey_held=False")

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self):
        hotkey_display = " + ".join(self._keys)
        _diag_log(
            f"[HOTKEY/{self._id}] start() called — hotkey={hotkey_display} "
            f"thread={threading.current_thread().name}"
        )
        self._tap.start()

    def stop(self):
        _diag_log(f"[HOTKEY/{self._id}] stop() called")
        if self._tap is not None:
            self._tap.stop()

    def join(self):
        # No listener thread to join — daemon CFRunLoop dies with the process.
        pass

    def is_alive(self):
        """Is the key listener still delivering keys? True, False (it died or
        was switched off by the system), or None when that isn't known yet.
        Asked from time to time by app.py, because a listener whose
        permission was taken away gives no sign by itself."""
        return self._tap.is_alive()

    # ── Backward-compat properties for IPC polling ────────────────────

    @property
    def is_combo_active(self) -> bool:
        """True iff the hotkey is currently held. Same surface as the
        Windows ``WindowsHotkeyListener.is_combo_active`` so the wizard can
        check one attribute regardless of platform.
        """
        if self._fn_handler is not None:
            return self._fn_handler.is_pressed
        if self._hotkey_handler is not None:
            return self._hotkey_handler.is_active
        return False
