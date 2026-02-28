# Research & Analysis: Mac vs PC Overlay Behavioral Differences

## 1. Executive Summary

The Waffler overlay behaves differently on Mac vs PC due to **three distinct root causes** operating at different layers of the stack:

1. **Hotkey Layer (Fn key detection)**: The Mac Fn key is fundamentally unreliable for hotkey detection. The `kCGEventFlagMaskSecondaryFn` flag is set on F1-F12 and arrow key events even when Fn is NOT pressed, causing false positives and missed detections. The Windows `Ctrl+Win` hotkey, by contrast, uses a robust low-level keyboard hook (`SetWindowsHookEx`) with clean key state tracking.

2. **Overlay Click Detection (NSWindow limitations)**: The Mac overlay's borderless `NSWindow` cannot become the key window by default (`canBecomeKeyWindow` returns `NO` for borderless windows). Combined with `NSApplicationActivationPolicyAccessory`, this means the overlay may not reliably receive mouse events when another app is in the foreground. Windows' `tkinter.overrideredirect(True)` + `-topmost True` has no such limitation.

3. **Toast Text Rendering (Paragraph style attribute bug)**: The Mac toast uses `NSMutableParagraphStyle` (the class object) as the attribute dictionary key instead of the correct `NSParagraphStyleAttributeName` string. This means text centering is silently ignored, causing misaligned text in toasts.

**Estimated complexity to fix all issues**: Medium-High
**Files requiring modification**: 2 (`src/overlay_process.py`, `src/smart_hotkey.py` / `src/fn_key_cgevent.py`)

---

## 2. Detailed Research Findings

### 2.1 Architecture Overview

The system runs as a main process (pywebview UI in `app.py`) that spawns:
- A **hotkey listener** (platform-specific, runs on its own thread)
- An **overlay subprocess** (platform-specific, owns its own UI event loop)

Communication between the main process and overlay subprocess is via JSON over stdin/stdout pipes.

```
[app.py Pipeline]
    |
    |---> SmartHotkeyListener (Mac: CGEventTap / Fn key)
    |     WindowsHotkeyListener (Win: SetWindowsHookEx / Ctrl+Win)
    |
    |---> RecordingOverlay (src/overlay.py)
              |
              |--spawn subprocess-->  overlay_process.py (Mac: PyObjC NSWindow)
              |                       overlay_process_windows.py (Win: tkinter)
              |
              |<--stdout JSON events-- {"event": "cancel_request"}, {"event": "stop"}, etc.
              |--stdin JSON commands--> {"type": "show"}, {"type": "show_toast", ...}, etc.
```

### 2.2 Issue 1: Fn Key Does Not Show Waffle Popup on Mac

#### What happens on Windows (WORKS)

File: `/Users/james/waffler/src/windows_hotkey.py`

- Uses `SetWindowsHookExW(WH_KEYBOARD_LL, ...)` -- a system-wide low-level keyboard hook
- Tracks `Ctrl` and `Win` key states independently via `vkCode` matching
- State machine: `IDLE -> PUSH_TO_TALK -> (optional STICKY) -> IDLE`
- Clean press/release detection: `WM_KEYDOWN`/`WM_KEYUP` events with specific virtual key codes
- Actively suppresses Win key-up to prevent Start menu activation
- Has a polling fallback (`GetAsyncKeyState`) if the hook fails
- Fires `on_press` -> Pipeline calls `overlay.show()` -> sends `{"type": "show"}` to subprocess
- **Result**: Waffle overlay appears reliably every time Ctrl+Win is held

#### What happens on Mac (BROKEN)

File: `/Users/james/waffler/src/smart_hotkey.py` -> delegates to `/Users/james/waffler/src/fn_key_cgevent.py`

The Mac uses `FnKeyMonitor` which creates a `CGEventTap` listening for `kCGEventFlagsChanged` events and checks the `kCGEventFlagMaskSecondaryFn` flag (bit 23, value `0x800000`).

