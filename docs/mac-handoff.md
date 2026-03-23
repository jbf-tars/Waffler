# Mac Handoff — Waffler v2.1.0

This document provides full context for continuing Waffler development on macOS. It was written at the end of a Windows Claude Code session on 2026-03-23.

## What Was Just Done (Windows)

v2.1.0 was released with two new features:

### 1. Hotkey Customization
Users can rebind the recording hotkey (default: Win+Ctrl) via Settings:
- **Settings UI**: New "Hotkey" section between API Keys and Preferences with golden badge + "Change" button
- **Key Capture Modal**: Dark overlay modal with live key display, Cancel/Reset Default/Save buttons
- **Backend**: `get_hotkey_config()` / `save_hotkey_config()` API endpoints in `app.py`
- **Configurable listener**: `WindowsHotkeyListener` in `src/windows_hotkey.py` accepts `keys` parameter
- **Live restart**: Saving a new hotkey stops the old listener and starts a new one immediately

### 2. App Window Icon
Windows title bar now shows the Waffler icon (`icon.ico`) instead of the Python default. Uses Win32 `SendMessageW` with `WM_SETICON`.

## What Needs Doing on Mac

### A. Hide "Change" button on Mac (quick fix)
The hotkey customization UI shows on all platforms, but the backend blocks saves on Mac (Fn key is hardware-level, not rebindable). The "Change" button should be hidden or replaced with explanatory text on macOS.

**Files to touch:**
- `ui/app.js` — in `loadHotkeyConfig()` or on DOMContentLoaded, detect Mac and either:
  - Hide the "Change" button (`document.querySelector('.settings-btn.accent')`)
  - Or replace it with text like "Fn (not customizable)"
- `ui/index.html` — the hotkey settings section is at roughly line 159-171

### B. Mac app icon
The Windows icon fix uses Win32 API. On Mac, pywebview may accept an `icon` parameter or it may need a different approach (`.icns` file, or setting it via `NSApplication`). The icon file at project root is `icon.ico` (Windows format) — a `.icns` version may be needed for Mac.

**Current `create_window` call** is in `app.py` around line 1840. The Windows icon code is just below it (wrapped in `if _platform.system() == "Windows"`).

### C. Build and test the Mac release
- The macOS GitHub Actions workflow is at `.github/workflows/macos-release.yml`
- It triggers on `v*` tags (v2.1.0 tag already exists)
- Last successful Mac build was v2.0.8
- The v2.1.0 Mac build failed due to GitHub Actions artifact storage quota (not a code issue — quota should refresh within 6-12 hours)
- You can either wait for quota refresh and re-trigger, or build locally on the Mac

### D. Verify existing Mac functionality still works
- Fn key hold-to-record (push-to-talk)
- Fn + Space for sticky mode
- Setup wizard (3 steps: API Key, Hotkeys, Try It)
- Overlay (waffle toast icon)
- System tray / menu bar icon

## Project Architecture (Key Files)

| File | Purpose |
|------|---------|
| `app.py` | Main entry point. pywebview window, API class, pipeline, hotkey thread |
| `src/windows_hotkey.py` | Windows-only hotkey listener (Win32 keyboard hook) |
| `src/smart_hotkey.py` | Mac hotkey listener (pynput-based, Fn key) |
| `src/overlay_process_windows.py` | Windows overlay (tkinter waffle toast) |
| `src/overlay.py` | Overlay manager (launches subprocess) |
| `ui/index.html` | Main UI — sidebar, home, settings, vocabulary, wizard |
| `ui/app.js` | Frontend logic — all JS |
| `ui/style.css` | All styles |
| `config.yaml` | App config |
| `icon.ico` | Windows icon file |
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
- **Latest tag**: `v2.1.0`
- **Remote**: `https://github.com/jbf-tars/waffler.git`
- All changes are pushed to `origin/main`

## Active Plan

There is a plan file at `docs/superpowers/plans/2026-03-20-hotkey-customization.md` and a spec at `docs/superpowers/specs/2026-03-20-hotkey-customization-design.md`. Both are complete for the Windows implementation. The Mac tasks above are not yet planned.

## GitHub Actions Artifact Quota

The quota was full as of 2026-03-23. All old artifacts were deleted but GitHub recalculates quota every 6-12 hours. If builds still fail on artifact upload, wait and retry. The build itself succeeds — only the upload step fails.

## Website

- Repo: `waffler-website` (separate repo)
- Hosted on Cloudflare Pages
- Auto-deploy workflow was just added (`.github/workflows/deploy.yml`) but needs two GitHub secrets configured:
  - `CLOUDFLARE_API_TOKEN`
  - `CLOUDFLARE_ACCOUNT_ID`: `1c12b1f74b5e860c87818112c3322a5f`
- Manual deploy was run on 2026-03-23 to push latest changes live
