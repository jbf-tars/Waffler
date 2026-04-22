# -*- mode: python ; coding: utf-8 -*-
"""
Waffler — macOS PyInstaller spec
Bundles all hidden imports, data files (ui/, prompts/, config.yaml, .env, src/)
Produces: dist/Waffler.app  (macOS .app bundle)

Run on a Mac with: pyinstaller Waffler_mac.spec
"""

import sys
import os

block_cipher = None
PROJECT_ROOT = os.path.abspath('.')

# Read __version__ from src/__init__.py so the bundle's Info.plist agrees
# with the in-app version checker. CI rewrites __init__.py from the git tag
# before this spec runs; local builds use whatever is checked in. Without
# this, CFBundleShortVersionString was frozen at 2.1.19 for every release.
import re as _re
_init_text = open(os.path.join(PROJECT_ROOT, 'src', '__init__.py')).read()
_m = _re.search(r'__version__\s*=\s*"([^"]+)"', _init_text)
_VERSION = _m.group(1) if _m else '0.0.0'
print(f"[spec] building Waffler.app version {_VERSION}")

a = Analysis(
    ['app.py'],
    pathex=[PROJECT_ROOT, os.path.join(PROJECT_ROOT, 'src')],
    binaries=[],
    datas=[
        ('ui', 'ui'),
        ('prompts', 'prompts'),
        ('src', 'src'),
        ('config.yaml', '.'),
        ('.env.example', '.'),
    ],
    hiddenimports=[
        # ── Audio ──
        'sounddevice',
        'numpy',
        '_sounddevice_data',
        # ── UI ──
        'webview',
        'webview.platforms.cocoa',
        # ── Clipboard ──
        'pyperclip',
        # ── HTTP / API ──
        'requests',
        'openai',
        'openai.resources',
        'openai._client',
        'groq',
        'groq.resources',
        'groq._client',
        'google.genai',
        'google.genai._api_client',
        'google.genai.models',
        'httpx',
        'httpcore',
        'anyio',
        'anyio._backends',
        'anyio._backends._asyncio',
        'sniffio',
        'certifi',
        'h11',
        'idna',
        'charset_normalizer',
        'urllib3',
        # ── Config ──
        'yaml',
        'dotenv',
        # ── macOS system tray ──
        'rumps',
        # ── macOS overlay (PyObjC) ──
        'objc',
        'AppKit',
        'Foundation',
        'Quartz',
        'PyObjCTools',
        # ── Standard library sometimes missed ──
        'ctypes',
        'json',
        're',
        'wave',
        'io',
        'hashlib',
        'pathlib',
        'queue',
        'math',
        'random',
        'asyncio',
        'tempfile',
        'subprocess',
        'webbrowser',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows-only packages — exclude on Mac
        'pystray',
        'pystray._win32',
        'ctypes.wintypes',
        'pythonnet',
        'clr',
        'windows_hotkey',
        'overlay_process_windows',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Waffler',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='icon.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Waffler',
)

app = BUNDLE(
    coll,
    name='Waffler.app',
    icon='icon.icns',
    bundle_identifier='com.waffler.app',
    info_plist={
        'CFBundleName': 'Waffler',
        'CFBundleDisplayName': 'Waffler',
        'CFBundleExecutable': 'Waffler',
        'NSMicrophoneUsageDescription': 'Waffler needs microphone access for voice transcription.',
        'NSAppleEventsUsageDescription': 'Waffler needs accessibility access for hotkey detection and auto-paste.',
        'NSLocalNetworkUsageDescription': 'Waffler uses a local web interface for its UI. No data is sent over the network.',
        'CFBundleShortVersionString': _VERSION,
        'LSMinimumSystemVersion': '10.13.0',
        # Launch Services: prevent a second instance when something fires
        # `open -a Waffler` while it's already running. Fixes the "app
        # opens twice on first launch" issue where a race between the
        # dock click and the app's own overlay-subprocess spawn could
        # land a second main instance.
        'LSMultipleInstancesProhibited': True,
    },
    # Code signing (set SIGNING_IDENTITY env var when you have Developer ID)
    codesign_identity=os.environ.get('SIGNING_IDENTITY', None),
    entitlements_file='entitlements.plist' if os.environ.get('SIGNING_IDENTITY') else None,
)
