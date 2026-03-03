# -*- mode: python ; coding: utf-8 -*-
"""
WafflerOverlay — PyInstaller spec for the overlay subprocess
Produces: dist/WafflerOverlay/WafflerOverlay.exe (one-folder mode)

This is the tkinter overlay that shows the waffle animation during recording.
It runs as a subprocess of the main Waffler app, communicating via stdin/stdout JSON.
"""

import os

block_cipher = None
PROJECT_ROOT = os.path.abspath('.')

a = Analysis(
    [os.path.join('src', 'overlay_process_windows.py')],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        'tkinter',
        'tkinter.font',
        '_tkinter',
        'json',
        'threading',
        'queue',
        'math',
        'time',
        'random',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Not needed for the overlay
        'numpy',
        'PIL',
        'pynput',
        'webview',
        'openai',
        'groq',
        'requests',
        'httpx',
        'supabase',
        'pystray',
        'pythonnet',
        'clr',
        'sounddevice',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WafflerOverlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WafflerOverlay',
)
