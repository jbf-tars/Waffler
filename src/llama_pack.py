"""llama.cpp's own server, fetched with the writing model on Windows, so the
tidy-up and meeting notes run on this computer there too (src/local_llm.py,
src/local_llm_server.py).

The Mac runs the writing model through llama-cpp-python, which has no Windows
build Waffler can ship. llama.cpp's official Windows releases carry
``llama-server.exe``, built for the processor, and for NVIDIA cards with the
CUDA runtime beside it. This fetches one of them with the model, pinned like
the models (release tag, size, SHA-256) and resumed when cut short
(src/local_stt.py _fetch), and unpacks it into <data folder>/llama, where
local_llm_server looks:

* a PC with an NVIDIA card (src/gpu_pack.py card): the CUDA build, 255 MB,
  and NVIDIA's CUDA runtime, 3.6 MB (cuBLAS comes with the graphics card
  files, src/gpu_pack.py);
* any other PC: the processor build, 18.6 MB.

Measured on an RTX 4070 SUPER with the writing model: a dictation tidied in
0.4 to 0.8 s on the card, 3.9 to 5.7 s on the processor (i7-14700F).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Callable, NamedTuple, Optional

try:
    import local_stt as _stt
    import gpu_pack as _gpu
    from data_paths import data_dir
except ImportError:  # imported as src.llama_pack
    from src import local_stt as _stt
    from src import gpu_pack as _gpu
    from src.data_paths import data_dir

_RELEASE = "https://github.com/ggml-org/llama.cpp/releases/download/b11168"


class Asset(NamedTuple):
    file: "_stt.ModelFile"
    member_dir: str = ""            # "" for a release zip (its files at its root)


CPU = (Asset(_stt.ModelFile(
    "llama-b11168-bin-win-cpu-x64.zip", f"{_RELEASE}/llama-b11168-bin-win-cpu-x64.zip",
    18567853, "ee7fb4fca17b3115fb382c9533e5eab9a05864b4e4bcc204113bc0095e17c0e8")),)
CUDA = (Asset(_stt.ModelFile(
    "llama-b11168-bin-win-cuda-12.4-x64.zip", f"{_RELEASE}/llama-b11168-bin-win-cuda-12.4-x64.zip",
    254719444, "70243b129dff464a230790538f8c1a8be2dc036b715e318a0b9ab8703e504459")),
    Asset(_stt.ModelFile(
        "nvidia_cuda_runtime_cu12-12.9.79-py3-none-win_amd64.whl",
        "https://files.pythonhosted.org/packages/59/df/e7c3a360be4f7b93cee39271b792669baeb3846c58a4df6dfcf187a7ffab/"
        "nvidia_cuda_runtime_cu12-12.9.79-py3-none-win_amd64.whl",
        3591604, "8e018af8fa02363876860388bd10ccb89eb9ab8fb0aa749aaf58430a9f7c4891"),
        "nvidia/cuda_runtime/bin/"))

SERVER = "llama-server.exe"
_READY = ".ready"
_DOWNLOADS = ".download"

UNPACK_FAILED = "The writing engine couldn't be unpacked. Try again."


class PackError(Exception):
    pass


class Cancelled(Exception):
    pass


def supported() -> bool:
    """Is there a build for this computer? 64-bit Windows."""
    return sys.platform == "win32" and sys.maxsize > 2 ** 32


def folder() -> Path:
    return data_dir() / "llama"


def plan() -> tuple:
    """What this computer gets: "cuda" or "cpu", and its assets."""
    try:
        has_card = _gpu.card() is not None
    except Exception:           # noqa: BLE001
        has_card = False
    return ("cuda", CUDA) if has_card else ("cpu", CPU)


def _mark() -> Optional[dict]:
    try:
        data = json.loads((folder() / _READY).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else None
    except (OSError, ValueError):
        return None


def is_ready() -> bool:
    """The server and every file unpacked with it are here, at their sizes."""
    mark = _mark()
    if not mark or SERVER not in mark["files"]:
        return False
    try:
        return all((folder() / n).stat().st_size == s for n, s in mark["files"].items())
    except (OSError, TypeError):
        return False


def parts() -> dict:
    """This computer's plan as {file: (size, bytes already here)}; {} when the
    server is here."""
    if is_ready():
        return {}
    _kind, assets = plan()
    here = folder() / _DOWNLOADS
    return {a.file.name: (a.file.size, _stt._bytes_here(here, a.file)) for a in assets}


def bytes_left() -> int:
    """What is still to come down for this computer's plan (0 when ready)."""
    return sum(size - here for size, here in parts().values())


