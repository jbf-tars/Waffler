# Hotkey Customization & App Icon — Design Spec

**Date:** 2026-03-20
**Status:** Approved

## Overview

Two changes: (1) let users rebind the recording hotkey via a game-style key capture UI in Settings, and (2) replace the default Python icon in the window title bar with the Waffler brand icon.

## Feature 1: Hotkey Customization

### Current Behavior

- Win+Ctrl is hardcoded in `windows_hotkey.py`
- Hold Win+Ctrl = push-to-talk (release to stop)
- Space during hold = sticky mode (recording locks on, press Win+Ctrl again to cancel)
- No UI to change the hotkey

### Design

#### Settings UI

A new **"Hotkey"** section in the Settings panel, positioned between API Keys and Preferences. Use existing CSS variables (`var(--bg-card)`, `var(--radius-lg)`) rather than hardcoded values.

- Section header: keyboard icon + "Hotkey" label (uppercase, small text, matching existing `.settings-section` style)
- Row with card background:
  - Left side: "Recording Hotkey" title + "Hold to record, release to stop" subtitle
  - Right side: Golden badge showing current binding (e.g. "Win + Ctrl") + "Change" button (golden `#C8A256` background)

On load, the Settings panel calls `get_hotkey_config()` to populate the badge with the current binding.

#### Key Capture Popup

Clicking "Change" opens a modal overlay:

- Dark semi-transparent backdrop
- Centered modal card (rounded corners, drop shadow)
- Keyboard icon + "Change Hotkey" title + "Press your new key combination..." instruction
- Capture area: bordered box with golden accent (`#C8A256` border), displays keys live as pressed (e.g. "Alt + Shift"), instruction text "Hold your desired keys together"
- Three buttons:
  - **Cancel** — closes modal, no changes
  - **Reset Default** — restores Win+Ctrl
  - **Save** — persists the new binding and applies it immediately

#### Key Capture Logic (Frontend)

- On modal open, listen for `keydown`/`keyup` events on the document
- Track all currently-held modifier keys (Ctrl, Alt, Shift, Win/Meta) plus up to one non-modifier key
- Display the combination live in the capture area as keys are held
- The combination "locks in" when keys are held — if the user releases all keys, the last-seen combination persists in the capture area until new keys are pressed or the modal is closed
- Require at least one modifier key (prevent binding to just a letter key)
- On "Save", send the key combination (as an array of string identifiers) to the backend API

#### Key Identifier Vocabulary

The frontend and backend share a canonical set of string key identifiers. The backend maps these to Win32 VK codes:

| String ID | VK Codes (any match) | Notes |
|-----------|---------------------|-------|
| `"win"` | `VK_LWIN` (0x5B), `VK_RWIN` (0x5C) | Modifier |
| `"ctrl"` | `VK_CONTROL` (0x11), `VK_LCONTROL` (0xA2), `VK_RCONTROL` (0xA3) | Modifier |
| `"alt"` | `VK_MENU` (0x12), `VK_LMENU` (0xA4), `VK_RMENU` (0xA5) | Modifier |
| `"shift"` | `VK_SHIFT` (0x10), `VK_LSHIFT` (0xA0), `VK_RSHIFT` (0xA1) | Modifier |
| Non-modifier keys | Single VK code, e.g. `"f9"` → `VK_F9` (0x78) | Letter/function keys |

The frontend maps `KeyboardEvent.key` / `KeyboardEvent.code` to these string IDs. Left/right variants are treated as equivalent (e.g. either LCtrl or RCtrl satisfies `"ctrl"`).

#### Backend

**API approach:** Hotkey config is read via the existing `get_settings()` (which already returns all settings including `hotkey_keys`). A dedicated `save_hotkey_config(keys)` endpoint handles writes because it must also restart the listener.

- `save_hotkey_config(keys)` → validates, persists to settings.json under `"hotkey_keys"`, restarts the hotkey listener
- `get_hotkey_config()` → convenience method returning `{ "keys": ["win", "ctrl"], "display": "Win + Ctrl" }` for UI components that need just the hotkey info
- Default value: `["win", "ctrl"]`

**Fallback on invalid config:** If `settings.json` contains an invalid or unrecognized `hotkey_keys` value (e.g. from manual editing), fall back to the default `["win", "ctrl"]` and log a warning.