**Root cause of failure -- The Fn key flag is unreliable on macOS:**

1. **Hardware-level modifier**: The Fn key is a meta-modifier that alters scancodes at the hardware level. macOS has no direct notion of a standalone "Fn key press" event. (Source: documented in `/Users/james/waffler/FN_KEY_SOLUTION.md`)

2. **False positives**: F1-F12 keys and arrow keys all have the `kCGEventFlagMaskSecondaryFn` flag set in their events, even when the Fn key is NOT pressed. This is because macOS treats these keys as inherently "function-key-modified."

3. **Missing events**: On many MacBook keyboards (especially Apple Silicon), pressing Fn alone may not generate a `kCGEventFlagsChanged` event at all, or generates it inconsistently.

4. **No filtering for false positives**: The `fn_key_cgevent.py` implementation (line 46-58) simply checks `flags & fn_flag` on any `kCGEventFlagsChanged` event. It does NOT filter out events from F1-F12 or arrow keys. The alternative `fn_hotkey_cgeventtap.py` does attempt filtering (checking for "pure Fn" events with no keycode), but it is marked as "EXPERIMENTAL" and is not the one used in production.

5. **Requires Accessibility permission**: `CGEventTapCreate` requires Input Monitoring (or Accessibility) permission. If permission is denied, the tap returns `None` and the hotkey silently fails. The code prints a warning but does not show a visible error to the user.

**Evidence from git history:**
```
a78095f  Revert to Right Option key - Fn key causes macOS dispatch queue crashes
59cc9b6  Fix Fn key crash: use local monitor instead of global monitor
93e0976  Fix PyObjC error: Remove NSObject inheritance from FnKeyMonitor
0b9631a  Fix Fn key crash and implement working Fn key detection
```

These commits show a history of repeated crashes and fixes related to Fn key detection, confirming this is a persistent problem.

#### Windows vs Mac comparison table

| Aspect | Windows (`Ctrl+Win`) | Mac (`Fn`) |
|--------|---------------------|------------|
| Hook mechanism | `SetWindowsHookExW(WH_KEYBOARD_LL)` | `CGEventTapCreate(kCGSessionEventTap)` |
| Key identification | Specific `vkCode` values (0x5B, 0xA2) | Flag bit (`0x800000`) in event flags |
| False positive risk | None -- unique VK codes | High -- flag set on F1-F12, arrows |
| Reliability | Very high | Low to moderate |
| Permission needed | None (admin not required) | Input Monitoring / Accessibility |
| Fallback mechanism | `GetAsyncKeyState` polling | None |
| Crash history | None | Multiple crashes documented |

### 2.3 Issue 2: X Button Does Not Show Cancel Toast on Mac

#### What happens on Windows (WORKS)

File: `/Users/james/waffler/src/overlay_process_windows.py`

```python
# Line 476-479
def _on_click(event):
    x = event.x
    if x < CANCEL_HIT_X:        # x < 36 -> cancel zone
        emit("cancel_request")   # Emits to parent
    elif x > STOP_HIT_X:        # x > 164 -> stop zone
        emit("stop")
```

- Simple X-coordinate region check (pill is horizontal)
- `tkinter.Canvas.bind("<Button-1>", _on_click)` -- always receives clicks because tkinter's `-topmost` window reliably captures mouse events
- Clicking X emits `{"event": "cancel_request"}` -> parent shows toast via `{"type": "show_toast", "style": "cancel", ...}`

#### What happens on Mac (PARTIALLY BROKEN)

File: `/Users/james/waffler/src/overlay_process.py`

```python
# Line 331-350 (WaffleView.mouseDown_)
def mouseDown_(self, event):
    loc = event.locationInWindow()
    x = loc.x
    y_window = loc.y
    y = WIN_H - y_window  # Flip Y coordinate

    cancel_dist2 = (x - BTN_CANCEL_CX) ** 2 + (y - BTN_CANCEL_CY) ** 2
    stop_dist2 = (x - BTN_STOP_CX) ** 2 + (y - BTN_STOP_CY) ** 2

    if cancel_dist2 <= BTN_HIT_R2:
        emit("cancel_request")
    elif stop_dist2 <= BTN_HIT_R2:
        emit("stop")
```