def size_on_disk() -> int:
    total = 0
    for name in (_mark() or {}).get("files", {}):
        try:
            total += (folder() / name).stat().st_size
        except OSError:
            pass
    return total


def _members(z: zipfile.ZipFile, member_dir: str):
    """(info, name) for each file to take: directly in `member_dir` of a wheel,
    or anywhere at the root of a release zip (one shared top folder dropped)."""
    infos = [i for i in z.infolist() if not i.is_dir()]
    if member_dir:
        prefix = member_dir
    else:
        tops = {i.filename.split("/", 1)[0] for i in infos}
        prefix = (tops.pop() + "/") if len(tops) == 1 and all("/" in i.filename for i in infos) else ""
    for info in infos:
        if not info.filename.startswith(prefix):
            continue
        name = info.filename[len(prefix):]
        if not name or "/" in name or "\\" in name or name in (".", "..") or ":" in name:
            continue
        yield info, name


def _unpack(dest: Path, archive: Path, member_dir: str, cancel) -> dict:
    taken = {}
    try:
        with zipfile.ZipFile(archive) as z:
            for info, name in _members(z, member_dir):
                part = dest / (name + ".part")
                with z.open(info) as src, open(part, "wb") as out:
                    while True:
                        if cancel.is_set():
                            raise Cancelled()
                        block = src.read(1024 * 1024)
                        if not block:
                            break
                        out.write(block)
                os.replace(part, dest / name)
                taken[name] = info.file_size
    except (zipfile.BadZipFile, KeyError) as e:
        raise PackError(UNPACK_FAILED) from e
    return taken


def fetch(note: Callable[[str, int], None], cancel, get=None) -> None:
    """Bring this computer's build here and unpack it. ``note(file, bytes)``
    as each file arrives (src/local_stt.py _fetch). Raises
    local_stt.LocalSTTError (download or check failed), Cancelled or PackError."""
    if is_ready():
        return
    kind, assets = plan()
    dest = folder()
    here = dest / _DOWNLOADS
    here.mkdir(parents=True, exist_ok=True)
    (dest / _READY).unlink(missing_ok=True)
    for a in assets:
        if cancel.is_set():
            raise Cancelled()
        try:
            _stt._fetch(a.file, here, note, cancel, get=get)
        except _stt._Cancelled as e:
            raise Cancelled() from e
    files: dict = {}
    for a in assets:
        files.update(_unpack(dest, here / a.file.name, a.member_dir, cancel))
    if SERVER not in files:
        raise PackError(UNPACK_FAILED)
    (dest / _READY).write_text(json.dumps({"engine": kind, "files": files,
                                           "assets": [a.file.name for a in assets]}), encoding="utf-8")
    shutil.rmtree(here, ignore_errors=True)


def remove() -> int:
    """Delete what this put here (the files its mark lists, and the downloads).
    Returns bytes freed. The server must not be running (local_llm releases
    the engine first)."""
    dest = folder()
    freed = 0
    mark = _mark() or {"files": {}}
    for name in list(mark["files"]) + [_READY]:
        p = dest / name
        try:
            freed += p.stat().st_size
            p.unlink()
        except OSError:
            pass
    shutil.rmtree(dest / _DOWNLOADS, ignore_errors=True)
    return freed
