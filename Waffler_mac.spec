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

# ── On-device speech to text (src/local_stt.py) ──────────────────────────────
# The ENGINE is bundled; the MODEL never is (about 0.6 GB): it is downloaded
# once into the data folder from Settings.
#   Apple Silicon: whisper.cpp through pywhispercpp. Its extension module links
#     libwhisper and libggml-* (Metal among them) from pywhispercpp/.dylibs,
#     and PyInstaller follows those links by itself (checked with a frozen
#     probe: see docs/OFFLINE-TRANSCRIPTION.md), so no library is named here.
#     pywhispercpp reads its own version from its package metadata when it is
#     imported, so the metadata is put in the bundle.
#   Intel Mac (not a release target today): faster-whisper, if it is installed.
# The helpers tolerate a package that is not installed, and only the engine
# that is installed is named, so this spec builds on either kind of Mac
# without "hidden import not found" lines for the other one.
import importlib.util
from PyInstaller.utils.hooks import copy_metadata


def _stt_installed(package, *modules):
    """`modules` when `package` is installed in the build's Python, else []."""
    try:
        found = importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        print(f"[spec] on-device speech to text: {package} is not installed, not bundled")
    return list(modules) if found else []


# Both engines are imported inside functions, so they are named.
_STT_HIDDEN = (
    # whisper.cpp (Apple Silicon)
    _stt_installed('pywhispercpp', 'pywhispercpp', 'pywhispercpp.model', 'pywhispercpp.constants',
                   'pywhispercpp.utils', '_pywhispercpp', 'platformdirs', 'tqdm')
    # faster-whisper (Intel Mac)
    + _stt_installed('faster_whisper', 'faster_whisper', 'ctranslate2', 'tokenizers',
                     'huggingface_hub', 'av')
)


def _stt_metadata(package):
    try:
        return copy_metadata(package)
    except Exception as e:
        print(f"[spec] no metadata for {package}: {e}")
        return []


_STT_DATAS = _stt_metadata('pywhispercpp')

# ── The writing model's engine (src/local_llm.py), optional ─────────────────
# llama.cpp through llama-cpp-python: it tidies text and writes meeting notes
# on this computer. As with speech, the ENGINE is bundled and the MODEL never
# is (5.2 GB, downloaded from Settings into the data folder).
#   It is bundled only if it is installed in the build's Python, and
#   requirements.txt does not install it (PyPI has no binary wheel: pip
#   compiles it; the exact command is in requirements.txt). A build without
#   it works as before and simply does not offer the second download.
#   llama_cpp is imported inside a function, so it is named. It opens its
#   libraries (libllama, libggml-* with Metal among them, libmtmd) by path
#   with ctypes from llama_cpp/lib, which PyInstaller cannot see, so they are
#   collected into that same folder.
from PyInstaller.utils.hooks import collect_dynamic_libs

_LLM_HIDDEN = _stt_installed('llama_cpp', 'llama_cpp', 'llama_cpp.llama_chat_format', 'jinja2',
                             'diskcache')


def _llm_libs():
    if not _LLM_HIDDEN:
        return []
    try:
        return collect_dynamic_libs('llama_cpp')
    except Exception as e:
        print(f"[spec] the writing model's engine: its libraries were not collected: {e}")
        return []


_LLM_BINARIES = _llm_libs()

# ── The computer-sound helper for meetings (mac/sysaudio), optional ─────────
# A meeting records the other people from what the Mac is playing, through a
# small Swift program (ScreenCaptureKit) that src/meetings/recorder.py starts.
# mac/build_sysaudio.sh builds it into mac/build/natter-sysaudio; build_mac.sh
# and the release workflow run that before this spec. It is bundled as a
# binary, so it lands in Contents/Frameworks and is inside the app's
# signature. Without it the build still works and a meeting records the
# microphone only, which is why its absence is said out loud here.
_SYSAUDIO = os.path.join(PROJECT_ROOT, 'mac', 'build', 'natter-sysaudio')
if os.path.isfile(_SYSAUDIO):
    _SYSAUDIO_BINARIES = [(_SYSAUDIO, '.')]
else:
    print("[spec] meetings: mac/build/natter-sysaudio is not built (run mac/build_sysaudio.sh), "
          "not bundled: meetings will record the microphone only")
    _SYSAUDIO_BINARIES = []

a = Analysis(
    ['app.py'],
    pathex=[PROJECT_ROOT, os.path.join(PROJECT_ROOT, 'src')],
    binaries=_LLM_BINARIES + _SYSAUDIO_BINARIES,
    datas=_STT_DATAS + [
        ('ui', 'ui'),
        ('prompts', 'prompts'),
        ('src', 'src'),
        ('config.yaml', '.'),
        ('.env.example', '.'),
        # Monochrome menu-bar template glyph (rendered white on dark bar,
        # black on light bar via NSImage setTemplate:). Without this the
        # status item falls back to the full-colour icon.icns and looks
        # out of place next to the system's monochrome menu-bar icons.
        ('menubar_icon_template.png', '.'),
    ],
    hiddenimports=[
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
        # ── On-device speech to text (see _STT_HIDDEN above) ──
        *_STT_HIDDEN,
        # ── The writing model's engine, when installed (see _LLM_HIDDEN) ──
        *_LLM_HIDDEN,
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
        # v3.14.31 — AVFoundation for the proper mic TCC check at startup
        'AVFoundation',
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
    # Caps NumPy's BLAS thread pools before anything imports it; see the
    # hook's docstring. (hooks/runtime_hook.py is a different, unused hook.)
    runtime_hooks=[os.path.join(PROJECT_ROOT, 'hooks', 'rthook_thread_caps.py')],
    excludes=[
        # Windows-only packages — exclude on Mac
        'pystray',
        'pystray._win32',
        'ctypes.wintypes',
        'pythonnet',
        'clr',
        'windows_hotkey',
        'overlay_process_windows',
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
    icon='icon.icns',
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
        # The real minimum, measured from the bundled binaries (v3.14.100).
        # The macOS 14 build runner installs NumPy's macosx_14_0_arm64
        # wheel: its extension modules are built for macOS 14.0 and call
        # Accelerate's $NEWLAPACK$ILP64 functions, which older releases
        # lack. NumPy is imported at start-up (src/audio.py), so declaring
        # 10.13 meant older Macs got an app that failed to open rather than
        # a clean "requires a newer version of macOS" refusal. The release
        # workflow runs scripts/check_macos_minos.py on the built app and
        # fails if any binary needs a newer macOS than this line says.
        'LSMinimumSystemVersion': '14.0.0',
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
