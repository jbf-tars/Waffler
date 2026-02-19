#!/bin/bash
# Install local Whisper for on-device transcription (no internet needed after setup).
#
# What this does:
#   - Mac Apple Silicon (M1/M2/M3): installs mlx-whisper  → ~0.2-0.5s per clip
#   - Everything else (Intel Mac):  installs faster-whisper → ~0.5-2s on CPU
#
# After installing, add LOCAL_WHISPER=1 to your .env file.
# First run will download the Whisper "base" model (~150MB) — then it's offline.

ARCH=$(uname -m)
OS=$(uname -s)

if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
    echo "🍎 Apple Silicon detected — installing mlx-whisper..."
    pip install mlx-whisper
    echo ""
    echo "✅ Done! Add this to your .env:"
    echo "   LOCAL_WHISPER=1"
    echo ""
    echo "First dictation downloads the model (~150MB). After that: ~0.3s, no internet."
else
    echo "🖥  Intel Mac / Linux detected — installing faster-whisper..."
    pip install faster-whisper
    echo ""
    echo "✅ Done! Add this to your .env:"
    echo "   LOCAL_WHISPER=1"
    echo ""
    echo "First dictation downloads the model (~150MB). After that: ~1s on CPU."
fi
