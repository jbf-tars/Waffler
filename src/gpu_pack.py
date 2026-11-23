"""NVIDIA's libraries, fetched when the person asks, so the speech model on
this computer runs on an NVIDIA graphics card (Windows).

faster-whisper uses the card when cuBLAS 12 and cuDNN 9 load
(src/local_stt_ct2.py), and the packaged app ships without them: about 1.8 GB.
Waffler fetches them itself, on one click in Settings and never on its own:
NVIDIA's own Windows wheels from PyPI, pinned like the models (version, size
and SHA-256) and resumed when cut short (src/local_stt.py _fetch). The DLLs
are taken out into <data folder>/gpu, where local_stt_ct2 looks for them, the
wheels are deleted, and the card is used from the next dictation, with no
restart.

Measured on an RTX 4070 SUPER (Windows 11, the `small` model, an 11.5 s clip
decoded as the app decodes): 0.24 s on the card against 1.3 s on the
processor. A real 2.4 s dictation was written down in 0.43 s, where real
dictations on the processor had taken 2.4 to 4.5 s. The real download, through
this module: 1,249 MB fetched in 42 s, checked and unpacked in 54 s in all,
and a fresh process with only these files ran the model on the card in 0.22
to 0.36 s.

Offered only on Windows with the faster-whisper engine, an NVIDIA card the
driver reports with room on it for the small model, and the libraries not
already loadable from somewhere else (a CUDA install, pip's nvidia wheels).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable, NamedTuple, Optional

try:
    import local_stt as _stt
    from data_paths import data_dir
except ImportError:  # imported as src.gpu_pack
    from src import local_stt as _stt
    from src.data_paths import data_dir

# ── what is fetched ──────────────────────────────────────────────────────────

_PYPI = "https://files.pythonhosted.org/packages"


class Wheel(NamedTuple):
    file: "_stt.ModelFile"          # name, url, size, sha256: checked like a model file
    member_dir: str                 # where its DLLs sit inside the wheel


# NVIDIA's own wheels for Windows, pinned. CTranslate2 4.x needs CUDA 12's
# cuBLAS and cuDNN 9 (src/local_stt_ct2.py _WIN_CUDA_DLLS); within a major
# version both keep their interface, and these were run on the card here
# before they were pinned.
WHEELS = (
    Wheel(_stt.ModelFile(
        "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
        f"{_PYPI}/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
        "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
        553162896, "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661"),
        "nvidia/cublas/bin/"),
    Wheel(_stt.ModelFile(
        "nvidia_cudnn_cu12-9.28.1.4-py3-none-win_amd64.whl",
        f"{_PYPI}/c7/bd/1d6709d2223e99f04079f16005f8ae6e11a14ee16b5eab6833686cf72fe1/"
        "nvidia_cudnn_cu12-9.28.1.4-py3-none-win_amd64.whl",
        696300805, "3467aafa9b260e6adf5fbbfc0e21d180133305b42032aa1160be2d3142bb8b09"),
        "nvidia/cudnn/bin/"),
)
DOWNLOAD_BYTES = sum(w.file.size for w in WHEELS)
# What the DLLs take on disk once taken out of the wheels, for the free-space
# check: 12 files, 1,855,393,376 bytes, measured on these two wheels.
UNPACKED_BYTES = 1_855_393_376
# A BLAS shim for other programs: nothing here loads it.
_SKIP = {"nvblas64_12.dll"}

# ── plain sentences ──────────────────────────────────────────────────────────

DOWNLOAD_FAILED = ("The graphics card files couldn't be downloaded. Check you're online and "
                   "try again: it carries on from where it stopped.")
DOWNLOAD_DAMAGED = "The download was damaged, so it was thrown away. Try again."
NO_SPACE = "There isn't enough free disk space for the graphics card files."
BUSY = "The graphics card files are already downloading."
NOT_OFFERED = "This computer has no NVIDIA graphics card Waffler can use."
UNPACK_FAILED = "The graphics card files couldn't be unpacked. Try again."
IN_USE = "Some files are in use. They go when Waffler next starts."

_READY = ".ready"
_REMOVE = ".remove"
_WHEELS_DIR = ".download"
_SPACE_MARGIN = 300 * 1024 * 1024
# The small model's float16 need plus local_stt_ct2's headroom: a card with
# less memory than this in all would never be used, so it is not offered.
_MIN_CARD_MB = 900 + 1024

_lock = threading.Lock()                     # guards _dl, _thread, _card_cache, _elsewhere
_dl: dict = {"active": False, "done": 0, "error": "", "cancelled": False}
_cancel = threading.Event()
_thread: Optional[threading.Thread] = None
_card_cache: list = []                       # [None] or [{"count", "total_mb"}], read once
_elsewhere: list = []                        # [bool]: the libraries load without this pack


def _http_get(url: str, headers: dict):
    """The streamed GET the speech model uses. Replaced in tests."""
    return _stt._http_get(url, headers)


def gpu_dir() -> Path:
    return data_dir() / "gpu"


# ── the card and the libraries ───────────────────────────────────────────────

def _ct2():
    return _stt._ct2()


def _total_vram_mb() -> Optional[int]:
    """All the memory on the first NVIDIA card, in MB; None when unreadable."""
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            info = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0))
            return int(info.total) // (1024 * 1024)
        finally:
            pynvml.nvmlShutdown()
    except Exception:           # noqa: BLE001 - not installed: ask nvidia-smi
        pass
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode == 0:
            return int(out.stdout.strip().splitlines()[0].strip())
    except Exception:           # noqa: BLE001
        pass
    return None


def _read_card() -> Optional[dict]:
    """An NVIDIA card this pack would help, or None. Asks the driver through
    CTranslate2 (no cuBLAS needed for that). Replaced in tests."""
    if sys.platform != "win32":
        return None
    try:
        if _stt.engine_name() != _stt.ENGINE_CT2 or not _stt.is_available():
            return None
        import ctranslate2
        count = int(ctranslate2.get_cuda_device_count())
    except Exception:           # noqa: BLE001 - no engine, no driver: no card
        return None
    if count < 1:
        return None
    total = _total_vram_mb()
    if total is not None and total < _MIN_CARD_MB:
        return None
    return {"count": count, "total_mb": total}


def card() -> Optional[dict]:
    """_read_card(), read once."""
    with _lock:
        if _card_cache:
            return _card_cache[0]
    found = _read_card()
    with _lock:
        if not _card_cache:
            _card_cache.append(found)
        return _card_cache[0]


def _linked(folder: Path) -> bool:
    """Is the gpu folder a link someone made by hand? Then it is theirs."""
    try:
        if folder.is_symlink():
            return True
        # A junction (mklink /J) is a reparse point that is not a symlink.
        attrs = getattr(os.lstat(folder), "st_file_attributes", 0)
        return bool(attrs & 0x400)                  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return False


def _loads_elsewhere() -> bool:
    """Do the libraries load without this pack (a CUDA install, pip's nvidia
    wheels, a folder put there by hand)? Asked once: it maps the DLLs."""
    with _lock:
        if _elsewhere:
            return _elsewhere[0]
    if _linked(gpu_dir()):
        ok = True
    else:
        try:
            ok = bool(_ct2()._cuda_usable()[0])
        except Exception:       # noqa: BLE001
            ok = False
    with _lock:
        if not _elsewhere:
            _elsewhere.append(ok)
        return _elsewhere[0]


# ── here or not ──────────────────────────────────────────────────────────────

def _mark() -> Optional[dict]:
    try:
        data = json.loads((gpu_dir() / _READY).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else None
    except (OSError, ValueError):
        return None


def is_ready() -> bool:
    """Every DLL this pack put here is here, at the size it was taken out at
    (the mark is written only after both wheels were checked and unpacked)."""
    mark = _mark()
    if not mark or not mark["files"] or (gpu_dir() / _REMOVE).exists():
        return False
    try:
        return all((gpu_dir() / name).stat().st_size == size for name, size in mark["files"].items())
    except (OSError, TypeError):
        return False


def _size_here() -> int:
    mark = _mark()
    total = 0
    for name in (mark or {}).get("files", {}):
        try:
            total += (gpu_dir() / name).stat().st_size
        except OSError:
            pass
    return total


def _wheel_bytes() -> int:
    folder = gpu_dir() / _WHEELS_DIR
    return sum(_stt._bytes_here(folder, w.file) for w in WHEELS)


def status() -> dict:
    """For the page. Cheap after the first call: file sizes and two answers
    read once. ``state``: "no_card" (not offered), "not_needed" (the libraries
    load already, not offered), "not_downloaded", "downloading", "ready"."""
    here = card()
    with _lock:
        downloading = _dl["active"]
        done, error, cancelled = _dl["done"], _dl["error"], _dl["cancelled"]
    base = {"offered": False, "state": "no_card", "percent": 0, "bytes_done": 0,
            "bytes_total": DOWNLOAD_BYTES, "size_on_disk": 0, "error": "", "cancelled": False}
    if here is None:
        return base
    if downloading:
        state = "downloading"
    elif is_ready():
        state = "ready"
    elif _loads_elsewhere():
        return {**base, "state": "not_needed"}
    else:
        state = "not_downloaded"
        done = _wheel_bytes()
    if state == "ready":
        done = DOWNLOAD_BYTES
    return {**base, "offered": True, "state": state,
            "percent": int(done * 100 / DOWNLOAD_BYTES) if DOWNLOAD_BYTES else 0,
            "bytes_done": done, "size_on_disk": _size_here(),
            "error": error if state == "not_downloaded" else "",
            "cancelled": cancelled and state == "not_downloaded"}


# ── fetching ─────────────────────────────────────────────────────────────────

class _Cancelled(Exception):
    pass


class PackError(Exception):
    pass


def _check_space(folder: Path) -> None:
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < DOWNLOAD_BYTES - _wheel_bytes() + UNPACKED_BYTES + _SPACE_MARGIN:
        raise PackError(NO_SPACE)


def _unpack(folder: Path, wheels: Path) -> dict:
    """Each wheel's DLLs into `folder`, under their own names. Only files that
    sit directly in the wheel's bin folder are taken. Returns {name: size}."""
    taken: dict = {}
    for w in WHEELS:
        try:
            with zipfile.ZipFile(wheels / w.file.name) as z:
                for info in z.infolist():
                    if not info.filename.startswith(w.member_dir):
                        continue
                    name = info.filename[len(w.member_dir):]
                    if not name.lower().endswith(".dll") or "/" in name or "\\" in name or name in _SKIP:
                        continue
                    part = folder / (name + ".part")
                    with z.open(info) as src, open(part, "wb") as out:
                        while True:
                            if _cancel.is_set():
                                raise _Cancelled()
                            block = src.read(1024 * 1024)
                            if not block:
                                break
                            out.write(block)
                    os.replace(part, folder / name)
                    taken[name] = info.file_size
        except (zipfile.BadZipFile, KeyError) as e:
            raise PackError(UNPACK_FAILED) from e
    names = [n.lower() for n in taken]
    if not any(n.startswith("cublas64_") for n in names) or not any(n.startswith("cudnn") for n in names):
        raise PackError(UNPACK_FAILED)
    return taken


