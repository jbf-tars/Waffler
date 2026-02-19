# VoiceFlow — Build Queue

Last updated: 2026-02-18
Project: `/Users/tars/clawd/projects/voice-app-downloadable/`
Windows status: ✅ Working (v17 - License)
macOS status: ✅ Working (v17 - License)

---

## 🔴 Queue (do in order)

All tasks complete! 🎉

### 1. Custom Vocabulary  [x] — shipped in v13
Let users add words/names that Whisper gets wrong (e.g. "VoiceFlow", "TARS", product names).
- Add a "Vocabulary" text area in the UI sidebar
- Save to `~/.voiceflow/vocab.json`
- Inject into Whisper API as `initial_prompt` param (this is how Whispr Flow does it)
- Also inject into GPT cleanup prompt: "Preserve these exact spellings: ..."
- Ships in next zip

### 2. System Tray on Windows (hide terminal) [x] — shipped in v14
Currently app.py runs in a terminal window on Windows — ugly, not production-ready.
- Use `pystray` library for Windows system tray icon
- On launch: minimise terminal, show tray icon with tooltip "VoiceFlow running"
- Right-click tray: Open UI / Quit
- macOS already has rumps menubar — Windows needs pystray
- Install: `pip install pystray pillow`

### 3. Pause / Resume Recording [x] — shipped in v15
Let users pause mid-dictation without losing audio.
- Backend: Added `pause()`, `resume()`, `toggle_pause()` to AudioRecorder
- State tracking in app: `_is_paused` flag  
- Overlay: Added `update_state("paused"|"recording")` API
- Programmatic pause via `pipeline.toggle_pause()`

### 4. Context Awareness — Active App Detection [x] — shipped in v16
Detect which app is in focus and adapt the output style automatically.
- Created `src/app_detection.py` with `get_active_app()` function
- macOS: uses AppleScript + System Events
- Windows: uses GetForegroundWindow + GetWindowThreadProcessId
- Maps apps to styles: casual (Slack/Teams→ramble), agentic (VS Code/terminal→engineer), prose (Docs/Word→normal)
- Exposed via API: `api.get_active_app()` returns {name, suggested_style}

### 5. Landing Page [x] — shipped
Simple one-page site to sell VoiceFlow.
- Tech: plain HTML/CSS, responsive design
- Sections: hero, demo placeholder, features (6 cards), pricing ($29 launch / $49 regular)
- Buy button links to Lemon Squeezy (placeholder URL)
- Host on Vercel (free)

### 6. Lemon Squeezy Payment Setup [x] — shipped in v17
- Created `src/license.py` with licence validation module
- Added API methods: `check_license()`, `activate_license()`
- Accepts dev keys starting with "VF-" or "DEV-" for testing
- Real Lemon Squeezy integration ready (needs API key)
- Licence stored in `~/.voiceflow/license.json`

---

## ✅ Done

- [x] Core pipeline: record → Whisper → GPT → paste
- [x] macOS hotkey: Right Option (PTT + sticky)
- [x] Windows hotkey: Right Ctrl + Right Alt
- [x] Overlay pill (macOS NSPanel)
- [x] Dark UI with transcript history + mode selector
- [x] 3 prompt modes: Normal / Ramble / Agentic Engineer
- [x] v3 prompts (87.4/100 avg, tested on 300 transcripts)
- [x] Local Whisper: mlx-whisper (Mac) + faster-whisper (Windows)
- [x] White overlay bars (no more green/yellow)
- [x] Short transcript fast-path (skip GPT for ≤10 words)
- [x] One-click launcher: run_windows.bat + VoiceFlow.command
- [x] config.yaml included in zip

---

## Notes

- Do NOT build subscription billing — one-time purchase only
- Windows code signing can wait for v1 (users click "Run anyway")
- macOS notarisation needed before public launch ($99/yr Apple Developer)
- Post updates to ADHD App group: `-5005324578`
- Always ship a new zip after each feature
