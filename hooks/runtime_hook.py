"""
PyInstaller runtime hook for Waffler.
Sets the working directory to the bundle's resource folder so that
config.yaml, .env, prompts/, and src/ are found by relative-path code.
"""
import sys
import os

if getattr(sys, 'frozen', False):
    # Running inside a PyInstaller bundle
    bundle_dir = sys._MEIPASS  # noqa: F821  (injected by PyInstaller)
    os.chdir(bundle_dir)
    # Also add bundle_dir to sys.path so 'import src.xxx' works
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)
