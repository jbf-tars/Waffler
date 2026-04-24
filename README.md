# Waffler

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![macOS](https://img.shields.io/badge/platform-macOS-blue)](https://github.com/jbf-tars/waffler/releases)
[![Windows](https://img.shields.io/badge/platform-Windows-blue)](https://github.com/jbf-tars/waffler/releases)

**Free, open-source voice-to-text for Mac and Windows. Bring your own API key.**

Press a hotkey, speak, release — your polished text is in the clipboard, ready to paste anywhere.

> A free alternative to Wispr Flow / Superwhisper. No account, no subscription, no telemetry.


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

> **Note:** macOS builds are signed with an Apple Developer ID and notarised — they open without Gatekeeper warnings. Windows builds are not yet signed; SmartScreen will warn on first launch — click "More info > Run anyway".

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

# Mac:
pip install -r requirements.txt
# Windows:
pip install -r requirements_windows.txt

python app.py
```

---

## Usage

**Push-to-talk (default)**
1. **Hold** the Fn key (Mac) or `Win + Ctrl` (Windows)
2. **Speak**
3. **Release** — text is transcribed, cleaned up, and copied to clipboard
4. **Paste** anywhere

**Hands-free (sticky) mode**
- While holding the hotkey, tap `Space` to lock recording on — you can release the hotkey and keep talking.
- Press the hotkey again (Fn on Mac, `Win + Ctrl` on Windows) to stop and send.

**Cancel a recording**
- Click the **×** button on the floating recording overlay to discard the current take without transcribing or touching the clipboard. Works in both push-to-talk and hands-free modes.

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
| STT | OpenAI Whisper API or Groq Whisper |
| LLM cleanup | Groq (Llama 3.3 70B) or OpenAI (GPT-4o-mini) |
| Audio | sounddevice + NumPy |
| UI | pywebview (WebView2 on Windows, WebKit on Mac) |
| Hotkey | CGEventTap (Mac) / Win32 low-level keyboard hook via ctypes (Windows) |
| Menubar / Tray | rumps (Mac) / pystray (Windows) |
| Clipboard | pyperclip |
| Packaging | PyInstaller + Inno Setup (Win) / hdiutil (Mac) |

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
