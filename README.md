# Waffler

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![macOS](https://img.shields.io/badge/platform-macOS-blue)](https://github.com/jbf-tars/waffler/releases)
[![Windows](https://img.shields.io/badge/platform-Windows-blue)](https://github.com/jbf-tars/waffler/releases)

**Free, open-source voice-to-text for Mac and Windows. Bring your own API key.**

Press a hotkey, speak, release — your polished text is in the clipboard, ready to paste anywhere.

> A free alternative to Wispr Flow / Superwhisper. No account, no subscription, no telemetry.

<!-- TODO: Add screenshot or demo GIF here -->
<!-- ![Waffler demo](docs/demo.gif) -->

---

## Features

- **Global hotkey** — works in any app, instantly
- **OpenAI Whisper or Groq** — your choice of transcription backend
- **AI cleanup** — removes filler words, fixes grammar, polishes output
- **Auto-clipboard** — result is copied the moment it's ready
- **Local transcription history** — searchable, stays on your machine
- **Recording overlay** — floating VU meter shows when you're recording
- **Setup wizard** — guided onboarding for first-time users
- **Mac + Windows** — native desktop app via PyInstaller

---

## Quick Start

### 1. Install

Download the latest installer from [GitHub Releases](https://github.com/jbf-tars/waffler/releases):

- **Windows:** `WafflerSetup-*.exe`
- **Mac:** `Waffler-*-mac.dmg`

> **Note:** Builds are currently unsigned. On Windows, click "More info > Run anyway" to bypass SmartScreen. On Mac, right-click > Open to bypass Gatekeeper. This is normal for indie open-source projects.

### 2. Add your API key

On first launch, the setup wizard will ask for your key. Or manually copy `.env.example` to `.env`:

```bash
# OpenAI (transcription via Whisper + GPT-4o-mini for cleanup)
OPENAI_API_KEY=your_openai_api_key_here

# OR Groq (faster, often cheaper)
# GROQ_API_KEY=your_groq_api_key_here
```

### 3. Run from source

```bash
git clone https://github.com/jbf-tars/waffler.git
cd waffler
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

---

## Usage

1. **Hold** the Fn key (Mac) or `Ctrl+Win` (Windows)
2. **Speak**
3. **Release** — text is transcribed, cleaned up, and copied to clipboard
4. **Paste** anywhere

Fn + Space locks recording on (hands-free). Press Fn again to stop.

---

## Privacy

- Audio goes directly to OpenAI/Groq via **your own API key** — never through any Waffler servers
- Transcription history is saved **locally on your machine only**
- No account required, no telemetry, no data collection

---

## Configuration

Edit `config.yaml` to customise hotkey, audio settings, and more.

Edit `.env` to set your API key(s).

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| STT | OpenAI Whisper API or Groq |
| LLM | GPT-4o-mini (OpenAI) or Groq |
| Audio | sounddevice + NumPy |
| UI | pywebview |
| Hotkey | pynput + platform-specific modules |
| Clipboard | pyperclip |
| Packaging | PyInstaller + Inno Setup (Win) / create-dmg (Mac) |

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

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:
- Reporting bugs
- Suggesting features
- Submitting pull requests

---

## Known Issues

- **Accessibility permissions (Mac):** Required for keyboard monitoring — grant in System Settings > Privacy & Security > Accessibility
- **Unsigned builds:** SmartScreen (Windows) and Gatekeeper (Mac) will warn on first launch — this is expected

---

## License

[MIT](LICENSE) — free to use, modify, and distribute.

---

## Security

Found a vulnerability? Please report it responsibly. See [SECURITY.md](SECURITY.md).
