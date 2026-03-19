# Waffler

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Free, open-source voice-to-text for Mac and Windows. Bring your own API key.**

Press a hotkey, speak, release — your polished text is in the clipboard, ready to paste anywhere.

> Built as a free alternative to Whisper Flow / Superwhisper.

---

## Features

- **Global hotkey** — works in any app, instantly
- **OpenAI Whisper or Groq** — your choice of transcription backend
- **AI cleanup** — removes filler words, fixes grammar, polishes output
- **Auto-clipboard** — result is copied the moment it's ready
- **Local transcription history** — searchable, stays on your machine
- **Mac + Windows** — native desktop app via PyInstaller

---

## Quick Start

### 1. Install

Download the latest installer from [GitHub Releases](https://github.com/jbf-tars/waffler/releases):

- **Windows:** `WafflerSetup-*.exe`
- **Mac:** `VoiceFlow-*-mac-unsigned.dmg`

> **Note:** Builds are unsigned. On Windows, click "More info → Run anyway" to bypass SmartScreen. On Mac, right-click → Open to bypass Gatekeeper. This is normal for an indie open-source project.

### 2. Add your API key

Copy `.env.example` to `.env` and add your key:

```bash
# OpenAI (transcription via Whisper + GPT-4o-mini for cleanup)
OPENAI_API_KEY=your_openai_api_key_here

# OR Groq (faster, often cheaper)
# GROQ_API_KEY=your_groq_api_key_here
```

### 3. Run (from source)

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

---

## Usage

1. **Hold** `Ctrl+Win` (Windows) or your configured hotkey
2. **Speak**
3. **Release** — text is transcribed, cleaned up, and copied to clipboard
4. **Paste** anywhere

---

## Privacy

- Your audio goes directly to OpenAI/Groq via **your own API key** — never through any Waffler servers
- Transcription history is saved **locally on your machine only**
- No account required, no telemetry

---

## Configuration

Edit `config.yaml` to customise hotkey, audio settings, STT model, and more.

Edit `.env` to set your API key(s).

---

## Tech Stack

- **Language:** Python 3.11+
- **STT:** OpenAI Whisper API or Groq
- **LLM:** GPT-4o-mini (OpenAI) or Groq
- **Audio:** sounddevice + NumPy
- **UI:** pywebview
- **Hotkey:** pynput + platform-specific modules
- **Clipboard:** pyperclip
- **Packaging:** PyInstaller + Inno Setup (Windows) / create-dmg (Mac)

---

## Building from Source

**Windows:**
```bash
pyinstaller Waffler_windows.spec
```

**Mac:**
```bash
pyinstaller Waffler_mac.spec
```

Or push a `v*` tag to trigger GitHub Actions builds for both platforms automatically.

---

## Known Issues

- **Accessibility permissions (Mac):** Required for keyboard monitoring — grant in System Settings → Privacy & Security → Accessibility
- **Unsigned builds:** SmartScreen (Windows) and Gatekeeper (Mac) will warn on first launch — this is expected

---

## License

[MIT](LICENSE) — free to use, modify, and distribute.

---

## Credits

Built by James as a free alternative to Whisper Flow.
