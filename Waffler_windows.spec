# -*- mode: python ; coding: utf-8 -*-
"""
Waffler — Windows PyInstaller spec
Bundles all hidden imports, data files (ui/, prompts/, config.yaml, .env, src/)
Produces: dist/Waffler/Waffler.exe  (one-folder mode for faster startup)
"""

import sys
import os
import shutil as _shutil

block_cipher = None
PROJECT_ROOT = os.path.abspath('.')

# ── ffmpeg ───────────────────────────────────────────────────────────────
# faster-whisper shells out to `ffmpeg` to decode audio. Bundle the build
# host's ffmpeg.exe into the .exe distribution so end users do not need to
# install ffmpeg separately. Fail hard if missing so we don't ship a
# silently broken installer.
_ffmpeg_path = _shutil.which('ffmpeg')
if not _ffmpeg_path:
    raise SystemExit(
        "ERROR: ffmpeg not found on PATH. Install with `choco install ffmpeg -y` "
        "(locally) or add a workflow step that installs ffmpeg before this build."
    )
print(f"[spec] bundling ffmpeg from {_ffmpeg_path}")

a = Analysis(
    ['app.py'],
    pathex=[PROJECT_ROOT, os.path.join(PROJECT_ROOT, 'src')],
    binaries=[(_ffmpeg_path, '.')],
    datas=[
        ('ui', 'ui'),
        ('prompts', 'prompts'),
        ('src', 'src'),
        ('config.yaml', '.'),
    ],
    hiddenimports=[
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
    icon='icon.ico',
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
