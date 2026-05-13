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

import threading
from mac_hotkey_monitor import (
    MacEventTap,
    FnHandler,
    SpaceHandler,
    GenericHotkeyHandler,
)


class SmartHotkeyListener:

    def __init__(self, on_press, on_release, keys=None):
        """
        Args:
            on_press: Callback when recording should start
            on_release: Callback when recording should stop
            keys: List of key names (default: ["fn"])
                 Examples: ["fn"], ["cmd", "shift", "space"], ["option", "space"]
        """
        self._on_press_cb = on_press
        self._on_release_cb = on_release
        self._keys = keys or ["fn"]

        self._hotkey_held = False   # Hotkey currently down
        self._sticky = False        # Locked-on (toggle) mode active
        self._recording = False     # Are we recording right now?

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
        # reaches other apps (dialog dismiss, vim, etc).
        self._esc_handler = GenericHotkeyHandler(
            keys=["esc"],
            on_press=self._on_esc_press,
            on_release=lambda: None,
            suppress=False,
        )
        self._tap.add_handler(self._esc_handler)

        # Backward-compat surface for app.py's get_fn_key_state — see
        # docstring at top of this file. Whichever of the two handlers is
        # active for this hotkey carries the relevant `_fn_pressed` /
        # `_hotkey_active` attribute (as a property alias in mac_hotkey_monitor).
        self._monitor = self._fn_handler if self._fn_handler else self._hotkey_handler

        print(f"[HOTKEY] SmartHotkeyListener initialized with keys: {self._keys}")

    # ── Key events ────────────────────────────────────────────────────

    def _on_hotkey_press(self):
        print(f"[HOTKEY] Hotkey pressed | sticky={self._sticky} recording={self._recording}")
        if not self._recording:
            print("[HOTKEY] → Starting push-to-talk")
            self._hotkey_held = True
            self._recording = True
            self._fire_press()
        else:
            # Hotkey pressed while already recording — keep held flag in
            # sync so Fn+Space detection still works in sticky mode.
            self._hotkey_held = True

    def _on_hotkey_release(self):
        print(f"[HOTKEY] Hotkey released | sticky={self._sticky} recording={self._recording}")
        self._hotkey_held = False
        if self._recording and not self._sticky:
            print("[HOTKEY] → Stopping push-to-talk")
            self._recording = False
            self._fire_release()

    def _on_space_press(self):
        print(
            f"[HOTKEY] Space pressed | hotkey_held={self._hotkey_held} "
            f"recording={self._recording} sticky={self._sticky}"
        )
        if self._hotkey_held and self._recording and not self._sticky:
            self._sticky = True
            print("📌 Hotkey+Space → Sticky mode ON! Press hotkey+Space again to stop.")
            self._fire_visual_feedback("sticky_on")
        elif self._hotkey_held and self._recording and self._sticky:
            self._sticky = False
            self._recording = False
            print("🛑 Hotkey+Space → Sticky mode OFF")
            self._fire_visual_feedback("sticky_off_fn")
            self._fire_release()

    def _on_esc_press(self):
        """Esc cancels recording in sticky mode — universal escape hatch.

        On Macs where macOS swallows Fn at HID level, Fn+Space cancel can be
        finicky to end. Esc is never typed during normal dictation, so it's
        safe as a fallback. We only fire when actively in sticky mode —
        otherwise pressing Esc is a no-op so we don't interfere with normal
        Esc usage in dialogs, vim, etc.
        """
        if self._recording and self._sticky:
            print("[HOTKEY] Esc → Sticky mode OFF (universal cancel)")
            self._sticky = False
            self._recording = False
            self._fire_visual_feedback("sticky_off_esc")
            self._fire_release()

    # ── Callbacks (run in a thread to avoid blocking the event tap) ──

    def _fire_press(self):
        threading.Thread(target=self._on_press_cb, daemon=True).start()

    def _fire_release(self):
        threading.Thread(target=self._on_release_cb, daemon=True).start()

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

    # ── State management ──────────────────────────────────────────────

    def reset_state(self):
        """Reset internal state — called when recording is stopped externally
        (e.g. manual stop button in the UI)."""
        print(f"[HOTKEY] reset_state() called | was: sticky={self._sticky} recording={self._recording}")
        self._hotkey_held = False
        self._sticky = False
        self._recording = False
        print("[HOTKEY] → State reset to: sticky=False recording=False hotkey_held=False")

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self):
        hotkey_display = " + ".join(self._keys)
        print(f"⌨️  Hotkey: Hold {hotkey_display} to record | Press Space while holding = sticky")
        print("   To cancel sticky: tap hotkey again, OR press Esc (universal escape hatch)")
        self._tap.start()

    def stop(self):
        if self._tap is not None:
            self._tap.stop()

    def join(self):
        # No listener thread to join — daemon CFRunLoop dies with the process.
        pass

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
