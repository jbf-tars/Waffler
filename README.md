# Waffler

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![macOS](https://img.shields.io/badge/platform-macOS-blue)](https://github.com/jbf-tars/Waffler/releases)
[![Windows](https://img.shields.io/badge/platform-Windows-blue)](https://github.com/jbf-tars/Waffler/releases)

**Free, open-source voice-to-text for Mac and Windows. Bring your own API key.**

Press a hotkey, speak, release — your polished text is in the clipboard, ready to paste anywhere.

> A free alternative to Wispr Flow / Superwhisper. No account, no subscription, no telemetry.

**Website:** [wafflerai.com](https://wafflerai.com)  ·  **Latest release:** [GitHub Releases](https://github.com/jbf-tars/Waffler/releases/latest)


---

## About this project

Waffler is a side project — and, if I'm honest, my first proper app. I built it for two reasons:

1. **I was knackered of paying £10 a month just to talk at my laptop.** Wispr Flow, Superwhisper, the rest — they're great, but they lock you into someone else's pricing, someone else's servers, and someone else's decisions about what happens to your audio. With Waffler you bring your own OpenAI or Groq key, the audio goes straight from your machine to the provider, and the whole thing costs pennies a month for normal use. No middleman.

2. **I wanted to see how far agentic coding tools could actually take a real app.** Waffler was built almost entirely by dictating into Waffler — using Claude Code to do the actual writing, with me voicing the intent, reviewing diffs, and steering. The prompts, the bug fixes, even a lot of this README were dictated into the app while it was being built. It's properly meta and it genuinely shaped how I think about building software now.

It's gone through a ridiculous amount of prompt-wrangling and live-API testing to get the transcriptions sounding like a normal human rather than a LinkedIn post. That work is still ongoing — it'll still get things wrong — but the plumbing is solid.

**Early days, definitely.** Rough edges, opinionated defaults, probably bugs I haven't spotted. If you find one or want a feature, please open an [issue](https://github.com/jbf-tars/Waffler/issues) — I actually read them, and this is the kind of project where user feedback steers the roadmap directly. Cheers.

— James


---

## Features

- **Global hotkey** — works in any app, instantly
- **Multi-provider transcription** — Groq Whisper (fastest) or OpenAI Whisper
- **Multi-provider AI cleanup** — Cerebras Qwen 235B (default, fastest), Groq Llama 3.3 70B, or OpenAI GPT-4.1-mini, with automatic fallback if a provider rate-limits
- **Smart hallucination filtering** — strips Whisper's "and more / thanks for watching / please subscribe" outros on near-silent clips before they hit your clipboard
- **Auto-clipboard** — result is copied the moment it's ready
- **Local transcription history** — searchable, stays on your machine
- **Recording overlay** — floating waffle indicator that lives over any app, including full-screen
- **Setup wizard** — guided onboarding for first-time users
- **In-app auto-update** — checks GitHub Releases and installs the new build with one click
- **Custom vocabulary** — teach Waffler your jargon, names, and acronyms
- **Mac + Windows** — native desktop app via PyInstaller

---

## Quick Start

### 1. Install

Download the latest installer from [GitHub Releases](https://github.com/jbf-tars/Waffler/releases/latest):

- **Windows:** `Waffler-Setup-<version>.exe`
- **Mac:** `Waffler-<version>-mac.dmg`

> **Note:** macOS builds are signed with an Apple Developer ID and notarised — they open without Gatekeeper warnings. Windows builds are not yet signed; SmartScreen will warn on first launch — click "More info > Run anyway".

### 2. Add your API key

On first launch, the setup wizard will ask for your key. Any one of the three providers below is enough — Waffler will use whatever you give it.

```bash
# Groq — fastest transcription AND cleanup, generous free tier (recommended)
GROQ_API_KEY=your_groq_api_key_here

# Cerebras — fastest cleanup in the world (~2200+ tok/s), free tier available
CEREBRAS_API_KEY=your_cerebras_api_key_here

# OpenAI — most reliable, cheapest at low volume
OPENAI_API_KEY=your_openai_api_key_here
```

You can set more than one key — Waffler will use the fastest available provider and fall back automatically if any single one rate-limits or errors.

Where to get each one (all free to sign up):
- **Groq:** <https://console.groq.com/keys>
- **Cerebras:** <https://cloud.cerebras.ai>
- **OpenAI:** <https://platform.openai.com/api-keys>

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
- Press **Esc** any time while recording to discard the current take without transcribing or touching the clipboard. Works in both push-to-talk and hands-free modes.
- Or click the **×** button on the floating recording overlay.

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
| Language | Python 3.11 |
| Speech-to-text | Groq Whisper (default) or OpenAI Whisper (`gpt-4o-mini-transcribe`) |
| LLM cleanup | Cerebras Qwen-3 235B (default) → Groq Llama 3.3 70B → OpenAI GPT-4.1-mini, with automatic fallback |
| Audio | sounddevice + NumPy |
| UI | pywebview (WebView2 on Windows, WebKit on Mac) |
| Hotkey (Mac) | Single HID-level CGEventTap with multi-handler dispatch + 150 ms hold-quiet trailing edge on Fn |
| Hotkey (Windows) | Win32 low-level keyboard hook via ctypes |
| Menubar / Tray | rumps (Mac) / pystray (Windows) |
| Clipboard | pyperclip |
| Auto-update | Native `curl` (Mac) / `requests` (Windows) → `.dmg` swap or Inno Setup silent upgrade |
| Packaging | PyInstaller + Inno Setup (Win) / hdiutil + codesign + notarytool (Mac) |

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

- **Accessibility / Input Monitoring (Mac):** Required for keyboard monitoring. Granted via System Settings → Privacy & Security → Accessibility *and* Input Monitoring. The wizard walks you through it on first launch.
- **Microphone permission:** Required for recording. Granted on first run via the standard macOS / Windows prompt.
- **Windows SmartScreen warning:** Builds are not yet code-signed on Windows; SmartScreen will say "Windows protected your PC" on first launch. Click "More info" → "Run anyway". (Mac builds *are* signed and notarised — no Gatekeeper warning.)
- **Rate limits:** Free tiers on Groq / Cerebras / OpenAI have daily / per-minute caps. Waffler falls back across providers automatically, but if all three hit limits in the same window you'll see a "Rate limit reached" toast. Set multiple keys to maximise headroom.

---

## License

[MIT](LICENSE) — free to use, modify, and distribute.

---

## Security

Found a vulnerability? Please report it responsibly. See [SECURITY.md](SECURITY.md).
