#!/bin/bash
# VoiceFlow launcher
cd "$(dirname "$0")"

echo "🎙️ Starting VoiceFlow..."

# Find python - try a few options
for py in python3 /usr/bin/python3 /opt/homebrew/bin/python3 python; do
  if command -v $py &> /dev/null; then
    PYTHON=$py
    break
  fi
done

echo "Using: $PYTHON"

# Install deps if needed
echo "Checking dependencies..."
$PYTHON -m pip install pywebview openai sounddevice numpy pynput pyperclip pyyaml python-dotenv --break-system-packages -q 2>/dev/null

# Launch
exec $PYTHON app.py
