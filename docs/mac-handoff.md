# Mac Handoff — Waffler v2.1.2

This document provides full context for continuing Waffler development on macOS. Written at the end of a Windows Claude Code session on 2026-03-24.

## What Was Just Done (Windows, v2.1.2)

Three bug fixes released as v2.1.2:

1. **Restored brand icon** — System tray/taskbar now uses `icon_512.png` (golden waffle with syrup/sound wave pattern) instead of the old `icon.ico` (plain waffle grid). The `_set_window_icon` Win32 code was removed entirely. Tray icon resolution: project root → `sys._MEIPASS` → `_internal/` subfolder.

2. **Push-to-talk release glitch** — First press sometimes left overlay visible. Root cause: Win key suppression (`return 1` from low-level keyboard hook) caused Windows to skip the key-up event, leaving `_key_states["win"]` stuck True. Fix: `_reset_key_states()` polls actual hardware via `GetAsyncKeyState` after every release. Also refined suppression to only block Win key-down when it transitions from IDLE state.

3. **Overlay toast fix** — "Couldn't hear you" error popup now hides the waffle grid first via `_root.withdraw()`.

Earlier (v2.1.0-v2.1.1): Hotkey customization feature was implemented — users can rebind their recording hotkey via Settings.

## What Needs Doing on Mac

### A. Verify Mac build works with current code

The macOS GitHub Actions workflow (`.github/workflows/macos-release.yml`) needs updates:
- Add `--collect-all pynput` to the PyInstaller command (same fix as Windows — pynput backends aren't auto-detected)
- Add `--add-data "icon_512.png:."` so the brand icon is bundled
- The v2.1.2 tag push should have triggered a build, but artifact storage quota may still be an issue

### B. Hide "Change" button on Mac (quick fix)

The hotkey customization UI shows on all platforms, but the backend blocks saves on Mac (Fn key is hardware-level, not rebindable). The "Change" button should be hidden or replaced with explanatory text on macOS.

**Files to touch:**
- `ui/app.js` — in `loadHotkeyConfig()` or on DOMContentLoaded, detect Mac and either:
  - Hide the "Change" button (`.settings-btn.accent` in the hotkey section)
  - Or replace it with text like "Fn (not customizable)"
- `ui/index.html` — the hotkey settings section is around line 159-171

### C. Mac app icon

The brand icon is `icon_512.png` (golden waffle with sound wave/syrup pattern). For Mac:
- Convert to `.icns` format for the app bundle
- pywebview on Mac may accept an icon parameter, or it may need `NSApplication` setup
- The tray icon code in `app.py` already uses `icon_512.png` with PIL/pystray

### D. Verify existing Mac functionality

- Fn key hold-to-record (push-to-talk)
- Fn + Space for sticky mode
- Setup wizard (3 steps: API Key, Hotkeys, Try It)
- Overlay (waffle toast icon)
- System tray / menu bar icon
- Settings persistence

## Project Architecture (Key Files)

| File | Purpose |
|------|---------|
| `app.py` | Main entry point. pywebview window, API class, pipeline, hotkey thread |
| `src/windows_hotkey.py` | Windows-only hotkey listener (Win32 low-level keyboard hook) |
| `src/smart_hotkey.py` | Mac hotkey listener (pynput-based, Fn key) |
| `src/overlay_process_windows.py` | Windows overlay (tkinter waffle toast) |
| `src/overlay.py` | Overlay manager (launches subprocess) |
| `ui/index.html` | Main UI — sidebar, home, settings, vocabulary, wizard |
| `ui/app.js` | Frontend logic — all JS |
| `ui/style.css` | All styles |
| `config.yaml` | App config |
| `icon_512.png` | **Correct brand icon** — golden waffle with sound wave bars |
| `icon.ico` | Old/wrong icon — plain waffle grid, DO NOT USE for display |
| `.github/workflows/macos-release.yml` | Mac CI/CD build |
| `.github/workflows/windows-release.yml` | Windows CI/CD build |
| `installer/windows/Waffler.iss` | Inno Setup installer script |

## Settings & Data

- Settings stored at `~/.waffler-hosted/settings.json`
- Hotkey config key: `"hotkey_keys"` (array of string IDs like `["win", "ctrl"]`)
- History at `~/.waffler-hosted/history.json`
- Hotkey debug log at `~/.waffler-hosted/hotkey.log`

## Current Git State

- **Branch**: `main`
- **Latest tag**: `v2.1.2`
- **Remote**: `https://github.com/jbf-tars/waffler.git`
- All changes pushed to `origin/main`

## GitHub Actions Artifact Quota

Quota was full as of 2026-03-23. All old artifacts were deleted but GitHub recalculates every 6-12 hours. If builds fail on artifact upload, wait and retry — the build itself succeeds.

## Website

- Repo: `waffler-website` (separate repo, `jbf-tars/waffler-website`)
- Hosted on Cloudflare Pages at wafflerai.com
- Auto-deploy workflow added (`.github/workflows/deploy.yml`) — needs `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets configured
- Still has stale Pricing nav links and unused components to clean up (see main plan)

## Outstanding Plan Items

There's a broader plan at the Windows session covering post-release cleanup:
- Remove stale VoiceFlow references (rename scripts, delete internal docs)
- Clean website (remove Pricing nav links, delete unused components)
- Delete dead code (`src/waffler_auth.py` — from paid era)
- Security: Paperclip API keys in git history need `git filter-repo` cleanup before making repo fully public
