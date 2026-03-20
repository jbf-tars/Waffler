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

A new **"Hotkey"** section in the Settings panel, positioned between API Keys and Preferences:

- Section header: keyboard icon + "Hotkey" label (uppercase, small text, matching existing style)
- Row with dark background (`#22223a`, `border-radius: 10px`):
  - Left side: "Recording Hotkey" title + "Hold to record, release to stop" subtitle
  - Right side: Golden badge showing current binding (e.g. "Win + Ctrl") + "Change" button (golden `#C8A256` background)

#### Key Capture Popup

Clicking "Change" opens a modal overlay:

- Dark semi-transparent backdrop
- Centered modal card (`#22223a`, rounded corners, drop shadow)
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
- Require at least one modifier key (prevent binding to just a letter key)
- On "Save", send the key combination to the backend API

#### Backend

**New API endpoints** (exposed via pywebview):

- `get_hotkey_config()` → returns `{ "keys": ["win", "ctrl"], "display": "Win + Ctrl" }`
- `save_hotkey_config(keys)` → validates, persists to settings.json under `"hotkey_keys"`, restarts the hotkey listener
- Default value: `["win", "ctrl"]`

**Settings storage** (`~/.waffler-hosted/settings.json`):

```json
{
  "hotkey_keys": ["win", "ctrl"],
  ...existing settings...
}
```

#### Configurable WindowsHotkeyListener

- Constructor accepts a `keys` parameter: list of virtual key code identifiers
- Separates keys into "modifier set" (the keys that must all be held for push-to-talk)
- Space remains hardcoded as the sticky-mode trigger (not user-configurable)
- The low-level hook callback (`_ll_keyboard_proc`) tracks press/release state for all configured keys instead of just Win+Ctrl
- Start menu suppression: only suppress Win key-up if Win is part of the configured combo

#### Hotkey Restart Flow

1. User saves new hotkey in popup
2. Frontend calls `save_hotkey_config(keys)`
3. Backend saves to settings.json
4. Backend calls `stop()` on current `WindowsHotkeyListener`
5. Backend creates new `WindowsHotkeyListener` with updated keys
6. Backend starts new listener on its dedicated thread
7. Effect is immediate — no app restart needed

#### Validation Rules

- At least one modifier key required (Ctrl, Alt, Shift, or Win)
- Maximum 3 keys in the combination
- Reject reserved system combos (Ctrl+Alt+Del, Alt+F4, Alt+Tab, Win alone)

## Feature 2: App Window Icon

### Current State

The pywebview window shows the default Python/tkinter icon in the title bar and taskbar.

### Design

- Use the existing Waffler icon asset (same as system tray icon) as the window icon
- Pass the `.ico` file path to pywebview's window creation
- The icon file is already bundled with the app (`src/icon.ico` or similar)

## Out of Scope

- Mac hotkey customization (Mac uses Fn key, different mechanism)
- Customizing the sticky-mode trigger key (Space stays hardcoded)
- Hotkey profiles or presets
- Per-application hotkey overrides
