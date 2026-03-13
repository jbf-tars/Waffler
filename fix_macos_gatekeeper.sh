#!/bin/bash
set -euo pipefail

# One-time Gatekeeper unblock for Waffler folder
TARGET_DIR="${1:-$(pwd)}"

if [ ! -d "$TARGET_DIR" ]; then
  echo "❌ Directory not found: $TARGET_DIR"
  echo "Usage: ./fix_macos_gatekeeper.sh /path/to/Waffler-folder"
  exit 1
fi

echo "🔓 Removing quarantine flags in: $TARGET_DIR"
xattr -dr com.apple.quarantine "$TARGET_DIR" || true

echo "✅ Making launcher executable"
chmod +x "$TARGET_DIR"/Waffler.command 2>/dev/null || true

echo "🧼 Clearing Gatekeeper assessment cache"
spctl --assess --type execute "$TARGET_DIR"/Waffler.command >/dev/null 2>&1 || true

echo "✅ Done. Try launching Waffler.command again."
echo "If macOS still blocks it once, run this manually one time:"
echo "  open -a Terminal \"$TARGET_DIR/Waffler.command\""
