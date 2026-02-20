# -*- mode: python ; coding: utf-8 -*-
"""
VoiceFlow — macOS PyInstaller spec
Bundles all hidden imports, data files (ui/, prompts/, config.yaml, .env, src/)
Produces: dist/VoiceFlow.app  (macOS .app bundle)

Run on a Mac with: pyinstaller VoiceFlow_mac.spec
"""

import sys
import os

block_cipher = None
PROJECT_ROOT = os.path.abspath('.')

a = Analysis(
    ['app.py'],
    pathex=[PROJECT_ROOT, os.path.join(PROJECT_ROOT, 'src')],
    binaries=[],
    datas=[
        ('ui', 'ui'),
        ('prompts', 'prompts'),
        ('src', 'src'),
        ('config.yaml', '.'),
    ],
    hiddenimports=[
        # ── Core dependencies ──
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._darwin',
        'pynput._util',
        'pynput._util.darwin',
        'pynput.mouse',
        'pynput.mouse._darwin',
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
        # ── Deepgram (optional) ──
        'deepgram',
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
    name='VoiceFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,  # Add icon path here if you have one: icon='icon.icns'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VoiceFlow',
)

app = BUNDLE(
    coll,
    name='VoiceFlow.app',
    icon=None,  # Add .icns icon here
    bundle_identifier='com.voiceflow.app',
    info_plist={
        'NSMicrophoneUsageDescription': 'VoiceFlow needs microphone access for voice transcription.',
        'NSAppleEventsUsageDescription': 'VoiceFlow needs accessibility access for hotkey detection and auto-paste.',
        'CFBundleShortVersionString': '1.0.0',
    },
)