def _download(progress_cb) -> None:
    folder = gpu_dir()
    wheels = folder / _WHEELS_DIR
    wheels.mkdir(parents=True, exist_ok=True)
    (folder / _REMOVE).unlink(missing_ok=True)
    if is_ready():
        _note(DOWNLOAD_BYTES, progress_cb, [None])
        return
    (folder / _READY).unlink(missing_ok=True)
    _check_space(folder)
    per_file = {w.file.name: _stt._bytes_here(wheels, w.file) for w in WHEELS}
    last = [None]

    def note(file_name: str, n: int) -> None:
        per_file[file_name] = n
        _note(sum(per_file.values()), progress_cb, last)

    for w in WHEELS:
        if _cancel.is_set():
            raise _Cancelled()
        try:
            _stt._fetch(w.file, wheels, note, _cancel, get=_http_get)
        except _stt._Cancelled as e:
            raise _Cancelled() from e
        except _stt.LocalSTTError as e:
            raise PackError(DOWNLOAD_DAMAGED if str(e) == _stt.DOWNLOAD_DAMAGED else DOWNLOAD_FAILED) from e
    taken = _unpack(folder, wheels)
    (folder / _READY).write_text(json.dumps({"files": taken, "wheels": [w.file.name for w in WHEELS]}),
                                 encoding="utf-8")
    shutil.rmtree(wheels, ignore_errors=True)
    _note(DOWNLOAD_BYTES, progress_cb, last)
    arrived()