The coordinate math is correct (verified by manual calculation). The event emission matches Windows. **The problem is that `mouseDown_` may never be called.** Three issues prevent reliable click delivery:

**Issue A: Borderless NSWindow cannot become key window**

The `_g_window` is created with `NSWindowStyleMaskBorderless` (style mask = 0). By default, `NSWindow.canBecomeKeyWindow()` returns `NO` for borderless windows. This means:
- `makeKeyAndOrderFront_(None)` (line 652) does NOT make the window the key window
- The window is visible but not "key" -- it's essentially a passive overlay
- Mouse events may be delivered to the view (since `setIgnoresMouseEvents_(False)` is set), but the first responder chain doesn't include the overlay's view

**Issue B: NSApplicationActivationPolicyAccessory**

The overlay subprocess sets `NSApplicationActivationPolicyAccessory` (line 782). Accessory apps:
- Do not appear in the Dock
- Do not have a menu bar
- **May not receive mouse events when the user's focus is on another application**

When the user is dictating, their focus is typically on another app (a text editor, browser, etc.). The overlay is floating above that app. Clicking the overlay requires macOS to route the mouse event from the focused app to the accessory app. This routing is not guaranteed for accessory apps, especially when they are not the active application.

**Issue C: Missing `acceptsFirstMouse_` override**

Neither `WaffleView` nor `ToastView` overrides `acceptsFirstMouse_`. On macOS, this method determines whether a view accepts the first mouse-down event that also activates its window. Without `acceptsFirstMouse_` returning `True`, the first click on the overlay may be consumed by the window activation process and never reach `mouseDown_`.

**Combined effect**: Even though the coordinate math and event emission are correct, clicks on the Mac overlay may be silently dropped due to window activation policies. This explains why:
- The X button click doesn't trigger `cancel_request`
- The stop button might also not work reliably
- The behavior appears "broken" compared to Windows where tkinter's `-topmost` window always receives clicks

### 2.4 Issue 3: Toast Rendering Differences

#### Toast positioning

| Aspect | Mac | Windows |
|--------|-----|---------|
| Toast size | 380x170 | 420x160 |
| Position calculation | `ty = _waffle_y + WAFFLE_H + TOAST_PAD` (above waffle, macOS coords) | `ty = _pill_y - th - TOAST_PAD` (above pill, tkinter coords) |
| Centering | `tx = _waffle_x + (WAFFLE_W - TOAST_W) // 2` (centered on waffle) | `tx = (_screen_w - tw) // 2` (centered on screen) |

Note: The Mac centers the toast relative to the waffle overlay, while Windows centers it relative to the screen. Since the waffle/pill is itself centered on the screen, the result should be visually similar, but the Mac toast is horizontally offset by `(WAFFLE_W - TOAST_W) // 2 = (69 - 380) // 2 = -155px` from the waffle's left edge, which is correct centering.

#### Toast text centering bug (Mac only)

File: `/Users/james/waffler/src/overlay_process.py`, lines 449-453, 465-468, 532-535

```python
heading_attrs = {
    NSFontAttributeName: heading_font,
    NSForegroundColorAttributeName: heading_color,
    NSMutableParagraphStyle: para_style,  # BUG: Wrong key!
}
```

The correct key for paragraph style in an `NSAttributedString` attributes dictionary is `NSParagraphStyleAttributeName` (string value `"NSParagraphStyle"`), NOT the `NSMutableParagraphStyle` class itself. Using the class as a dictionary key means:
- The paragraph style (which sets center alignment) is silently ignored
- All text renders left-aligned instead of centered
- This affects heading text, body text, and button labels in the toast
- No error is raised -- the wrong key is simply unused by the text rendering system

