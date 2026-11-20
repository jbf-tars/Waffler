#!/bin/sh
# Builds the system-audio helper into mac/build/natter-sysaudio (universal).
set -e
cd "$(dirname "$0")"
mkdir -p build
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos13.0" -o "build/natter-sysaudio-$arch" sysaudio/main.swift
done
lipo -create -output build/natter-sysaudio build/natter-sysaudio-arm64 build/natter-sysaudio-x86_64
rm build/natter-sysaudio-arm64 build/natter-sysaudio-x86_64
# Ad-hoc sign for local runs; build_mac.sh re-signs with the real identity.
codesign --force --sign - build/natter-sysaudio
echo "built mac/build/natter-sysaudio"
