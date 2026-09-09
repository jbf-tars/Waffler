"""Mirror of app._MIN_TAP_SPEECH_S for tests.

app.py imports pywebview and audio hardware at module scope, so it cannot be
imported in a headless test. test_capture_device_and_taps asserts this stays in
sync with the real constant.
"""
import pathlib, re

_src = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
MIN_TAP_SPEECH_S = float(re.search(r"_MIN_TAP_SPEECH_S\s*=\s*([\d.]+)", _src).group(1))
