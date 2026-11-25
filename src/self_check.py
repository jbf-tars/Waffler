"""`Waffler --self-check`: is everything this build needs really inside it?

A built app can be missing a module, a page's files or an engine's library and
still open, and nobody finds out until a person presses the button that needed
it. This imports each part that is easy to leave out of a build, looks for the
files the window loads, asks the two on-device engines to answer one question
each (no model is loaded: models are never in a build), and prints one JSON
object on stdout. On Windows the writing engine is llama.cpp's own server,
which comes down with the model and so is never in a build either: there the
check imports the code that fetches and starts it, names what this computer
would fetch, and asks a server already here its version.

    Waffler.app/Contents/MacOS/Waffler --self-check
    python app.py --self-check --complete

Exit code 0 when every required part is there, 1 when one is not. The engines,
the computer-sound helper and the two libraries meetings import only when they
are needed (anthropic, and imageio-ffmpeg with its ffmpeg program) are
optional in a build: a build without one simply cannot do that one thing. So
they are reported, and fail the check only when `--complete` is given, which
is what a release build should pass.

    Waffler --self-check --audio memo.m4a

also puts one audio file through "Import audio" (src/meetings/service.py
load_any_audio) and says how many seconds came out and whether ffmpeg was
needed: the proof that a voice memo can be imported by this build.

It opens no window, registers no shortcut, makes no connection, and never
reads or writes the person's data: app.py runs it before it opens crash.log,
and it works in a data folder of its own unless WAFFLER_DATA_DIR names one.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Parts added for meetings, on-device speech and writing, accounts, the Claude
# connector and Try it. Each is imported as `src.<name>`, then as `<name>`,
# the two ways the app itself imports them.
MODULES = (
    "meetings", "meetings.audioproc", "meetings.keys", "meetings.llm", "meetings.paths",
    "meetings.recorder", "meetings.service", "meetings.store", "meetings.sync",
    "meetings.transcribe", "meetings.granola_import", "meetings_ctl", "meetings_bridge", "meeting_mic",
    "local_stt", "local_stt_whispercpp", "local_stt_ct2", "local_llm", "local_llm_llamacpp",
    "account", "claude_connector", "try_it", "pill", "idle_pill", "user_messages",
)
# The pop-up pill's drawing: AppKit on a Mac. (Importing it draws nothing.)
MAC_MODULES = ("overlay_process",)

# Files the window loads, from the folder app.py is in.
FILES = (
    "config.yaml", "prompts",
    "ui/index.html", "ui/app.js", "ui/logic.js", "ui/icons.js",
    "ui/account.js", "ui/claude.js", "ui/tryit.js", "ui/howitruns.js", "ui/meetings.js",
    "ui/granola.js",
    "ui/tokens.css", "ui/components.css", "ui/style.css", "ui/setup.css", "ui/meetings.css",
    "ui/meetings-app/index.html",
)


def _import(name: str):
    try:
        return importlib.import_module(f"src.{name}")
    except ImportError:
        return importlib.import_module(name)


def check_modules(names) -> dict:
    """{name: why} for each module that could not be imported."""
    missing = {}
    for name in names:
        try:
            _import(name)
        except Exception as e:                      # any failure is a missing part
            missing[name] = f"{type(e).__name__}: {e}"
    return missing


def check_files(root: Path, names=FILES) -> list:
    """The files that are not there, the meetings page's own scripts and
    styles (named in its index.html) among them."""
    missing = [n for n in names if not (root / n).exists()]
    page = root / "ui" / "meetings-app" / "index.html"
    if page.is_file():
        for ref in re.findall(r'(?:src|href)="(?:\./)?(assets/[^"?#]+)', page.read_text(encoding="utf-8")):
            if not (page.parent / ref).is_file():
                missing.append(f"ui/meetings-app/{ref}")
    return missing


def check_speech() -> dict:
    """The speech engine for this computer: is it in the build, does it answer?"""
    out = {"engine": None, "present": False, "runs": False}
    try:
        stt = _import("local_stt")
        out["engine"] = stt.engine_name()
        out["present"] = bool(stt.is_available())
        if not out["present"]:
            return out
        if out["engine"] == stt.ENGINE_WHISPERCPP:
            from pywhispercpp.model import Model                # noqa: F401  the class the app opens a model with
            import _pywhispercpp
            out["detail"] = str(_pywhispercpp.whisper_print_system_info()).strip()
        else:
            import faster_whisper
            out["detail"] = f"faster-whisper {faster_whisper.__version__}"
        out["runs"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def check_writing() -> dict:
    """The writing engine (llama.cpp): is it in the build, does it answer?"""
    if sys.platform == "win32" and importlib.util.find_spec("llama_cpp") is None:
        return check_writing_server()
    out = {"engine": "llama.cpp", "present": False, "runs": False}
    try:
        out["present"] = bool(_import("local_llm").is_available())
        if not out["present"]:
            return out
        import llama_cpp                                        # opens libllama and its libraries by path
        llama_cpp.llama_backend_init()
        info = llama_cpp.llama_print_system_info()
        out["detail"] = (info.decode("utf-8", "replace") if isinstance(info, bytes) else str(info)).strip()
        out["version"] = getattr(llama_cpp, "__version__", "")
        out["gpu"] = bool(llama_cpp.llama_supports_gpu_offload())
        out["runs"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def check_writing_server() -> dict:
    """Windows: llama.cpp's own server, fetched with the model into the data
    folder (src/llama_pack.py) and started by src/local_llm_server.py. No
    build carries it, so with none here yet the answer is what would be
    fetched; a server that is here is asked its version."""
    out = {"engine": "llama.cpp server", "present": False, "runs": False}
    try:
        pack, server = _import("llama_pack"), _import("local_llm_server")
        if not pack.supported():
            out["error"] = "no llama.cpp server is made for this computer"
            return out
        found = server.find_server()
        if found is None:
            kind, assets = pack.plan()
            out["comes_with_model"] = True
            out["detail"] = f"comes down with the model ({kind}): " + ", ".join(a.file.name for a in assets)
            return out
        out["present"] = True
        done = subprocess.run([str(found), "--version"], capture_output=True, encoding="utf-8",
                              errors="replace", timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        said = [line.strip() for line in ((done.stdout or "") + (done.stderr or "")).splitlines() if line.strip()]
        if done.returncode != 0:
            out["error"] = f"{found.name} --version exited {done.returncode}" + (f": {said[-1][:200]}" if said else "")
            return out
        out["detail"] = next((line for line in said if line.startswith("version")), said[0] if said else "")
        out["runs"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def check_libraries() -> dict:
    """The libraries meetings import inside functions, which a build leaves
    out without a word unless they are installed and named in the spec."""
    out = {}
    try:
        import anthropic
        out["anthropic"] = {"present": True, "version": getattr(anthropic, "__version__", "")}
    except Exception as e:
        out["anthropic"] = {"present": False, "error": f"{type(e).__name__}: {e}"}
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()       # the wheel's own program; raises when it is not there
        out["ffmpeg"] = {"present": os.path.isfile(exe) and os.access(exe, os.X_OK), "path": str(exe)}
    except Exception as e:
        out["ffmpeg"] = {"present": False, "error": f"{type(e).__name__}: {e}"}
    return out


def check_meeting_keys() -> dict:
    """Where a meeting's keys are kept. keyring is left out of the build on
    purpose (requirements.txt), so this should say there is none: keys then
    come from the environment the app sets, and are held in memory only."""
    try:
        keys = _import("meetings.keys")
        return {"keyring": bool(keys._keyring()), "store": keys.store_kind()}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def check_audio(path) -> dict:
    """One audio file through "Import audio", as the app would read it."""
    out = {"file": os.path.basename(str(path)), "decoded": False}
    try:
        service = _import("meetings.service")
        try:
            _import("meetings.audioproc").read_audio(path)
            out["needed_ffmpeg"] = False
        except Exception:
            out["needed_ffmpeg"] = True              # soundfile cannot read it (m4a)
        out["ffmpeg_on_path"] = shutil.which("ffmpeg") or ""
        audio = service.load_any_audio(Path(path))
        out["seconds"] = round(len(audio) / service.RATE, 2)
        out["decoded"] = len(audio) > 0
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def check_computer_sound() -> dict:
    """The helper that records what the computer is playing in a meeting (a
    Mac needs one; Windows does not). It is looked for, never started:
    starting it asks for the Screen & System Audio Recording permission."""
    if sys.platform != "darwin":
        return {"needed": False, "present": False}
    try:
        path = _import("meetings.recorder")._mac_helper_path()
        return {"needed": True, "present": path.is_file() and os.access(path, os.X_OK), "path": str(path)}
    except Exception as e:
        return {"needed": True, "present": False, "error": f"{type(e).__name__}: {e}"}


def run(root: Path, complete: bool = False, audio=None) -> dict:
    """The whole check, as the object that is printed."""
    names = MODULES + (MAC_MODULES if sys.platform == "darwin" else ())
    missing_modules = check_modules(names)
    missing_files = check_files(root)
    speech, writing, sound = check_speech(), check_writing(), check_computer_sound()
    libraries = check_libraries()
    imported = check_audio(audio) if audio else None
    try:
        version = _import("__init__").__version__      # src/__init__.py, by either name
    except Exception:
        version = ""
    optional_missing = [what for what, there in (
        ("speech engine", speech["runs"]),
        ("writing engine", writing["runs"] or bool(writing.get("comes_with_model"))),
        ("computer-sound helper", sound["present"] or not sound["needed"]),
        ("anthropic", libraries["anthropic"]["present"]),
        ("ffmpeg for Import audio", libraries["ffmpeg"]["present"])) if not there]
    ok = (not missing_modules and not missing_files and not (complete and optional_missing)
          and (imported is None or imported["decoded"]))
    return {
        "ok": ok,
        "version": version,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": platform.python_version(),
        "machine": f"{sys.platform} {platform.machine()}",
        "modules": {"checked": len(names), "missing": missing_modules},
        "files": {"checked": len(FILES), "missing": missing_files},
        "engines": {"speech": speech, "writing": writing},
        "computer_sound_helper": sound,
        "libraries": libraries,
        "meeting_keys": check_meeting_keys(),
        **({"audio": imported} if imported is not None else {}),
        "optional_missing": optional_missing,
        "complete_required": bool(complete),
    }


def main(argv=None, root: Path | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    # The engines write their own lines to the real stdout as they start
    # (llama.cpp's Metal start-up does). Only the JSON may be there, so the
    # real stdout is kept aside and file descriptor 1 points at stderr while
    # the check runs. A windowed Windows build has no stdout at all: then
    # there is nothing to keep aside, and only the exit code says how it went.
    try:
        real_out = os.dup(1)
        os.dup2(2, 1)
    except OSError:
        real_out = None
    own_data = None
    if not os.environ.get("WAFFLER_DATA_DIR", "").strip():
        own_data = tempfile.mkdtemp(prefix="waffler-self-check-")
        os.environ["WAFFLER_DATA_DIR"] = own_data
    try:
        audio = argv[argv.index("--audio") + 1] if "--audio" in argv[:-1] else None
        result = run(root, complete="--complete" in argv, audio=audio)
    except Exception as e:                           # the check itself must still answer
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if own_data:
            shutil.rmtree(own_data, ignore_errors=True)
    if real_out is not None:
        try:
            os.dup2(real_out, 1)
            os.write(real_out, (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
            os.close(real_out)
        except OSError:
            pass
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
