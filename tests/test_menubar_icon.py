#!/usr/bin/env python3
"""Test menubar icon loading (macOS only).

``rumps`` is a macOS-only library — this file can't be collected by pytest
on non-Mac platforms without raising ImportError during discovery. The
``pytest.importorskip`` below makes collection succeed everywhere; the
file simply skips on Linux/Windows.
"""
import pytest

rumps = pytest.importorskip("rumps")

from pathlib import Path

# Test with the actual icon file from the built app
icon_path = "/Applications/Waffler.app/Contents/Resources/menubar_icon_template.png"

print(f"Icon path: {icon_path}")
print(f"Icon exists: {Path(icon_path).exists()}")

if Path(icon_path).exists():
    print(f"Icon size: {Path(icon_path).stat().st_size} bytes")

class TestApp(rumps.App):
    def __init__(self):
        super().__init__("Test", icon=icon_path, template=True)

    @rumps.clicked("Quit")
    def quit_app(self, _):
        rumps.quit_application()

if __name__ == "__main__":
    app = TestApp()
    print("Starting test app...")
    app.run()