**Settings storage** (`~/.waffler-hosted/settings.json`):

```json
{
  "hotkey_keys": ["win", "ctrl"],
  ...existing settings...
}
```

#### Configurable WindowsHotkeyListener

**Constructor change:** `__init__(self, on_press, on_release, keys=None)` — `keys` defaults to `["win", "ctrl"]`. All 4 call sites in `app.py` must be updated:
1. `WafflerPipeline.start_hotkey()` (line ~1531)
2. `wizard_start_fn_detection()` (line ~731)
3. `wizard_start_hotkey_test()` (line ~782)
4. Wizard stop/restart paths

**Pipeline must store listener reference:** Currently `start_hotkey()` creates a local `hotkey` variable that blocks. The pipeline must store the listener as `self.hotkey_listener` so `save_hotkey_config()` can call `stop()` on it. The `Api` class needs access to this reference (via `_pipeline_ref` or similar).

**Key tracking:** The listener maintains a dict of `{key_id: bool}` for each configured key's pressed state. The hook callback checks VK codes against the mapping table to update state. The combo is active when all configured keys are held.

**Non-modifier keys in combo:** If the user configures e.g. `Ctrl + F9`, the non-modifier key participates identically to modifiers in the state dict — all configured keys must be held simultaneously. Key press order does not matter.

**Modifier suppression policy:**
- Win key-up: suppress only when Win is part of the configured combo and was used in an active combo press (prevents Start menu)
- Alt key-up: suppress only when Alt is part of the configured combo and was used in an active combo press (prevents window menu activation)
- Ctrl/Shift: no suppression needed (no OS-level side effects)

**Polling fallback:** The `_poll_fallback()` method must also be made configurable, using `GetAsyncKeyState` for whatever keys are configured instead of hardcoded Ctrl+Win.

**Space remains hardcoded** as the sticky-mode trigger (not user-configurable).

#### Hotkey Restart Flow

1. User saves new hotkey in popup
2. Frontend calls `save_hotkey_config(keys)`
3. Backend saves to settings.json
4. Backend calls `stop()` on current `WindowsHotkeyListener` (posts `WM_QUIT` to its message loop)
5. A new daemon thread is created (the old thread exits when its message loop ends)
6. Backend creates new `WindowsHotkeyListener` with updated keys on the new thread
7. Backend starts new listener (installs hook + enters message loop)
8. Effect is immediate — no app restart needed

**Busy state:** If a restart happens during transcription processing (`_busy=True`), the new listener inherits the busy state from the pipeline's current transcription state, not from the old listener.

#### UI Hints Update

All hardcoded hotkey display strings must read from the configured hotkey:
- **Sidebar hint** (`#hotkeyHint` element): `updateHotkeyHint()` in `app.js` must call `get_hotkey_config()` instead of hardcoding platform strings
- **Wizard hotkey badge** (`#wizHotkeyBadge`): already loads from API via `wizLoadHotkeyInfo()`, but `test_hotkey()` must return the configured display string
- **Wizard Try It badge** (`#wizTryHotkeyBadge`): same
- **`test_hotkey()` API** (line ~615 in `app.py`): must return the configured hotkey display string instead of hardcoded "Win + Ctrl"

#### Validation Rules

- At least one modifier key required (Ctrl, Alt, Shift, or Win) — keeps combos ergonomic and avoids accidental triggers
- Maximum 3 keys in the combination — prevents unwieldy combos
- Reject reserved system combos (Ctrl+Alt+Del, Alt+F4, Alt+Tab, Win alone)

## Feature 2: App Window Icon

### Current State

The pywebview window (`webview.create_window()` at line ~1730 in `app.py`) shows the default Python icon in the title bar and taskbar. No `icon` parameter is passed.

### Design

- Pass `icon="icon.ico"` to `webview.create_window()` — the file exists at project root (`C:\Users\james\waffler\icon.ico`)
- PyInstaller bundles this file already (it's referenced in the `.spec`/build config)
- For frozen builds, resolve the path relative to the executable directory

## Out of Scope

- Mac hotkey customization (Mac uses Fn key, different mechanism)
- Customizing the sticky-mode trigger key (Space stays hardcoded)
- Hotkey profiles or presets
- Per-application hotkey overrides