def _note(done: int, progress_cb, last: list) -> None:
    with _lock:
        _dl["done"] = done
    percent = int(done * 100 / DOWNLOAD_BYTES) if DOWNLOAD_BYTES else 0
    if progress_cb is not None and percent != last[0]:
        last[0] = percent
        try:
            progress_cb(percent, done, DOWNLOAD_BYTES)
        except Exception:       # noqa: BLE001 - the page's trouble is not the download's
            pass


def arrived() -> None:
    """The libraries are here now: the speech engine looks again, and the
    machine is read again (the card can take a bigger model), with no restart."""
    with _lock:
        _elsewhere.clear()
    try:
        _ct2().gpu_libraries_arrived()
    except Exception:           # noqa: BLE001
        pass
    try:
        _stt.forget_machine()
    except Exception:           # noqa: BLE001
        pass


def _run(progress_cb) -> dict:
    error, cancelled = "", False
    try:
        _download(progress_cb)
    except _Cancelled:
        cancelled = True
    except PackError as e:
        error = str(e)
    except OSError as e:
        error = NO_SPACE if getattr(e, "errno", None) == 28 else DOWNLOAD_FAILED
    except Exception:           # noqa: BLE001 - never let a download take a thread down
        error = DOWNLOAD_FAILED
    finally:
        with _lock:
            _dl.update(active=False, error=error, cancelled=cancelled)
    result = status()
    return {**result, "ok": result["state"] == "ready", "cancelled": cancelled}


