# -*- mode: python ; coding: utf-8 -*-
"""
Waffler — macOS PyInstaller spec
Bundles all hidden imports, data files (ui/, prompts/, config.yaml, .env, src/)
Produces: dist/Waffler.app  (macOS .app bundle)

Run on a Mac with: pyinstaller Waffler_mac.spec
"""

import sys
import os
import platform

from PyInstaller.utils.hooks import collect_all

block_cipher = None
PROJECT_ROOT = os.path.abspath('.')

# ── Version ──────────────────────────────────────────────────────────────
# Read from src/__init__.py which CI rewrites from the git tag in
# `.github/workflows/macos-release.yml` → "Sync app version from tag".
# Local builds use whatever is currently checked in.
import re as _re
_init_text = open(os.path.join(PROJECT_ROOT, 'src', '__init__.py')).read()
_m = _re.search(r'__version__\s*=\s*"([^"]+)"', _init_text)
_VERSION = _m.group(1) if _m else '0.0.0'
print(f"[spec] building Waffler.app version {_VERSION}")

# ── Local Whisper backend ────────────────────────────────────────────────
# Apple Silicon: bundle mlx-whisper (uses Neural Engine via MLX).
# Intel Mac / building on non-ARM: bundle faster-whisper (CPU).
# collect_all pulls submodules, data files, and any bundled .dylib/.so
# shaders so Private Mode works in the frozen .app without pip install.
import shutil as _shutil

_extra_hidden = []
_extra_datas = []
_extra_binaries = []

# ── ffmpeg ───────────────────────────────────────────────────────────────
# mlx-whisper / faster-whisper shell out to `ffmpeg` to decode audio.
# Bundle the build host's ffmpeg into the .app so end users do not need
# to `brew install ffmpeg`. Fail hard here rather than shipping a silently
# broken DMG — every prior release has burned a notarization cycle to
# discover this.
_ffmpeg_path = _shutil.which('ffmpeg')
if not _ffmpeg_path:
    raise SystemExit(
        "ERROR: ffmpeg not found on PATH. Install with `brew install ffmpeg` "
        "(locally) or ensure the GitHub Actions runner has it installed."
    )
print(f"[spec] bundling ffmpeg from {_ffmpeg_path}")
_extra_binaries.append((_ffmpeg_path, '.'))
_IS_ARM_MAC_BUILD = sys.platform == 'darwin' and platform.machine() == 'arm64'
_local_whisper_pkgs = ['mlx_whisper', 'mlx'] if _IS_ARM_MAC_BUILD else ['faster_whisper']
for _pkg in _local_whisper_pkgs:
    try:
        # PyInstaller's collect_all returns (datas, binaries, hiddenimports).
        _d, _b, _h = collect_all(_pkg)
        _extra_datas.extend(_d)
        _extra_binaries.extend(_b)
        _extra_hidden.extend(_h)
    except Exception as _e:
        print(f"WARNING: could not collect {_pkg} for Private Mode: {_e}")

a = Analysis(
    ['app.py'],
    pathex=[PROJECT_ROOT, os.path.join(PROJECT_ROOT, 'src')],
    binaries=_extra_binaries,
    datas=[
        ('ui', 'ui'),
        ('prompts', 'prompts'),
        ('src', 'src'),
        ('config.yaml', '.'),
        ('.env.example', '.'),
    ] + _extra_datas,
    hiddenimports=_extra_hidden + [
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
        # Version string is rewritten by CI's "Sync app version from tag" step
        # for tagged builds. Keep in sync manually for local builds.
        'CFBundleShortVersionString': _VERSION,
        'LSMinimumSystemVersion': '10.13.0',
    },
    # Code signing (set SIGNING_IDENTITY env var when you have Developer ID)
    codesign_identity=os.environ.get('SIGNING_IDENTITY', None),
    entitlements_file='entitlements.plist' if os.environ.get('SIGNING_IDENTITY') else None,
)
