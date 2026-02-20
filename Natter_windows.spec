# -*- mode: python ; coding: utf-8 -*-
"""
Natter — Windows PyInstaller spec
Bundles all hidden imports, data files (ui/, prompts/, config.yaml, .env, src/)
Produces: dist/Natter/Natter.exe  (one-folder mode for faster startup)
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
        'pynput.keyboard._win32',
        'pynput._util',
        'pynput._util.win32',
        'pynput.mouse',
        'pynput.mouse._win32',
        # ── Audio ──
        'sounddevice',
        'numpy',
        '_sounddevice_data',
        # ── UI ──
        'webview',
        'webview.platforms.edgechromium',
        'clr',
        'pythonnet',
        'tkinter',
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
        # ── System tray (Windows) ──
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        # ── Standard library sometimes missed ──
        'ctypes',
        'ctypes.wintypes',
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
        # ── Deepgram (optional but include if installed) ──
        'deepgram',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # macOS-only packages — exclude on Windows
        'pyobjc',
        'pyobjc_core',
        'pyobjc_framework_Cocoa',
        'pyobjc_framework_Quartz',
        'objc',
        'AppKit',
        'Foundation',
        'rumps',
        'mlx_whisper',
        'faster_whisper',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Natter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Natter',
)