def _begin() -> Optional[dict]:
    """None when the download is ours to run; else the answer to give."""
    now = status()
    if card() is None:
        return {**now, "ok": False, "error": NOT_OFFERED}
    if now["state"] == "not_needed":            # nothing to fetch, nothing to write over
        return {**now, "ok": True, "nothing_to_do": True}
    with _lock:
        if _dl["active"]:
            return {"busy": True}
        _dl.update(active=True, done=_wheel_bytes(), error="", cancelled=False)
        _cancel.clear()
    return None


def download(progress_cb: Optional[Callable[[int, int, int], None]] = None) -> dict:
    """Fetch and unpack, and wait. Safe to call again after a failure or a
    cancel: what arrived is kept. Never raises."""
    refused = _begin()
    if refused is not None:
        return {**status(), "ok": False, "error": BUSY} if refused.get("busy") else refused
    return _run(progress_cb)


def start_download(progress_cb=None, done_cb=None) -> dict:
    """download() on a thread of its own; returns at once with the status
    already saying "downloading". ``done_cb(result)`` gets download()'s answer."""
    global _thread
    refused = _begin()
    if refused is not None:
        if refused.get("busy"):
            return {**status(), "ok": True, "started": False}
        return {**refused, "started": False}


    def run():
        result = _run(progress_cb)
        if done_cb is not None:
            try:
                done_cb(result)
            except Exception:   # noqa: BLE001
                pass
    thread = threading.Thread(target=run, daemon=True, name="GpuPackDownload")
    with _lock:
        _thread = thread
    thread.start()
    return {**status(), "ok": True, "started": True}


def cancel_download() -> bool:
    with _lock:
        if not _dl["active"]:
            return False
        _cancel.set()
        return True


def _wait(timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            if not _dl["active"]:
                return
        time.sleep(0.02)


# ── removing ─────────────────────────────────────────────────────────────────

def _ours(folder: Path) -> list:
    """The paths this pack put in `folder`: the DLLs its mark lists, the mark,
    and the wheels folder. Nothing else there is touched (a folder linked
    there by hand is someone else's)."""
    mark = _mark() or {"files": {}}
    paths = [folder / name for name in mark["files"]]
    paths += [folder / _READY, folder / _WHEELS_DIR]
    paths += list(folder.glob("*.dll.part"))
    return paths


def _delete(paths: list) -> list:
    left = []
    for p in paths:
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
        except PermissionError:
            left.append(p)
        except OSError:
            if p.exists():
                left.append(p)
    return left


def remove() -> dict:
    """Delete the pack. A DLL the engine has loaded cannot be deleted while
    Waffler runs: then the rest goes now, and it at the next start
    (finish_pending_removal). Returns ``freed_bytes`` and ``pending``."""
    if cancel_download():
        _wait()
    folder = gpu_dir()
    before = _size_here() + _wheel_bytes()
    left = _delete(_ours(folder)) if folder.is_dir() else []
    if left:
        try:
            (folder / _REMOVE).write_text(json.dumps([p.name for p in left]), encoding="utf-8")
        except OSError:
            pass
    with _lock:
        _dl.update(done=0, error="", cancelled=False)
        _elsewhere.clear()
    try:
        _stt.forget_machine()
    except Exception:           # noqa: BLE001
        pass
    after = sum(p.stat().st_size for p in left if p.is_file())
    return {**status(), "ok": True, "freed_bytes": max(0, before - after), "pending": bool(left),
            "note": IN_USE if left else ""}


def finish_pending_removal() -> bool:
    """At start: delete what a removal could not, before anything loads it."""
    folder = gpu_dir()
    try:
        names = json.loads((folder / _REMOVE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    left = _delete([folder / n for n in names if isinstance(n, str) and "/" not in n and "\\" not in n])
    if not left:
        (folder / _REMOVE).unlink(missing_ok=True)
    return not left
