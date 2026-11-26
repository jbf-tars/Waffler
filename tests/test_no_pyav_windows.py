"""The Windows build ships without PyAV, and without the PortAudio libraries
64-bit Windows never loads.

James, 26 Nov 2026: no GPL program or library inside either installer.
PyAV's wheel carries FFmpeg's libraries built with libx264 and libx265 (GPL),
and avcodec loads them whenever `av` is imported (checked on the PC: its
import table names both).

Waffler never decodes through PyAV. Its one faster-whisper call passes a
16 kHz float32 array (src/local_stt_ct2.py), and faster-whisper decodes only
what is not an array (faster_whisper/transcribe.py). But
faster_whisper/audio.py says `import av` as it loads. So the build leaves the
real package out (Waffler_windows.spec, excludes), and a runtime hook puts a
stand-in in its place before anything runs (hooks/rthook_no_pyav.py).
Importing the stand-in works; asking it for anything raises a plain error.

These tests import faster-whisper against that stand-in and transcribe an
array through Waffler's own engine, so a faster-whisper that needs more of
PyAV fails here before it ships. The transcription needs a CTranslate2
Whisper model already on the machine: WAFFLER_TEST_CT2_MODEL, or
Systran/faster-whisper-base in the Hugging Face cache. Nothing is
downloaded; without a model that test is skipped.

sounddevice's wheel carries PortAudio for every Windows CPU, each with and
without ASIO, plus the Mac's. On 64-bit Windows it loads
libportaudio64bit.dll only (the -asio one only with SD_ENABLE_ASIO, which
Waffler never sets). The ASIO builds include Steinberg's ASIO SDK, whose
licence the wheel does not state, so the rest are left out.
"""
import ast
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "Waffler_windows.spec"
HOOK = ROOT / "hooks" / "rthook_no_pyav.py"

_TREE = ast.parse(SPEC.read_text(encoding="utf-8"))
_ANALYSIS = next(n for n in ast.walk(_TREE) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Analysis")
_KW = {k.arg: k.value for k in _ANALYSIS.keywords}

needs_faster_whisper = pytest.mark.skipif(
    importlib.util.find_spec("faster_whisper") is None,
    reason="faster-whisper is not installed here (requirements_windows.txt installs it)")


def _run(code: str, tmp_path: Path, timeout: int = 300) -> dict:
    """`code` in a fresh Python, after the stand-in's hook: the last line it prints, as JSON."""
    prelude = f"import json, runpy, sys\nrunpy.run_path({str(HOOK)!r})\n"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
           "WAFFLER_DATA_DIR": str(tmp_path), "WAFFLER_WHISPER_DEVICE": "cpu"}
    r = subprocess.run([sys.executable, "-c", prelude + textwrap.dedent(code)], capture_output=True,
                       encoding="utf-8", errors="replace", timeout=timeout, cwd=str(tmp_path), env=env)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


# -- the build -----------------------------------------------------------------

def test_the_spec_leaves_pyav_out_and_runs_the_stand_in_before_the_app():
    assert "av" in [e.value for e in _KW["excludes"].elts]
    hooks = [ast.unparse(h) for h in _KW["runtime_hooks"].elts]
    assert any("'rthook_no_pyav.py'" in h for h in hooks), hooks
    assert HOOK.is_file()
    # Nothing names it back in.
    hidden = next(n for n in _TREE.body if isinstance(n, ast.Assign)
                  and getattr(n.targets[0], "id", "") == "_STT_HIDDEN")
    assert "'av'" not in ast.unparse(hidden.value)
    assert "'av'" not in ast.unparse(_KW["hiddenimports"])


def test_the_build_carries_no_ffmpeg_of_its_own():
    """imageio-ffmpeg's program is gyan.dev's GPL build. Import audio's
    converter is an LGPL FFmpeg fetched on first use (src/audio_decoder_pack.py),
    so the package is neither installed for the build nor collected from a
    build environment that still has it (excluded, as the Mac spec does)."""
    reqs = (ROOT / "requirements_windows.txt").read_text(encoding="utf-8")
    assert not [line for line in reqs.splitlines() if line.strip().lower().startswith("imageio-ffmpeg")]
    assert "'imageio_ffmpeg'" not in ast.unparse(_KW["hiddenimports"])
    assert "imageio_ffmpeg" in [e.value for e in _KW["excludes"].elts]


