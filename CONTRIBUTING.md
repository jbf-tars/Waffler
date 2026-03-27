# Contributing to Waffler

Thanks for your interest in contributing to Waffler! This guide will help you get started.

## Getting Started

### Prerequisites

- Python 3.11+
- An OpenAI or Groq API key
- **Mac:** macOS 12+ with Xcode command line tools
- **Windows:** Windows 10/11

### Development Setup

```bash
# Clone the repo
git clone https://github.com/jbf-tars/waffler.git
cd waffler

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt          # Mac
pip install -r requirements_windows.txt  # Windows

# Copy env and add your API key
cp .env.example .env

# Run
python app.py
```

### macOS Permissions

On Mac, you'll need to grant:
- **Accessibility** — System Settings > Privacy & Security > Accessibility
- **Input Monitoring** — System Settings > Privacy & Security > Input Monitoring
- **Microphone** — granted on first use

## How to Contribute

### Reporting Bugs

Open an issue using the **Bug Report** template. Include:
- Your OS and version
- Steps to reproduce
- Expected vs actual behaviour
- Logs from `~/.waffler-hosted/app.log` if relevant

### Suggesting Features

Open an issue using the **Feature Request** template. Describe the problem you're trying to solve, not just the solution you want.

### Submitting Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Test on your platform (Mac or Windows)
4. Open a PR with a clear description of what changed and why

### Code Style

- Python code follows standard PEP 8 conventions
- Keep functions focused and reasonably sized
- Use type hints where it helps readability
- Avoid bare `except:` — catch specific exceptions

### Project Structure

```
waffler/
  app.py              # Main entry point (pywebview UI + pipeline)
  src/                 # Core modules
    audio.py           # Audio recording
    audio_devices.py   # Device enumeration
    clipboard.py       # Clipboard management
    config.py          # Configuration loading
    fn_key_cgevent.py  # macOS Fn key detection
    overlay.py         # Recording overlay (subprocess manager)
    overlay_process.py # macOS overlay subprocess
    overlay_process_windows.py # Windows overlay subprocess
    permissions_manager.py     # macOS permission checks
    smart_hotkey.py    # macOS hotkey listener
    style_openai.py    # OpenAI text cleanup
    transcribe_whisper.py      # Whisper transcription
    windows_hotkey.py  # Windows hotkey listener
  ui/                  # Web UI (HTML/CSS/JS served by pywebview)
  tests/               # Test suite
  prompts/             # System prompts for text cleanup
  hooks/               # PyInstaller runtime hooks
  .github/workflows/   # CI/CD (Mac + Windows release builds)
```

## Running Tests

```bash
python -m pytest tests/
```

## Building

See the [README](README.md#building-from-source) for build instructions.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
