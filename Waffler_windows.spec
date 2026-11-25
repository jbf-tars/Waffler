# -*- mode: python ; coding: utf-8 -*-
"""
Waffler — Windows PyInstaller spec
Bundles all hidden imports, data files (ui/, prompts/, config.yaml, .env, src/)
Produces: dist/Waffler/Waffler.exe  (one-folder mode for faster startup)
"""

import sys
import os

block_cipher = None
PROJECT_ROOT = os.path.abspath('.')

# ── On-device speech to text (src/local_stt.py) ──────────────────────────────
# The ENGINE is bundled; the MODEL never is (0.15 to 1.6 GB): it is downloaded
# once into the data folder from Settings.
#   Windows: faster-whisper, which is CTranslate2. ctranslate2 loads
#     ctranslate2.dll and libiomp5md.dll from its own package folder
#     (os.add_dll_directory), so they are collected into that folder. NVIDIA's
#     CUDA libraries (about 2 GB) are not bundled: the CPU is used unless they
#     are already on the machine (src/local_stt_ct2.py).
# The helpers tolerate a package that is not installed.
import importlib.util
from PyInstaller.utils.hooks import collect_dynamic_libs


def _stt_installed(package, *modules):
    """`modules` when `package` is installed in the build's Python, else []."""
    try:
        found = importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        print(f"[spec] on-device speech to text: {package} is not installed, not bundled")
    return list(modules) if found else []


# The engine is imported inside functions, so it is named, with what it
# imports as it loads.
_STT_HIDDEN = _stt_installed('faster_whisper', 'faster_whisper', 'ctranslate2', 'tokenizers',
                             'huggingface_hub', 'av')


def _stt_libs(package):
    try:
        return collect_dynamic_libs(package)
    except Exception as e:
        print(f"[spec] no dynamic libraries collected for {package}: {e}")
        return []


_STT_BINARIES = _stt_libs('ctranslate2')

a = Analysis(
    ['app.py'],
    pathex=[PROJECT_ROOT, os.path.join(PROJECT_ROOT, 'src')],
    binaries=_STT_BINARIES,
    datas=[
        ('ui', 'ui'),
        ('prompts', 'prompts'),
        ('src', 'src'),
        ('config.yaml', '.'),
        # The tray and title bar icon. Left out from 3.14.84 to 3.14.100, so
        # installed builds had no tray icon (tests/test_tray_icon_bundled.py).
        ('icon.ico', '.'),
    ],
    hiddenimports=[
        # ── On-device speech to text (see _STT_HIDDEN above) ──
        *_STT_HIDDEN,
        # ── Audio ──
        'sounddevice',
        'numpy',
        '_sounddevice_data',
        # FLAC upload encoder: imported inside a function, so named here;
        # pyinstaller-hooks-contrib's hook-soundfile collects the
        # libsndfile library from _soundfile_data.
        'soundfile',
        '_soundfile',
        '_soundfile_data',
        'cffi',
        '_cffi_backend',
        'winsound',   # src/sounds.py imports it by name, so PyInstaller cannot see it
        # Meetings: the computer's sound through WASAPI loopback, imported
        # inside functions in src/meetings/recorder.py (SystemSource).
        'pyaudiowpatch',
        # Meetings, also imported inside functions. Import audio that
        # soundfile cannot read (m4a): the wheel's own ffmpeg program, which
        # pyinstaller-hooks-contrib's hook-imageio_ffmpeg collects from
        # imageio_ffmpeg/binaries. And meeting notes by Anthropic's models.
        'imageio_ffmpeg',
        'anthropic',
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
        # Import from Granola (src/meetings/granola_import.py): the listener the
        # browser comes back to, and the rest it signs in and reads with.
        'http.server',
        'html',
        'secrets',
        'urllib.request',
        # Sound cues (src/sounds.py) import it by name at the first cue.
        'winsound',
    ],
    hookspath=[],
    hooksconfig={},
    # Caps NumPy's BLAS thread pools before anything imports it; see the
    # hook's docstring. (hooks/runtime_hook.py is a different, unused hook.)
    runtime_hooks=[os.path.join(PROJECT_ROOT, 'hooks', 'rthook_thread_caps.py')],
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
        # Apple Silicon's engine; Windows uses faster-whisper.
        'pywhispercpp',
        '_pywhispercpp',
        # faster-whisper lists onnxruntime for its voice-activity filter,
        # which Waffler never switches on (it has its own): about 85 MB saved.
        'onnxruntime',
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
    # UPX off: packed executables are a common antivirus false-positive
    # trigger (Waffler has had one Defender flag), and unpacking at every
    # launch slows start-up.
    upx=False,
    console=False,
    icon='icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Waffler',
)
