"""Speech to text on this computer: no key, and no network once the model is here.

One model, downloaded once, used by dictation (src/transcribe_whisper.py) and
by meetings (src/meetings/transcribe.py). ``transcription_mode`` in
settings.json says whether they use it: ``"local"`` (on this computer) or
``"cloud"`` (a provider, as before; also what a missing value means).

Two engines, each on the machines it suits (docs/OFFLINE-TRANSCRIPTION.md has
the measurements behind this):

* Apple Silicon: whisper.cpp through ``pywhispercpp`` (src/local_stt_whispercpp.py).
  It runs on the GPU through Metal.
* Windows and Intel Macs: faster-whisper, which is CTranslate2
  (src/local_stt_ct2.py). The CPU, or an NVIDIA card when one is usable.

Nothing here imports an engine until a transcription is asked for, so the app
starts as fast as it did, and starts at all on a machine without the packages.

The model lives in ``<data folder>/models/<name>/`` (src/data_paths.py), never
in the app or the repository. A download is resumed where it stopped, checked
against the size and SHA-256 written below, and can be cancelled. One model is
held in memory at a time; it is loaded at the first transcription and released
after ``idle_unload_s`` without one (five minutes unless set). Transcriptions
run one at a time; a dictation goes ahead of a waiting piece of a meeting.

Environment (for tests and diagnosis): ``WAFFLER_LOCAL_STT_ENGINE`` forces an
engine (``whispercpp`` or ``faster-whisper``); ``WAFFLER_LOCAL_STT_IDLE_S``
sets the idle period in seconds (0: never unload).
"""
from __future__ import annotations

import atexit
import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Callable, NamedTuple, Optional

import numpy as np

try:
    from data_paths import data_dir
except ImportError:  # imported as src.local_stt
    from src.data_paths import data_dir

# One module, whichever way it is imported. app.py has src/ on its path and
# says `import local_stt`; the meetings engine says `from src import
# local_stt`. Left alone those are two modules: two models in memory, two
# locks, and a download one of them cannot see.
for _alias in ("local_stt", "src.local_stt"):
    sys.modules.setdefault(_alias, sys.modules[__name__])

RATE = 16000

# ── the setting ──────────────────────────────────────────────────────────────

MODE_SETTING = "transcription_mode"
MODE_CLOUD = "cloud"
MODE_LOCAL = "local"
MODES = (MODE_CLOUD, MODE_LOCAL)
# Optional, for diagnosis: the name of a model in MODELS. Absent: the one
# already downloaded, else the one that suits this machine (default_model).
MODEL_SETTING = "local_stt_model"

MODE_UNKNOWN = "Waffler doesn't know that way of turning speech into text. Choose On this computer or Cloud."
CLOUD_NEEDS_KEY = "Cloud transcription needs a Groq or OpenAI key. Add one first, or stay on this computer."
NOT_DOWNLOADED = ("The speech model for this computer isn't downloaded yet. "
                  "Download it in Settings, then try again.")
NOT_INSTALLED = "This copy of Waffler can't turn speech into text on this computer."
DOWNLOAD_FAILED = ("The speech model couldn't be downloaded. Check you're online and try again: "
                   "it carries on from where it stopped.")
DOWNLOAD_DAMAGED = "The download was damaged, so it was thrown away. Try again."
DOWNLOAD_NO_SPACE = "There isn't enough free disk space for the speech model."
DOWNLOAD_BUSY = "The speech model is already downloading."
LOAD_FAILED = "The speech model on this computer couldn't be opened. Remove it in Settings and download it again."
TRANSCRIBE_FAILED = "Speech couldn't be turned into text on this computer. Nothing was sent anywhere."


_PLAIN = (NOT_DOWNLOADED, NOT_INSTALLED, LOAD_FAILED, TRANSCRIBE_FAILED)


def plain_reason(reason) -> str:
    """``reason`` when it is one of this module's own sentences about a
    transcription that could not be done here, else "". For the message shown
    when a dictation is kept instead of pasted."""
    text = str(reason or "")
    return text if text in _PLAIN else ""


