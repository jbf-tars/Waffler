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
- **AI cleanup with automatic fallback.** Groq gpt-oss-120b first, OpenAI GPT-4.1-mini as backup. Groq covers both transcription and cleanup, so one key is enough to run the whole app.
- **Smart hallucination filtering** — strips Whisper's "and more / thanks for watching / please subscribe" outros on near-silent clips before they hit your clipboard
- **Auto-clipboard** — result is copied the moment it's ready
- **Paste that keeps your clipboard (Mac).** Waffler puts back whatever you had copied once it has pasted, waits for your hotkey's keys to come up first, works on Dvorak and Colemak, and says so if the paste didn't land (your words stay on the clipboard). Paste last or copy last from the menu bar. Settings can keep the dictation on the clipboard instead.
- **Permission check (Mac).** If the microphone or Accessibility is switched off, or Waffler isn't receiving your keyboard, one message opens the exact System Settings pane.
- **Optional sounds and a quiet "Don't tap, hold" hint.** A soft tone when recording starts and stops and when hands-free locks (off until you switch it on; the microphone is silenced while one sounds, so it is never transcribed), and a once-in-a-while tip, with your own hotkey, if you tap instead of holding. Every message goes through one governor, so tips never talk over an error.
- **Local transcription history** — searchable, stays on your machine. Delete any entry (with an undo), redo a take from its kept recording, export as JSON, CSV or plain text, correct a dictation with Edit text (Copy and Paste last then use your version), and a take that a crash cut short comes back as a card you can try again
- **Recording overlay** — floating waffle indicator that lives over any app, including full-screen
- **Setup wizard** — guided onboarding for first-time users
- **In-app auto-update** — checks GitHub Releases and installs the new build with one click
- **Custom vocabulary** — teach Waffler your jargon, names, and acronyms. Say what speech to text writes for each ("grok" for Groq), search and sort the list, star the words that matter most, select many to remove or star, and import or export it as a CSV. Correct a dictation in the Journal and Waffler can offer to add the name it got wrong (it only offers; nothing is added by itself).
- **Snippets.** Say a short phrase, paste longer text: your address, a sign-off, a link. Any capitals, longest phrase first, and saying only the phrase skips the clean-up. Import and export as CSV or JSON.
- **Your language.** Dictate in 46 languages, or let Waffler detect which. The clean-up keeps what you spoke and never translates it.
- **Style for each kind of app.** Turn the clean-up off to get exactly what was heard, or give personal messages, work chat, email and everything else their own style (Formal, Casual, Very casual, Excited), and an optional email signature. The styles are plain rules, not a model, so they never change a word and work on short dictations too.
- **Transforms (beta).** Select text in any app, press a shortcut, and it is rewritten in place: Polish with rules you choose, Prompt Engineer for turning a rough request into a structured prompt, and up to five transforms of your own. Your app's Undo takes it back, and the Journal keeps the text before and after.
- **Command mode (beta).** Hold a shortcut and say what you want: edit the selected text ("make this shorter", "translate to French"), write new text where your cursor is ("reply saying I'll be 10 minutes late"), or ask a question and Waffler opens it in your search engine (Perplexity, Google, DuckDuckGo, ChatGPT or Claude). Your app's Undo takes an edit back, and the Journal keeps what you said.
- **Every shortcut in one place.** Settings, Shortcuts lists Hold to dictate, Hands-free, Cancel, Transform, Command mode, Paste last and Copy last, each with its keys, Change, Clear and a plain sentence if it clashes with another. Choose keys for Paste last and Copy last, an F key as your cancel key, or (Mac) two modifier keys held together for Command mode. The key of a shortcut never reaches the app in front, and combinations your computer or most apps use are refused with the reason.
- **Your week at a glance.** Settings, Usage shows your week streak, this week against last, words a minute, an estimate of the minutes saved (from your own typing speed), your busiest hour, the apps you dictate into most, and a map of the last 26 weeks. All of it is counted on your computer from your own history, with no accounts, rankings or uploads.
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

# Cerebras (optional, cleanup only, no speech-to-text)
# CEREBRAS_API_KEY=your_cerebras_api_key_here

# OpenAI — most reliable, cheapest at low volume
OPENAI_API_KEY=your_openai_api_key_here
```

You can set more than one key — Waffler will use the fastest available provider and fall back automatically if any single one rate-limits or errors.

Where to get each one (all free to sign up):
- **Groq:** <https://console.groq.com/keys>
- **Cerebras (optional):** <https://cloud.cerebras.ai>
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

**Paste again**
- Click the waffle in the menu bar (Mac) or the tray (Windows) and choose **Paste last transcript** or **Copy last transcript**. The menu says how many words, never what they are.
- Or give them shortcuts in Settings, Shortcuts (none until you choose). Paste last pastes when you let go of the keys.

**Cancel a recording**
- Press **Esc** any time while recording to discard the current take without transcribing or touching the clipboard. Works in both push-to-talk and hands-free modes. Settings, Shortcuts can change Esc to an F key.
- Or click the **×** button on the floating recording overlay.

---

## Privacy

- Audio goes directly to OpenAI/Groq via **your own API key** — never through any Waffler servers
- Transcription history is saved **locally on your machine only**. If you correct a dictation in the Journal, your corrected text is kept with it, on your machine only, and goes when the history does
- Vocabulary and Snippets exports are files you save to your Desktop yourself: they are your own copies, never sent anywhere, and Delete all my data leaves them alone
- On a Mac, to confirm a paste landed, Waffler reads whether the text field you paste into changed. It never stores or logs what is in it
- Each history entry notes the app you dictated into and its kind (email, work chat, personal chat or other), on your machine only and never sent to a provider. For a browser tab, Waffler may read the window title to tell webmail or chat from other pages; the title is not stored or logged, and the page address is never read
- If you turn on an email signature, then on a Mac Waffler reads the text before your cursor in an email app to check the signature isn't already there. It never stores or logs it, and the signature text is left out of the logs zip
- Transforms is off until you turn it on. When you use it, Waffler reads the text you selected (through Accessibility, or by copying it and putting your clipboard back) and sends it to your clean-up provider on your own key. The rewrite is kept in your history, and your own rules and transforms are left out of the logs zip
- Command mode is off until you turn it on. While you hold its shortcut, Waffler records what you say, your speech provider turns it into text, and that, with the text you selected (or up to 500 characters before your cursor when nothing is selected), goes to your clean-up provider on your own key. A question opens your browser with only your spoken question in the address. The recording is not kept
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
| LLM cleanup | Groq gpt-oss-120b → OpenAI GPT-4.1-mini (Groq is faster and has a free tier; OpenAI is the backstop). Cerebras is still supported if a key is set, but it has no speech-to-text so it can never run a dictation alone |
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
- **Rate limits:** Groq's free tier has daily and per-minute caps. Waffler falls back to OpenAI automatically, but if both hit limits in the same window you'll see a "Rate limit reached" toast. Set multiple keys to maximise headroom.

---

## License

[MIT](LICENSE) — free to use, modify, and distribute.

---

## Security

Found a vulnerability? Please report it responsibly. See [SECURITY.md](SECURITY.md).
