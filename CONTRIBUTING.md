# Contributing to Waffler

Thanks for your interest in contributing to Waffler! This guide will help you get started.

## Getting Started

### Prerequisites

- Python 3.11+
- At least one API key from Groq, Cerebras, or OpenAI
  - **Groq** (free tier, recommended): <https://console.groq.com/keys>
  - **Cerebras** (free tier + paid): <https://cloud.cerebras.ai>
  - **OpenAI** (paid): <https://platform.openai.com/api-keys>
- **Mac:** macOS 12+ with Xcode command line tools
- **Windows:** Windows 10/11

### Development Setup

```bash
# Clone the repo
git clone https://github.com/jbf-tars/Waffler.git
cd Waffler

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt          # Mac
pip install -r requirements_windows.txt  # Windows

# Copy env template and add your API key(s)
cp .env.example .env
$EDITOR .env   # paste at least one of GROQ / CEREBRAS / OPENAI keys

# Run from source
python app.py
```

### macOS Permissions

On Mac, you'll need to grant:
- **Accessibility** — System Settings > Privacy & Security > Accessibility
- **Input Monitoring** — System Settings > Privacy & Security > Input Monitoring
- **Microphone** — granted on first use via the standard macOS prompt

The setup wizard walks you through each of these on first launch.

## How to Contribute

### Reporting Bugs

Open an issue using the **Bug Report** template. Include:
- Your OS and version
- Waffler version (visible in Settings → About, or in the log header: `=== Waffler starting === (vX.Y.Z, ...)`)
- Steps to reproduce
- Expected vs actual behaviour
- Logs from `~/.waffler-hosted/app.log` if relevant
- For crashes: also attach `~/Library/Logs/DiagnosticReports/Waffler-*.ips` (macOS) or the equivalent on Windows

### Suggesting Features

Open an issue using the **Feature Request** template. Describe the problem you're trying to solve, not just the solution you want.

### Submitting Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Test on your platform (Mac or Windows) — see [Running Tests](#running-tests)
4. If your change touches `prompts/normal.txt` or the styler, run the prompt-regression harness too (see below)
5. Open a PR with a clear description of what changed and why

### Code Style

- Python code follows standard PEP 8 conventions
- Keep functions focused and reasonably sized
- Use type hints where it helps readability
- Avoid bare `except:` — catch specific exceptions
- Comments explain *why*, not *what*. A non-obvious "this exists because…" comment is worth more than ten "increment counter"-style ones

## Running Tests

Most test files are stand-alone scripts (not pytest-only), so run them with plain Python:

```bash
# Unit tests — fast, no API key needed, runs in CI on every push:
python tests/test_strip_hallucinations.py        # YouTube-outro hallucination filter
python tests/test_and_more_hallucination.py      # v3.14.39 "and more" Whisper filter
python tests/test_fn_handler_chatter.py          # v3.14.33 macOS Fn hold-quiet state machine

# Prompt-regression harness — talks to real APIs, needs GROQ/CEREBRAS/OPENAI keys
# in ~/.waffler-hosted/.env. ~5–30s per case depending on provider.
python scripts/auto_test_corpus.py                                   # full 101-case corpus
python scripts/auto_test_corpus.py --filter FT                       # the 5 v3.14.38 anti-abridgement cases (FT1–FT5) + 2 incidental "ft"-substring matches
python scripts/auto_test_corpus.py --filter SOLO-NUM                 # the 5 solo-number-not-list cases
python scripts/auto_test_corpus.py --category hallucination-bait     # the 5 hallucination-bait cases (H-prefix)
python scripts/auto_test_corpus.py --category email                  # the 33 email-mode cases
```

If you edit `prompts/normal.txt`, run the full corpus and check that every previously-passing case still passes before opening a PR — the corpus exists because every "NEVER" rule in the prompt has shipped a bug in production at some point.

## Project Structure

```
Waffler/
  app.py                          # Main entry point (pywebview UI + pipeline)
  src/                            # Core modules
    audio.py                      # sounddevice-based audio capture
    audio_devices.py              # Mic device enumeration / selection
    app_detection.py              # Which app the user is dictating into
    clipboard.py                  # Cross-platform clipboard
    config.py                     # Config + .env loading
    log_util.py                   # Shared file-logger
    mac_hotkey_monitor.py         # macOS event tap + hotkey handlers (single CGEventTap)
    smart_hotkey.py               # macOS hotkey dispatcher (wraps the event tap)
    fn_key_cgevent.py             # Backward-compat shim (deprecated; new code uses mac_hotkey_monitor)
    windows_hotkey.py             # Windows low-level keyboard hook
    overlay.py                    # Overlay subprocess manager (parent-side)
    overlay_process.py            # macOS overlay subprocess (pywebview-free PyObjC window)
    overlay_process_windows.py    # Windows overlay subprocess
    permissions_manager.py        # macOS Accessibility / Input Monitoring checks
    style_openai.py               # 3-provider styler chain (Groq → Cerebras → OpenAI)
    transcribe_whisper.py         # 3-tier transcriber chain (Groq → local → OpenAI)
    updater.py                    # In-app auto-updater (DMG / Inno Setup)
  ui/                             # Web UI (HTML/CSS/JS served by pywebview)
  tests/                          # Unit tests (mostly run as plain scripts)
  scripts/                        # Tooling (corpus harness, vocab corpus, etc.)
  prompts/                        # System prompts for text cleanup
    normal.txt                    # Active mode — the big prompt
    email.txt                     # Hidden since v3.14.6, retained as a draft
  hooks/                          # PyInstaller runtime hooks
  .github/workflows/              # CI/CD (Mac + Windows release builds)
    ci.yml                        # Per-push lint + unit tests + CHANGELOG check
    macos-release.yml             # Triggered on v* tag — builds + signs + notarises DMG
    windows-release.yml           # Triggered on v* tag — builds Inno Setup EXE
```

## Building

See the [README](README.md#building-from-source) for build instructions. The short version: push a `v*` tag and CI builds + uploads release assets for both platforms automatically.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