def mode_of(stored) -> str:
    """``"local"`` only when settings.json says exactly that; else ``"cloud"``."""
    if isinstance(stored, dict) and stored.get(MODE_SETTING) == MODE_LOCAL:
        return MODE_LOCAL
    return MODE_CLOUD


def _read_settings() -> dict:
    """settings.json as it is on disk now ({} when missing or unreadable)."""
    try:
        data = json.loads((data_dir() / "settings.json").read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def mode_is_local(stored=None) -> bool:
    """Is speech turned into text on this computer? Read from settings.json at
    the time of asking when ``stored`` is not given, so a change in Settings
    applies to the next dictation without a restart."""
    return mode_of(_read_settings() if stored is None else stored) == MODE_LOCAL


def touches(payload) -> bool:
    return isinstance(payload, dict) and MODE_SETTING in payload


def settings_problem(payload, has_key: bool = True) -> Optional[str]:
    """A plain sentence when a save names a mode Waffler does not have, or
    asks for the cloud with no key to use it; else None."""
    if not touches(payload):
        return None
    if payload[MODE_SETTING] not in MODES:
        return MODE_UNKNOWN
    if payload[MODE_SETTING] == MODE_CLOUD and not has_key:
        return CLOUD_NEEDS_KEY
    return None


class LocalSTTError(Exception):
    """A failure with a sentence fit to show the person. ``kind`` is
    'unavailable' (no engine in this build), 'not_downloaded', 'download',
    'cancelled', 'busy' or 'failed'."""

    def __init__(self, message: str, kind: str = "failed"):
        super().__init__(message)
        self.kind = kind


# ── the models ───────────────────────────────────────────────────────────────

ENGINE_WHISPERCPP = "whispercpp"
ENGINE_CT2 = "faster-whisper"
_ENGINE_PACKAGE = {ENGINE_WHISPERCPP: "pywhispercpp", ENGINE_CT2: "faster_whisper"}

_HF = "https://huggingface.co"


class ModelFile(NamedTuple):
    name: str
    url: str
    size: int
    sha256: str


class ModelSpec(NamedTuple):
    engine: str
    label: str
    files: tuple

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


def _files(repo: str, revision: str, *entries) -> tuple:
    return tuple(ModelFile(name, f"{_HF}/{repo}/resolve/{revision}/{name}", size, sha)
                 for name, size, sha in entries)


# Every file is pinned to a commit of its repository, with its size and
# SHA-256, so what is loaded is exactly what was measured.
_GGML = ("ggerganov/whisper.cpp", "5359861c739e955e79d9a303bcbc70fb988958b1")
_CT2_TOKENIZER = ("tokenizer.json", 2203239,
                  "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab")
_CT2_VOCAB = ("vocabulary.txt", 459861,
              "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913")

MODELS: dict = {
    # whisper.cpp (Apple Silicon)
    "large-v3-turbo-q5": ModelSpec(ENGINE_WHISPERCPP, "Whisper large v3 turbo", _files(
        *_GGML, ("ggml-large-v3-turbo-q5_0.bin", 574041195,
                 "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"))),
    "small-q5": ModelSpec(ENGINE_WHISPERCPP, "Whisper small", _files(
        *_GGML, ("ggml-small-q5_1.bin", 190085487,
                 "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb"))),
    # faster-whisper (Windows, Intel Macs)
    "small": ModelSpec(ENGINE_CT2, "Whisper small", _files(
        "Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120",
        ("config.json", 2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
        ("model.bin", 483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
        _CT2_TOKENIZER, _CT2_VOCAB)),
    "base": ModelSpec(ENGINE_CT2, "Whisper base", _files(
        "Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
        ("config.json", 2309, "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a"),
        ("model.bin", 145217532, "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"),
        _CT2_TOKENIZER, _CT2_VOCAB)),
    "large-v3-turbo": ModelSpec(ENGINE_CT2, "Whisper large v3 turbo", _files(
        "dropbox-dash/faster-whisper-large-v3-turbo", "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        ("config.json", 2263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
        ("model.bin", 1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
        ("preprocessor_config.json", 340,
         "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
        ("tokenizer.json", 2710337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
        ("vocabulary.json", 1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"))),
}

_READY_MARK = ".ready"
_PART = ".part"


def engine_name() -> str:
    """The engine this machine uses."""
    forced = os.environ.get("WAFFLER_LOCAL_STT_ENGINE", "").strip()
    if forced in _ENGINE_PACKAGE:
        return forced
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return ENGINE_WHISPERCPP
    return ENGINE_CT2


def is_available() -> bool:
    """Is this machine's engine in this build? (Does not import it.)"""
    try:
        return importlib.util.find_spec(_ENGINE_PACKAGE[engine_name()]) is not None
    except (ImportError, ValueError):
        return False


def models_dir() -> Path:
    return data_dir() / "models"


def model_dir(name: str) -> Path:
    return models_dir() / name


def _models_for(engine: str) -> list:
    return [name for name, spec in MODELS.items() if spec.engine == engine]


_default_cache: dict = {}


def default_model() -> str:
    """The model that suits this machine. Apple Silicon: large-v3-turbo,
    quantised. Elsewhere faster-whisper decides from the hardware
    (src/local_stt_ct2.py auto_model): turbo on an NVIDIA card with room,
    else small or base on the CPU. Worked out once, as it reads hardware."""
    engine = engine_name()
    if engine not in _default_cache:
        if engine == ENGINE_WHISPERCPP:
            _default_cache[engine] = "large-v3-turbo-q5"
        else:
            try:
                _default_cache[engine] = _ct2().auto_model()
            except Exception:       # noqa: BLE001 - no engine, odd hardware: the safe middle
                _default_cache[engine] = "small"
    return _default_cache[engine]


# ── does this computer suit it? ──────────────────────────────────────────────

class Machine(NamedTuple):
    """What decides whether speech to text on this computer is a good idea."""
    system: str                     # platform.system(): "Darwin", "Windows", "Linux"
    arch: str                       # platform.machine(): "arm64", "x86_64", "AMD64"
    cores: int                      # logical cores
    ram_gb: Optional[float]         # total memory; None when it could not be read
    nvidia: bool = False            # a usable NVIDIA card with room for the large model


SUITS_APPLE_SILICON = "apple_silicon"
SUITS_NVIDIA = "nvidia"
SUITS_CPU = "cpu"
SMALL_MACHINE = "small_machine"

_machine_cache: list = []


def describe_machine() -> Machine:
    """This computer, read once (the NVIDIA check loads libraries). Apple
    Silicon is not probed at all: its answer does not depend on the rest."""
    if _machine_cache:
        return _machine_cache[0]
    system, arch = platform.system(), platform.machine()
    cores = os.cpu_count() or 1
    ram, nvidia = None, False
    if not (system == "Darwin" and arch == "arm64"):
        try:
            ram = _ct2()._total_ram_gb()
        except Exception:           # noqa: BLE001 - unknown memory promises nothing
            ram = None
        try:
            # default_model() has already asked the card (CUDA libraries and
            # free memory, src/local_stt_ct2.py auto_model): the large model
            # is its answer only when the card can take it.
            nvidia = engine_name() == ENGINE_CT2 and default_model() == _ct2().AUTO_GPU_MODEL
        except Exception:           # noqa: BLE001
            nvidia = False
    machine = Machine(system, arch, cores, ram, bool(nvidia))
    _machine_cache.append(machine)
    return machine


def machine_verdict(machine: Optional[Machine] = None) -> dict:
    """Does this computer suit speech to text on it? ``{"suits", "reason"}``,
    by the table in docs/OFFLINE-TRANSCRIPTION.md ("The models"):

    * Apple Silicon: yes (whisper.cpp on the GPU; ``"apple_silicon"``).
    * Windows with an NVIDIA card that has room for the large model: yes
      (``"nvidia"``). A card is no help to a Mac.
    * Windows or an Intel Mac on the processor: yes with 8+ logical cores and
      12+ GB of memory, where the ``small`` model runs (``"cpu"``).
    * Anything smaller, or a machine that could not be read: it works, with
      the ``base`` model, slowly and less accurately (``"small_machine"``).
      The setup screen then puts the key first and says so.

    Pure when given a Machine; with none it describes this one."""
    m = machine or describe_machine()
    if m.system == "Darwin" and m.arch == "arm64":
        return {"suits": True, "reason": SUITS_APPLE_SILICON}
    if m.nvidia and m.system != "Darwin":
        return {"suits": True, "reason": SUITS_NVIDIA}
    if _ct2().cpu_model_for(m.cores, m.ram_gb) == "small":
        return {"suits": True, "reason": SUITS_CPU}
    return {"suits": False, "reason": SMALL_MACHINE}


def selected_model(stored=None) -> str:
    """The one model in use: the one settings.json names (when this machine's
    engine has it), else one that is already downloaded, else default_model()."""
    engine = engine_name()
    names = _models_for(engine)
    chosen = (stored if isinstance(stored, dict) else _read_settings()).get(MODEL_SETTING)
    if isinstance(chosen, str) and chosen in names:
        return chosen
    for name in names:
        if _is_ready(name):
            return name
    return default_model()


def _is_ready(name: str) -> bool:
    """Every file of the model is here, whole, and was verified when it was
    downloaded (the mark is written only after the checks)."""
    spec = MODELS.get(name)
    if spec is None:
        return False
    folder = model_dir(name)
    try:
        if not (folder / _READY_MARK).is_file():
            return False
        return all((folder / f.name).stat().st_size == f.size for f in spec.files)
    except OSError:
        return False


def _dir_size(folder: Path) -> int:
    total = 0
    try:
        for p in folder.iterdir():
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


# ── downloading ──────────────────────────────────────────────────────────────

_CHUNK = 1024 * 1024
_SPACE_MARGIN = 200 * 1024 * 1024

_dl_lock = threading.Lock()                  # guards _dl and _dl_thread
_dl: dict = {"active": False, "model": "", "done": 0, "total": 0, "error": "", "cancelled": False}
_dl_cancel = threading.Event()
_dl_thread: Optional[threading.Thread] = None


class _Cancelled(Exception):
    pass


def _http_get(url: str, headers: dict):
    """One streamed GET. Replaced in tests. Returns an object with
    ``status_code``, ``iter_content(size)`` and ``close()``."""
    import requests
    return requests.get(url, headers=headers, stream=True, timeout=(15, 60))


def _sha256(path: Path, cancel: Optional[threading.Event] = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise _Cancelled()
            block = f.read(_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _bytes_here(folder: Path, f: ModelFile) -> int:
    """How much of this file has arrived: all of it, or its part-file."""
    try:
        final = folder / f.name
        if final.is_file() and final.stat().st_size == f.size:
            return f.size
        part = folder / (f.name + _PART)
        return min(part.stat().st_size, f.size) if part.is_file() else 0
    except OSError:
        return 0


def _fetch(f: ModelFile, folder: Path, note: Callable[[str, int], None],
           cancel: threading.Event) -> None:
    """Bring one file here: resumed from its part-file, checked, then given its
    name. A file already here at full size was checked when it arrived."""
    final = folder / f.name
    part = folder / (f.name + _PART)
    try:
        if final.is_file() and final.stat().st_size == f.size:
            note(f.name, f.size)
            return
    except OSError:
        pass
    have = part.stat().st_size if part.is_file() else 0
    if have > f.size:
        part.unlink()
        have = 0
    note(f.name, have)
    if have < f.size:
        try:
            resp = _http_get(f.url, {"Range": f"bytes={have}-"} if have else {})
        except Exception as e:      # noqa: BLE001 - offline, DNS, TLS: one sentence for all
            raise LocalSTTError(DOWNLOAD_FAILED, "download") from e
        try:
            status = getattr(resp, "status_code", 200)
            if status == 416:               # the part-file is not a prefix of this file
                part.unlink(missing_ok=True)
                note(f.name, 0)
                raise LocalSTTError(DOWNLOAD_FAILED, "download")
            if status not in (200, 206):
                raise LocalSTTError(DOWNLOAD_FAILED, "download")
            if status == 200 and have:      # the server sent the whole file again
                have = 0
                note(f.name, 0)
            out = open(part, "ab") if have else open(part, "wb")
            with out:
                try:
                    for chunk in resp.iter_content(_CHUNK):
                        if cancel.is_set():
                            raise _Cancelled()
                        if not chunk:
                            continue
                        out.write(chunk)
                        have += len(chunk)
                        note(f.name, min(have, f.size))
                        if have > f.size:
                            break
                except (_Cancelled, LocalSTTError):
                    raise
                except Exception as e:      # noqa: BLE001 - the connection dropped mid-file
                    raise LocalSTTError(DOWNLOAD_FAILED, "download") from e
        finally:
            try:
                resp.close()
            except Exception:       # noqa: BLE001
                pass
    if have < f.size:
        raise LocalSTTError(DOWNLOAD_FAILED, "download")
    if have > f.size or _sha256(part, cancel) != f.sha256:
        part.unlink(missing_ok=True)
        note(f.name, 0)
        raise LocalSTTError(DOWNLOAD_DAMAGED, "download")
    os.replace(part, final)


def _begin_download(name: str) -> Optional[dict]:
    """Mark the download as running. None when it is ours to run, else the
    answer to give (another is running, or the model is not one we have)."""
    spec = MODELS.get(name)
    if spec is None:
        return {**model_status(), "ok": False, "error": NOT_INSTALLED}
    with _dl_lock:
        if _dl["active"]:
            return {**_status_locked(_dl["model"]), "ok": False, "error": DOWNLOAD_BUSY}
        _dl.update(active=True, model=name, done=_dir_size(model_dir(name)), total=spec.size,
                   error="", cancelled=False)
        _dl_cancel.clear()
    return None


def _run_download(name: str, progress_cb) -> dict:
    """The download itself, after _begin_download. Never raises."""
    error, cancelled = "", False
    try:
        _download(name, MODELS[name], progress_cb)
    except _Cancelled:
        cancelled = True
    except LocalSTTError as e:
        error = str(e)
    except OSError as e:
        error = DOWNLOAD_NO_SPACE if getattr(e, "errno", None) == 28 else DOWNLOAD_FAILED
    except Exception:               # noqa: BLE001 - never let a download take a thread down
        error = DOWNLOAD_FAILED
    finally:
        with _dl_lock:
            _dl.update(active=False, error=error, cancelled=cancelled)
    with _dl_lock:
        status = _status_locked(name)
    return {**status, "ok": status["state"] == "ready", "cancelled": cancelled}


def download(progress_cb: Optional[Callable[[int, int, int], None]] = None,
             model: Optional[str] = None) -> dict:
    """Download the model and wait for it. ``progress_cb(percent, bytes_done,
    bytes_total)`` is called as the percentage moves.

    Safe to call again after a failure or a cancel: files that arrived are
    kept, and a file cut short carries on from where it stopped. Each file is
    checked against its size and SHA-256 before it is used. Returns
    model_status() with ``ok``; never raises."""
    name = model or selected_model()
    refused = _begin_download(name)
    if refused is not None:
        return refused
    return _run_download(name, progress_cb)


def _download(name: str, spec: ModelSpec, progress_cb) -> None:
    folder = model_dir(name)
    folder.mkdir(parents=True, exist_ok=True)
    if _is_ready(name):
        _note_progress(spec.size, spec.size, progress_cb, [None])
        return
    (folder / _READY_MARK).unlink(missing_ok=True)
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        free = None
    if free is not None and free < spec.size - _dir_size(folder) + _SPACE_MARGIN:
        raise LocalSTTError(DOWNLOAD_NO_SPACE, "download")
    # What is already here counts from the start, so a download that carries
    # on shows where it left off rather than starting again from nothing.
    per_file: dict = {f.name: _bytes_here(folder, f) for f in spec.files}
    last = [None]

    def note(file_name: str, n: int) -> None:
        per_file[file_name] = n
        _note_progress(sum(per_file.values()), spec.size, progress_cb, last)

    for f in spec.files:
        if _dl_cancel.is_set():
            raise _Cancelled()
        _fetch(f, folder, note, _dl_cancel)
    (folder / _READY_MARK).write_text(
        json.dumps({"model": name, "files": {f.name: f.size for f in spec.files}}),
        encoding="utf-8")
    _note_progress(spec.size, spec.size, progress_cb, last)


def _note_progress(done: int, total: int, progress_cb, last: list) -> None:
    with _dl_lock:
        _dl.update(done=done, total=total)
    percent = int(done * 100 / total) if total else 0
    if progress_cb is not None and percent != last[0]:
        last[0] = percent
        try:
            progress_cb(percent, done, total)
        except Exception:           # noqa: BLE001 - the page's trouble is not the download's
            pass


def start_download(progress_cb=None, done_cb=None) -> dict:
    """Start the download on a thread of its own and return at once, with the
    status already saying "downloading". ``done_cb(result)`` gets what
    download() would have returned. ``started`` is False when one was running
    already."""
    global _dl_thread
    name = selected_model()
    refused = _begin_download(name)
    if refused is not None:
        busy = refused.get("error") == DOWNLOAD_BUSY
        return {**refused, "ok": busy, "started": False, "error": "" if busy else refused["error"]}

    def run():
        result = _run_download(name, progress_cb)
        if done_cb is not None:
            try:
                done_cb(result)
            except Exception:       # noqa: BLE001
                pass
    thread = threading.Thread(target=run, daemon=True, name="LocalSTTDownload")
    with _dl_lock:
        _dl_thread = thread
        status = _status_locked(name)
    thread.start()
    return {**status, "ok": True, "started": True}


def cancel_download() -> bool:
    """Stop the download. What arrived is kept for the next try. True when a
    download was running."""
    with _dl_lock:
        if not _dl["active"]:
            return False
        _dl_cancel.set()
        return True


def _wait_for_download(timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _dl_lock:
            if not _dl["active"]:
                return
        time.sleep(0.02)


def remove_model(model: Optional[str] = None) -> dict:
    """Delete the model from this computer (a download in progress is stopped
    first, and the model is released from memory). Returns ``freed_bytes``."""
    name = model or selected_model()
    if cancel_download():
        _wait_for_download()
    _gate.acquire(background=False)
    try:
        if _engine_key == name:
            _release("removed")
        folder = model_dir(name)
        freed = _dir_size(folder)
        shutil.rmtree(folder, ignore_errors=True)
    finally:
        _gate.release()
    with _dl_lock:
        if _dl["model"] == name:
            _dl.update(done=0, total=0, error="", cancelled=False)
    return {**model_status(), "ok": not model_dir(name).exists(), "freed_bytes": freed}


def _status_locked(name: str) -> dict:
    """Holds _dl_lock."""
    spec = MODELS.get(name)
    total = spec.size if spec else 0
    downloading = _dl["active"] and _dl["model"] == name
    ready = (not downloading) and _is_ready(name)
    size_on_disk = _dir_size(model_dir(name))
    if downloading:
        state, done = "downloading", _dl["done"]
    elif ready:
        state, done = "ready", total
    else:
        state, done = "not_downloaded", min(size_on_disk, total)
    engine = spec.engine if spec else engine_name()
    return {
        "available": is_available(),          # is the engine in this build?
        "engine": engine,
        "model": name,
        "label": spec.label if spec else name,
        "state": state,                       # 'not_downloaded' | 'downloading' | 'ready'
        "percent": int(done * 100 / total) if total else 0,
        "bytes_done": done,
        "bytes_total": total,
        "size_on_disk": size_on_disk,
        "error": "" if (downloading or ready) else (_dl["error"] if _dl["model"] == name else ""),
        "cancelled": bool(_dl["cancelled"]) and _dl["model"] == name and not downloading and not ready,
        "loaded": _engine_key == name and _engine is not None,
        "device": _last["device"],            # what the last transcription ran on, or None
        "load_s": _last["load_s"],
        "transcribe_s": _last["transcribe_s"],
    }


def model_status(stored=None) -> dict:
    """For the page: not downloaded, downloading (with ``percent``) or ready,
    and the size on disk. Cheap enough to poll: it reads file sizes only."""
    name = selected_model(stored)
    with _dl_lock:
        if _dl["active"]:
            name = _dl["model"]
        return _status_locked(name)


# ── one transcription at a time ──────────────────────────────────────────────

class _Gate:
    """One caller at a time. A waiting dictation (foreground) goes before a
    waiting piece of a meeting (background)."""

    def __init__(self):
        self._cond = threading.Condition()
        self._busy = False
        self._foreground_waiting = 0

    def acquire(self, background: bool = False, blocking: bool = True) -> bool:
        with self._cond:
            if not blocking:
                if self._busy or (background and self._foreground_waiting):
                    return False
                self._busy = True
                return True
            if not background:
                self._foreground_waiting += 1
            try:
                while self._busy or (background and self._foreground_waiting):
                    self._cond.wait()
            finally:
                if not background:
                    self._foreground_waiting -= 1
            self._busy = True
            return True

    def release(self) -> None:
        with self._cond:
            self._busy = False
            self._cond.notify_all()


_gate = _Gate()
_engine = None                               # the loaded engine; only touched holding _gate
_engine_key: Optional[str] = None
_last: dict = {"device": None, "load_s": None, "transcribe_s": None, "unloaded": ""}
_last_used = 0.0
_now = time.monotonic                        # replaced in tests

DEFAULT_IDLE_UNLOAD_S = 300.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


_idle_unload_s = _env_float("WAFFLER_LOCAL_STT_IDLE_S", DEFAULT_IDLE_UNLOAD_S)
_idle_lock = threading.Lock()
_idle_timer: Optional[threading.Timer] = None


def _ct2():
    try:
        import local_stt_ct2
    except ImportError:  # imported as src.local_stt
        from src import local_stt_ct2
    return local_stt_ct2


def _load_engine(name: str):
    """Open the model. Replaced in tests. The engine has ``transcribe(audio,
    language, prompt)``, ``close()`` and ``device``."""
    spec = MODELS[name]
    folder = model_dir(name)
    if spec.engine == ENGINE_WHISPERCPP:
        try:
            import local_stt_whispercpp as mod
        except ImportError:  # imported as src.local_stt
            from src import local_stt_whispercpp as mod
        return mod.WhisperCppEngine(folder / spec.files[0].name)
    return _ct2().Ct2Engine(folder, name)


def _release(why: str) -> bool:
    """Give the model's memory back. Holds _gate."""
    global _engine, _engine_key
    if _engine is None:
        return False
    try:
        _engine.close()
    except Exception:               # noqa: BLE001
        pass
    _engine, _engine_key = None, None
    _last["unloaded"] = why
    return True


def unload() -> bool:
    """Release the model from memory now. The next transcription loads it
    again. True when one was loaded."""
    _cancel_idle_timer()
    _gate.acquire(background=False)
    try:
        return _release("on request")
    finally:
        _gate.release()


def _release_at_exit() -> None:
    """Close the model while Python is still whole. Left to the interpreter's
    own teardown, the engine's destructor runs after its module is gone and
    prints a traceback. If a transcription is still running it is left be."""
    _cancel_idle_timer()
    if _gate.acquire(background=False, blocking=False):
        try:
            _release("exit")
        finally:
            _gate.release()


atexit.register(_release_at_exit)


def warm() -> bool:
    """Load the model and run it once on a second of silence, so the first
    real dictation does not pay for it. Worth doing at start-up: the first
    use by a newly installed build took about ten seconds on an M5 (macOS
    checks and prepares the engine's libraries once per build), against half a
    second afterwards. Does nothing unless the model is here. Never raises.
    Blocks until done: call it on a thread."""
    try:
        if not is_available() or not _is_ready(selected_model()):
            return False
        transcribe_segments(np.zeros(RATE, dtype=np.float32), language="en", background=True)
        return True
    except Exception:               # noqa: BLE001
        return False


def set_idle_unload(seconds: Optional[float]) -> None:
    """The idle period after which the model is released; 0 or None keeps it."""
    global _idle_unload_s
    _idle_unload_s = float(seconds or 0)
    _arm_idle_timer()


def _cancel_idle_timer() -> None:
    global _idle_timer
    with _idle_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None


def _arm_idle_timer(delay: Optional[float] = None) -> None:
    global _idle_timer
    with _idle_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None
        if _idle_unload_s > 0 and _engine is not None:
            _idle_timer = threading.Timer(_idle_unload_s if delay is None else delay, _idle_fire)
            _idle_timer.daemon = True
            _idle_timer.name = "LocalSTTIdle"
            _idle_timer.start()


def _idle_fire() -> None:
    """Timer thread: release the model if nothing was transcribed for the idle
    period. It takes its turn like any caller, so it never pulls a model from
    under a transcription; if one is running it looks again in a moment."""
    with _idle_lock:
        if threading.current_thread() is not _idle_timer:
            return                  # superseded
    if not _gate.acquire(background=True, blocking=False):
        _arm_idle_timer(min(1.0, max(0.05, _idle_unload_s)))
        return
    try:
        if _engine is None or _idle_unload_s <= 0:
            return
        remaining = _idle_unload_s - (_now() - _last_used)
        if remaining > 0.01:
            _arm_idle_timer(remaining)
            return
        _release("idle")
    finally:
        _gate.release()


# ── transcription ────────────────────────────────────────────────────────────

def _as_samples(audio) -> np.ndarray:
    """WAV bytes or an array → 16 kHz mono float32 in -1..1."""
    if isinstance(audio, (bytes, bytearray, memoryview)):
        with wave.open(io.BytesIO(bytes(audio)), "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(w.getnframes())
        if width != 2:
            raise LocalSTTError(TRANSCRIBE_FAILED, "failed")
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            samples = samples[:len(samples) - len(samples) % channels].reshape(-1, channels).mean(axis=1)
        if rate != RATE and len(samples):
            n = int(round(len(samples) * RATE / float(rate)))
            samples = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)),
                                samples).astype(np.float32)
        return np.ascontiguousarray(samples, dtype=np.float32)
    arr = np.asarray(audio)
    if arr.dtype.kind in "iu":
        arr = arr.astype(np.float32) / 32768.0
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    return np.ascontiguousarray(arr, dtype=np.float32)


def transcribe_segments(audio, language: Optional[str] = None, prompt: Optional[str] = None,
                        background: bool = False) -> list:
    """Speech → Whisper segments (``start``, ``end``, ``text``, and
    ``avg_logprob`` / ``no_speech_prob`` where the engine gives them), worked
    out on this computer. ``audio`` is WAV bytes or 16 kHz mono samples.
    ``language`` None (or "auto") lets the model decide. ``prompt`` is words to
    listen for (the Vocabulary). ``background`` is for work nobody is waiting
    on (a meeting): a dictation that arrives goes first.

    Thread-safe. Raises LocalSTTError; never falls back to a cloud service."""
    global _engine, _engine_key, _last_used
    samples = _as_samples(audio)
    if not is_available():
        raise LocalSTTError(NOT_INSTALLED, "unavailable")
    name = selected_model()
    if not _is_ready(name):
        raise LocalSTTError(NOT_DOWNLOADED, "not_downloaded")
    if len(samples) == 0:
        return []
    language = None if language in (None, "", "auto") else str(language)
    _gate.acquire(background=background)
    try:
        if _engine is None or _engine_key != name:
            _release("replaced")
            t0 = time.perf_counter()
            try:
                _engine = _load_engine(name)
            except LocalSTTError:
                raise
            except Exception as e:  # noqa: BLE001
                raise LocalSTTError(LOAD_FAILED, "failed") from e
            _engine_key = name
            _last["load_s"] = round(time.perf_counter() - t0, 3)
        t1 = time.perf_counter()
        try:
            segments = _engine.transcribe(samples, language, prompt or None)
        except LocalSTTError:
            raise
        except Exception as e:      # noqa: BLE001
            raise LocalSTTError(TRANSCRIBE_FAILED, "failed") from e
        _last.update(transcribe_s=round(time.perf_counter() - t1, 3),
                     device=getattr(_engine, "device", None), unloaded="")
        return segments
    finally:
        _last_used = _now()
        _gate.release()
        _arm_idle_timer()


def transcribe(audio, language: Optional[str] = None, prompt: Optional[str] = None,
               background: bool = False) -> str:
    """Speech → text, on this computer. See transcribe_segments."""
    parts = (str(s.get("text") or "").strip()
             for s in transcribe_segments(audio, language, prompt, background))
    return " ".join(p for p in parts if p).strip()