def _keep_portaudio():
    fn = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_keep_portaudio")
    ns = {}
    exec(compile(ast.Module([fn], []), "<Waffler_windows.spec>", "exec"), ns)
    return ns["_keep_portaudio"]


def test_only_the_portaudio_library_64_bit_windows_loads_is_kept():
    keep = _keep_portaudio()
    where = "_sounddevice_data\\portaudio-binaries\\"
    assert keep(where + "libportaudio64bit.dll")
    assert keep(where + "README.md")                    # PortAudio's MIT notice
    for left_out in ("libportaudio64bit-asio.dll", "libportaudio32bit.dll", "libportaudio32bit-asio.dll",
                     "libportaudioarm64.dll", "libportaudioarm64-asio.dll", "libportaudio.dylib"):
        assert not keep(where + left_out), left_out
        assert not keep(where.replace("\\", "/") + left_out), left_out
    assert keep("ctranslate2\\libiomp5md.dll") and keep("_soundfile_data\\libsndfile_x64.dll")


def test_the_spec_applies_the_portaudio_filter_to_what_is_collected():
    text = SPEC.read_text(encoding="utf-8")
    assert "a.binaries = [b for b in a.binaries if _keep_portaudio(b[0])]" in text
    # PyInstaller files a library that is not a Windows one (the Mac's .dylib)
    # under datas, so both lists are filtered (the first build kept it).
    assert "a.datas = [d for d in a.datas if _keep_portaudio(d[0])]" in text
    assert text.index("a.binaries = [b for b in a.binaries") < text.index("coll = COLLECT(")
    assert text.index("a.datas = [d for d in a.datas") < text.index("coll = COLLECT(")


# -- the stand-in ----------------------------------------------------------------

def test_the_stand_in_imports_and_refuses_anything_asked_of_it(tmp_path):
    out = _run("""
        import av
        out = {"stand_in": getattr(av, "WAFFLER_STAND_IN", False)}
        try:
            av.open("memo.m4a")
        except ImportError as e:
            out["open"] = str(e)
        try:
            import av.audio.resampler
        except ImportError as e:
            out["submodule"] = type(e).__name__
        out["asked"] = list(av.asked)
        print(json.dumps(out))
    """, tmp_path)
    assert out["stand_in"] is True
    assert "not part of Waffler for Windows" in out["open"]
    assert out["submodule"] in ("ModuleNotFoundError", "ImportError")
    assert out["asked"] == ["open"]


@needs_faster_whisper
def test_faster_whisper_imports_against_the_stand_in(tmp_path):
    out = _run("""
        import faster_whisper
        from faster_whisper import WhisperModel, decode_audio
        import av
        print(json.dumps({"version": faster_whisper.__version__,
                          "stand_in": getattr(av, "WAFFLER_STAND_IN", False), "asked": list(av.asked)}))
    """, tmp_path)
    assert out["stand_in"] is True
    assert out["asked"] == [], out


@needs_faster_whisper
def test_an_array_is_transcribed_through_waffler_s_engine_against_the_stand_in(tmp_path):
    out = _run("""
        import os
        from pathlib import Path
        import numpy as np
        folder = os.environ.get("WAFFLER_TEST_CT2_MODEL", "")
        if not folder:
            try:
                from huggingface_hub import snapshot_download
                folder = snapshot_download("Systran/faster-whisper-base", local_files_only=True)
            except Exception:
                folder = ""
        if not folder:
            print(json.dumps({"skip": "no CTranslate2 Whisper model on this machine"}))
            sys.exit(0)
        import local_stt_ct2
        engine = local_stt_ct2.Ct2Engine(Path(folder), "base")
        t = np.arange(2 * 16000) / 16000.0
        audio = (0.1 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)    # two seconds of a tone
        segments = engine.transcribe(audio, "en", None)
        import av
        print(json.dumps({"segments": len(segments), "device": engine.device, "asked": list(av.asked),
                          "stand_in": getattr(av, "WAFFLER_STAND_IN", False)}))
    """, tmp_path, timeout=600)
    if "skip" in out:
        pytest.skip(out["skip"])
    assert out["stand_in"] is True and out["device"] == "cpu"
    assert out["asked"] == [], out
    assert isinstance(out["segments"], int)
