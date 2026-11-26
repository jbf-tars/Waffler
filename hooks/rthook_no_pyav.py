"""Runtime hook, Windows build: Waffler ships without PyAV.

PyAV's wheel carries FFmpeg's libraries built with libx264 and libx265 (GPL),
and Waffler's installers hold no GPL code (James, 26 Nov 2026). Waffler never
decodes through PyAV: its one faster-whisper call passes a 16 kHz float32
array (src/local_stt_ct2.py), and faster-whisper decodes only what is not an
array. faster_whisper/audio.py still says `import av` as it loads. So
Waffler_windows.spec leaves the real package out, and this hook, which runs
before the app, puts a stand-in named av in sys.modules.

Importing the stand-in works; asking it for anything raises a plain
ImportError instead of half doing something. It notes what it was asked for
(av.asked). That is how tests/test_no_pyav_windows.py shows that importing
faster-whisper, and transcribing an array with it, never touch PyAV.
"""
import importlib.machinery
import sys
import types


class _NotBundled(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        self.__dict__.setdefault("asked", []).append(name)
        raise ImportError(
            f"av.{name}: PyAV is not part of Waffler for Windows (its FFmpeg libraries "
            "include GPL encoders). Waffler gives faster-whisper its audio as an array, "
            "so nothing should need it.")


if "av" not in sys.modules:
    _av = _NotBundled("av", "Stand-in for PyAV, which Waffler for Windows does not ship.")
    _av.__spec__ = importlib.machinery.ModuleSpec("av", None)
    _av.asked = []
    _av.WAFFLER_STAND_IN = True
    sys.modules["av"] = _av