This bug is present in three locations: heading attributes (line 452), body attributes (line 468), and button text attributes (line 535).

#### Toast button click handling

The toast `ToastView.mouseDown_` implementation (lines 620-639) has correct coordinate conversion and button zone detection. However, it suffers from the same window activation issues as the main waffle overlay (borderless window can't become key, accessory app may not receive clicks).

#### Toast visual differences

| Element | Mac | Windows |
|---------|-----|---------|
| Background | `#2A1F0E` (warm dark) | `#18181f` (cool dark) |
| Border | `#C8A256` golden | `#C8A256` golden |
| Cancel icon | Sad waffle | Red circle with X |
| Error icon | Sad waffle | Golden circle with ! |
| Cancel buttons | Discard (red #D94040) + Keep going (golden #D4A843) | Discard (dark red bg #2d1520, red text #ef4444) + Keep going (purple #7c3aed) |
| Shadow | No shadow layer | Shadow layer (offset dark rect) |
| Divider line | None | Yes (`#2a2a35`) |
| Text layout | Icon centered above text | Icon left, text right |

The visual design is intentionally different between platforms (the Mac uses a waffle-themed design, Windows uses a more standard notification design). However, the functional behavior should be identical -- both should emit `toast_action` events on button clicks.

### 2.5 Recent Fixes Already Applied (Git History)

The self-hosted branch already has several fixes that were NOT on the main branch previously:

| Commit | Fix |
|--------|-----|
| `5974fae` | Convert mouse coordinates for flipped view |
| `7b143ad` | X button now emits `cancel_request` to match Windows |
| `8a8fed4` | Mac overlay coordinate system and button behavior |
| `9ec2b60` | Complete toast with text and buttons (matches Windows) |
| `1094bf6` | Mac overlay syrup now fills from BOTTOM (was filling from top) |
| `f9eeb95` | Make Mac syrup colors fully opaque to match Windows |

These fixes addressed the coordinate conversion and event emission correctly. **The remaining issues are the window-level click delivery problems and the hotkey detection unreliability.**

### 2.6 Remote vs Local Status

Local `self-hosted` branch is 1 commit behind `origin/self-hosted`:
- Missing commit `ad236e4`: "Remove backend -- self-hosted is BYOK only" (only removes backend files, irrelevant to overlay behavior)

No overlay-related fixes exist on the remote that are missing locally.

---

## 3. Root Cause Summary

### Why Fn key doesn't show waffle popup on Mac

**Primary cause**: The Fn key on macOS is a hardware-level modifier that does not generate reliable, isolated `kCGEventFlagsChanged` events. The `kCGEventFlagMaskSecondaryFn` flag is also set on F1-F12 and arrow key events, causing false positives. On many keyboards, pressing Fn alone generates no event at all.

**Secondary cause**: The CGEventTap requires Input Monitoring / Accessibility permission. If permission is not granted, the tap fails silently (the code prints a console warning but shows no UI error).

### Why X button doesn't show cancel toast on Mac

**Primary cause**: The borderless `NSWindow` used for the overlay cannot become the key window (default `canBecomeKeyWindow` returns `NO`). Combined with `NSApplicationActivationPolicyAccessory`, mouse events may not be delivered to the overlay's view when another application has focus.

**Secondary cause**: Missing `acceptsFirstMouse_` override means the first click on the overlay may be consumed by window activation rather than delivered to the view's `mouseDown_` handler.

**Not the cause**: The coordinate conversion math and event emission logic are correct (verified by manual calculation).

---

## 4. Recommended Development Plan

### Phase 1: Fix Overlay Click Reception (Critical)

**Goal**: Make the Mac overlay reliably receive mouse clicks when another app is in the foreground.

#### Step 1.1: Create a custom NSWindow subclass that can become key

**File**: `/Users/james/waffler/src/overlay_process.py`
**Action**: Modify

Add a custom `NSWindow` subclass (or use `NSPanel`) that overrides `canBecomeKeyWindow` to return `True`:

```python
class ClickableWindow(NSWindow):
    """Borderless window that accepts mouse clicks even when not key."""
    def canBecomeKeyWindow(self):
        return True
    def canBecomeMainWindow(self):
        return False
```

Replace `NSWindow.alloc().initWithContentRect_...` with `ClickableWindow.alloc().initWithContentRect_...` for both the waffle window (line 800) and the toast window (line 721).

**Acceptance Criteria**:
- The overlay window can become the key window
- Clicking on the overlay reliably triggers `mouseDown_` in the view

#### Step 1.2: Add `acceptsFirstMouse_` to both views

**File**: `/Users/james/waffler/src/overlay_process.py`
**Action**: Modify

Add to both `WaffleView` and `ToastView`:

```python
def acceptsFirstMouse_(self, event):
    return True
```

**Acceptance Criteria**:
- The first click on the overlay (when it wasn't previously clicked) is processed as a button click, not consumed by window activation

#### Step 1.3: Consider using NSPanel instead of NSWindow

**File**: `/Users/james/waffler/src/overlay_process.py`
**Action**: Modify (alternative to Step 1.1)

`NSPanel` is designed for floating auxiliary windows and has `canBecomeKeyWindow` returning `YES` by default for panels with a non-zero style mask. However, for borderless panels, you still need the override. The advantage of `NSPanel` is that it's designed for exactly this use case (floating utility panels) and integrates better with macOS's window management.

This requires importing `NSPanel` from `AppKit` and using it instead of `NSWindow`.

**Key detail**: Use `NSPanel` with `NSUtilityWindowMask` or `NSNonactivatingPanelMask` to prevent the overlay from deactivating the user's current application when clicked.

### Phase 2: Fix Toast Text Centering (Moderate)

**Goal**: Fix the paragraph style attribute key so toast text renders centered.

#### Step 2.1: Import and use the correct attribute key

**File**: `/Users/james/waffler/src/overlay_process.py`
**Action**: Modify

In the import section, add:
```python
from AppKit import NSParagraphStyleAttributeName
```

Then replace all three occurrences of `NSMutableParagraphStyle: para_style` with `NSParagraphStyleAttributeName: para_style` in the attribute dictionaries (lines 452, 468, 535).

**Acceptance Criteria**:
- Toast heading text is centered
- Toast body text is centered
- Toast button labels are centered

### Phase 3: Fix or Replace Fn Key Hotkey (Critical)

**Goal**: Provide a reliable hotkey on Mac that triggers recording consistently.

#### Option A: Switch to a configurable standard key (RECOMMENDED)

Replace the Fn key with a configurable standard key combination. The codebase already has `smart_hotkey_f13.py` which uses F13 (or Right Command as fallback). This is the approach recommended in the project's own documentation (`FN_KEY_SOLUTION.md`).

**File**: `/Users/james/waffler/src/smart_hotkey.py`
**Action**: Rewrite to use F13 from `smart_hotkey_f13.py`

OR

**File**: `/Users/james/waffler/app.py`
**Action**: Modify line 1494 to use `smart_hotkey_f13.SmartHotkeyListener` instead of `smart_hotkey.SmartHotkeyListener`

**Key consideration**: The existing `HotkeyListener` in `src/hotkey.py` uses `pynput` and supports arbitrary key combinations configured in `config.yaml` (currently set to `ctrl+space`). However, `app.py` line 1494 bypasses this and hardcodes `SmartHotkeyListener` (which uses Fn key). This disconnect means the config.yaml `hotkey.key: "ctrl+space"` setting is ignored on Mac.

#### Option B: Use the HotkeyListener with pynput (from config.yaml)

**File**: `/Users/james/waffler/app.py`
**Action**: Modify `start_hotkey()` to respect the `config.yaml` hotkey setting on Mac

Currently on Mac, `start_hotkey()` always creates `SmartHotkeyListener` (Fn key). It could instead create `HotkeyListener` with the configured key combination (e.g., `ctrl+space`), which uses pynput and is more reliable than CGEventTap Fn detection.

However, pynput has its own macOS issues (dispatch queue crashes documented in `FN_KEY_SOLUTION.md`), so this needs testing.

#### Option C: Improve Fn key detection with better filtering

**File**: `/Users/james/waffler/src/fn_key_cgevent.py`
**Action**: Modify

Port the filtering logic from `fn_hotkey_cgeventtap.py` (the more advanced implementation) into `fn_key_cgevent.py`:
- Only trigger on `kCGEventFlagsChanged` events (not key press/release)
- Only when the event's keycode is `-1` (no physical key, just the modifier flag changing)
- Debounce to avoid rapid false triggers

This improves reliability but the Fn key will still be fundamentally unreliable on some hardware.

### Phase 4: Testing and Validation

#### Step 4.1: Test overlay click reception

1. Start Waffler on Mac
2. Open a text editor and focus it
3. Trigger recording (using whatever hotkey works)
4. Click the X button on the waffle overlay
5. Verify: Cancel toast appears
6. Click "Discard" on the toast
7. Verify: Recording is cancelled

#### Step 4.2: Test toast text rendering

1. Trigger the cancel toast (click X on overlay)
2. Verify: "Cancel recording?" text is centered
3. Verify: "Audio will be discarded." text is centered
4. Verify: Button labels ("Discard", "Keep going") are centered

#### Step 4.3: Test hotkey reliability

1. Start Waffler on Mac
2. Press the configured hotkey 10 times in sequence
3. Verify: Waffle overlay appears every time
4. Verify: No false triggers when pressing F1-F12 or arrow keys

---

## 5. File Manifest

| File Path | Action | Description |
|-----------|--------|-------------|
| `/Users/james/waffler/src/overlay_process.py` | Modify | Add ClickableWindow subclass, acceptsFirstMouse_, fix NSParagraphStyleAttributeName |
| `/Users/james/waffler/src/smart_hotkey.py` | Modify or Replace | Switch from Fn key to F13/Right Cmd or configurable key |
| `/Users/james/waffler/src/fn_key_cgevent.py` | Modify (if Option C) | Add filtering for pure Fn events |
| `/Users/james/waffler/app.py` | Modify (if Option B) | Respect config.yaml hotkey setting on Mac |

---

## 6. Testing Strategy

### Unit tests
- Verify coordinate conversion math in `WaffleView.mouseDown_` and `ToastView.mouseDown_`
- Verify toast button zone calculation matches drawn positions

### Integration tests
- Test overlay subprocess launches and emits `{"event": "ready"}`
- Test `{"type": "show"}` / `{"type": "hide"}` commands work
- Test `{"type": "show_toast"}` creates visible toast window
- Test click on X button emits `{"event": "cancel_request"}`
- Test click on stop button emits `{"event": "stop"}`
- Test toast button clicks emit `{"event": "toast_action", "action": "..."}`

### Manual testing
- Test on MacBook Pro (built-in keyboard) -- Fn key detection
- Test on Mac with external keyboard -- Fn key detection
- Test overlay click reception while another app has focus
- Test toast button click reception
- Test that overlay does not steal focus from the user's active application

---

## 7. Open Questions & Assumptions

### Open Questions
1. **Which hotkey approach to use on Mac?** The user needs to decide between F13 (requires Karabiner-Elements on MacBook), Right Command, or a configurable key combination. The Windows `Ctrl+Win` has no direct equivalent on Mac.
2. **Should the overlay use NSPanel?** This is the macOS-idiomatic approach for floating utility panels but requires more testing to verify it doesn't steal activation from the user's current app.
3. **Is Input Monitoring / Accessibility permission granted?** If the CGEventTap fails silently due to missing permissions, the user sees no error. Should there be a visible permission check?

### Assumptions
- The user is on macOS Sonoma or later (Apple Silicon)
- The Fn key behavior described matches the user's hardware (MacBook built-in keyboard)
- The overlay subprocess starts successfully (Python is available in PATH)
- PyObjC is installed and functional

---

## Appendix A: Complete Diff Between Mac and Windows Overlay Designs

The Mac and Windows overlays have fundamentally different visual designs:

| Aspect | Mac (PyObjC) | Windows (tkinter) |
|--------|-------------|-------------------|
| Shape | Square 69x69 waffle with 4x4 grid | Horizontal 200x44 pill |
| Animation | 16 cells fill with syrup bottom-up | 16 VU bars + syrup gradient |
| Buttons | Two circles below waffle (X and square) | X on left side, square on right side |
| Click detection | Distance-from-center calculation (circular hit zones) | X-coordinate region check (rectangular zones) |
| Window type | NSWindow (borderless) | tkinter.Tk (overrideredirect) |
| Transparency | NSColor.clearColor() background | `-transparentcolor` attribute |
| Always-on-top | `NSFloatingWindowLevel + 1` | `-topmost True` |
| Animation timer | NSTimer (50ms) | tkinter.after() (50ms) |
| Toast window | Separate NSWindow above waffle | tkinter.Toplevel above pill |
| Activation policy | NSApplicationActivationPolicyAccessory | N/A (standard process) |

## Appendix B: Event Flow Comparison

### Mac flow (when it works):
```
Fn key press
  -> CGEventTap callback (fn_key_cgevent.py:46)
  -> FnKeyMonitor._on_fn_press (fn_key_cgevent.py:54)
  -> SmartHotkeyListener._on_fn_press (smart_hotkey.py:41)
  -> Pipeline.on_hotkey_press (app.py:1272)
  -> overlay.show() (overlay.py:64)
  -> subprocess stdin: {"type": "show"}
  -> overlay_process.py _dispatch_cmd "show"
  -> _g_window.makeKeyAndOrderFront_(None)
  -> Waffle appears on screen
```

### Windows flow:
```
Ctrl+Win key press
  -> SetWindowsHookEx callback (windows_hotkey.py:174)
  -> WindowsHotkeyListener._check_combo_press (windows_hotkey.py:226)
  -> Pipeline.on_hotkey_press (app.py:1272)
  -> overlay.show() (overlay.py:64)
  -> subprocess stdin: {"type": "show"}
  -> overlay_process_windows.py _handle_cmd "show"
  -> _root.deiconify() + _root.lift()
  -> Pill appears on screen
```

### Mac X button click flow (when it works):
```
User clicks X button on waffle
  -> WaffleView.mouseDown_ (overlay_process.py:331)
  -> emit("cancel_request") (overlay_process.py:347)
  -> stdout: {"event": "cancel_request"}
  -> overlay.py _read_stdout -> _on_cancel_request callback
  -> Pipeline._on_overlay_cancel_request (app.py:1244)
  -> overlay.show_toast("cancel", ...) (app.py:1248)
  -> subprocess stdin: {"type": "show_toast", "style": "cancel", ...}
  -> overlay_process.py _dispatch_cmd "show_toast"
  -> _show_toast() creates toast NSWindow
  -> Toast appears above waffle
```

### Windows X button click flow:
```
User clicks left region of pill
  -> _on_click(event) via Canvas.bind (overlay_process_windows.py:476)
  -> emit("cancel_request") (overlay_process_windows.py:479)
  -> stdout: {"event": "cancel_request"}
  -> overlay.py _read_stdout -> _on_cancel_request callback
  -> Pipeline._on_overlay_cancel_request (app.py:1244)
  -> overlay.show_toast("cancel", ...) (app.py:1248)
  -> subprocess stdin: {"type": "show_toast", "style": "cancel", ...}
  -> overlay_process_windows.py _handle_cmd "show_toast"
  -> _show_toast() creates toast Toplevel
  -> Toast appears above pill
```
