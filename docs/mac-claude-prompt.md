# Mac Claude Code Prompt

Copy and paste everything below the line into Claude Code on your Mac.

---

I'm continuing work on Waffler — a free, open-source, BYOK voice-to-text desktop app. The repo is at `~/waffler` (or wherever you cloned `https://github.com/jbf-tars/waffler.git`). Pull the latest from `main` first — the current version is v2.1.2.

Read `docs/mac-handoff.md` for full context on what was done in the Windows session and what needs doing on Mac.

Here's what I need you to do:

1. **Update the macOS GitHub Actions workflow** (`.github/workflows/macos-release.yml`):
   - Add `--collect-all pynput` to the PyInstaller command (pynput backends aren't auto-detected, this was already fixed on Windows)
   - Add `--add-data "icon_512.png:."` so the brand icon is bundled in the Mac build
   - Make sure `pynput` is in `requirements.txt` if it isn't already

2. **Hide the "Change Hotkey" button on Mac**: The hotkey customization feature works on Windows but the Fn key on Mac is hardware-level and not rebindable. In `ui/app.js`, detect macOS and either hide the "Change" button or replace it with text like "Fn (not customizable)". The hotkey settings section is in `ui/index.html` around line 159.

3. **Set up the Mac app icon**: The brand icon is `icon_512.png` — golden waffle with sound wave/syrup pattern. Convert it to `.icns` for the Mac app bundle and wire it into the PyInstaller build.

4. **Build and test locally** if possible: `pip install -r requirements.txt && python app.py` — verify Fn key push-to-talk, overlay, tray icon all work.

5. **Commit, push, and tag as v2.1.3** when Mac fixes are done.

Don't touch the Windows-specific code (`src/windows_hotkey.py`, `src/overlay_process_windows.py`). The Mac hotkey listener is `src/smart_hotkey.py`.
