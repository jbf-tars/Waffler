"""The audio converter "Import audio" needs on Windows, fetched the first time
someone imports a file soundfile cannot read (an m4a, a wma, a webm ...).

No GPL program or library goes in Waffler's installers (owner's decision,
26 Nov 2026), so FFmpeg is not bundled. An LGPL build of it comes down on
first use, the way the writing engine does (src/llama_pack.py): pinned like
the models (src/local_stt.py ModelFile: URL, size, SHA-256), resumed when cut
short (local_stt._fetch), and checked before anything is unpacked from it.
Nothing from it runs before that check. ffmpeg.exe is unpacked last, and it is
what src/meetings/audio_decode.py helper_path() looks for (the only lookup),
so finding it means everything beside it is there.

The build: BtbN/FFmpeg-Builds (ffmpeg.org's download page links it), the 9.0
release branch, shared libraries, LGPL, from the monthly build of 31 October
2026. BtbN keeps its last 14 daily builds and one a month for two years, so
this file should stay up until about October 2028: move the pin before then
(a new pin is fetched again, see is_ready).

Checked on a Windows PC from the program itself (26 Nov 2026):
  ffmpeg version n9.0.2-39-g7adfbe40dc-20261031
  configuration: --enable-version3; no --enable-gpl, no --enable-nonfree;
    --disable-libx264 --disable-libx265 --disable-libxvid
  ffmpeg -L: GNU Lesser General Public License, version 3 or later
ffmpeg.exe loads all seven of its libraries: 155 MB on disk with them, from a
76.6 MB download. ffprobe, ffplay, the headers and the documentation are not
kept; the licence (LICENSE.txt, LGPL-3.0) is.

The prompt (Offer): the import that found the converter missing waits in
app.py Api.meetings_api while the window asks "Import audio needs a one-time
download, 77 MB." (ui/decoder.js), then carries on with the file once it is
here, or answers as before when the person says Not now.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import zipfile
from pathlib import Path
from typing import Callable, Optional

try:
    import local_stt as _stt
    from meetings import audio_decode as _ad
except ImportError:  # imported as src.audio_decoder_pack
    from src import local_stt as _stt
    from src.meetings import audio_decode as _ad

_TOP = "ffmpeg-n9.0.2-39-g7adfbe40dc-win64-lgpl-shared-9.0"
ASSET = _stt.ModelFile(
    f"{_TOP}.zip",
    f"https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/{_TOP}.zip",
    76576818, "e907b01fa4783913151237e37df2a10fa97d99a1598a021bf59d4ae62ffed4c0")

# What is taken from the zip, in this order: ffmpeg.exe last.
KEEP = ("LICENSE.txt",
        "bin/avutil-61.dll", "bin/swresample-7.dll", "bin/swscale-10.dll", "bin/avcodec-63.dll",
        "bin/avformat-63.dll", "bin/avfilter-12.dll", "bin/avdevice-63.dll",
        "bin/ffmpeg.exe")

_READY = ".ready"
_DOWNLOADS = ".download"

UNPACK_FAILED = "The audio converter couldn't be unpacked. Try again."
DOWNLOAD_FAILED = ("The audio converter couldn't be downloaded. Check you're online and "
                   "import the file again: it carries on from where it stopped.")
NO_ROOM = "The audio converter couldn't be saved. Check there is free disk space and try again."


class PackError(Exception):
    pass


class Cancelled(Exception):
    pass


def supported() -> bool:
    """64-bit Windows: the only build there is."""
    return sys.platform == "win32" and sys.maxsize > 2 ** 32


def folder() -> Path:
    return _ad.helper_install_path().parent


def download_mb() -> int:
    return round(ASSET.size / 1e6)


def _mark() -> Optional[dict]:
    try:
        data = json.loads((folder() / _READY).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else None
    except (OSError, ValueError):
        return None


def is_ready() -> bool:
    """This pin's converter is here, every file at the size it was unpacked at."""
    mark = _mark()
    if not mark or mark.get("asset") != ASSET.name or _ad.helper_path("win32") is None:
        return False
    try:
        return all((folder() / n).stat().st_size == s for n, s in mark["files"].items())
    except (OSError, TypeError):
        return False


def bytes_left() -> int:
    if is_ready():
        return 0
    return ASSET.size - _stt._bytes_here(folder() / _DOWNLOADS, ASSET)


def failure_sentence(e: BaseException) -> str:
    """What the window says when fetching it failed."""
    if isinstance(e, PackError):
        return str(e) or UNPACK_FAILED
    if isinstance(e, _stt.LocalSTTError):
        return _stt.DOWNLOAD_DAMAGED if str(e) == _stt.DOWNLOAD_DAMAGED else DOWNLOAD_FAILED
    if isinstance(e, OSError):
        return NO_ROOM
    return DOWNLOAD_FAILED


def _unpack_one(z: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path, name: str, cancel) -> int:
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
    return info.file_size


def _unpack(archive: Path, dest: Path, cancel) -> dict:
    taken = {}
    try:
        with zipfile.ZipFile(archive) as z:
            infos = {i.filename: i for i in z.infolist()}
            wanted = [(infos.get(f"{_TOP}/{w}"), w.rsplit("/", 1)[-1]) for w in KEEP]
            if any(info is None for info, _ in wanted):
                raise PackError(UNPACK_FAILED)
            for info, name in wanted:
                taken[name] = _unpack_one(z, info, dest, name, cancel)
    except (zipfile.BadZipFile, KeyError) as e:
        raise PackError(UNPACK_FAILED) from e
    return taken


def fetch(note: Callable[[int, int], None], cancel: threading.Event, get=None) -> None:
    """Bring the converter here: downloaded (resumed), checked, unpacked.
    ``note(bytes so far, total)`` as it arrives. Raises local_stt.LocalSTTError
    (the download or its check failed), Cancelled, PackError or OSError."""
    if is_ready():
        return
    dest = folder()
    here = dest / _DOWNLOADS
    here.mkdir(parents=True, exist_ok=True)
    try:
        _stt._fetch(ASSET, here, lambda _name, n: note(n, ASSET.size), cancel, get=get)
    except _stt._Cancelled as e:
        raise Cancelled() from e
    # From here the download has matched its SHA-256. An older converter never
    # sits beside newer libraries: it goes first, and the new one comes last.
    (dest / _READY).unlink(missing_ok=True)
    (dest / _ad.HELPER_NAME).unlink(missing_ok=True)
    try:
        files = _unpack(here / ASSET.name, dest, cancel)
    except BaseException:
        for p in dest.glob("*.part"):
            p.unlink(missing_ok=True)
        (dest / _ad.HELPER_NAME).unlink(missing_ok=True)
        raise
    (dest / _READY).write_text(json.dumps({"asset": ASSET.name, "files": files}), encoding="utf-8")
    shutil.rmtree(here, ignore_errors=True)


class Offer:
    """One prompt at a time, for every import that finds the converter missing.

    ``run(show, progress, done)`` puts the prompt up with ``show(view)``
    (False: it could not be shown, and nothing waits), waits for
    ``answer()``, and on "download" fetches it, telling
    ``progress(bytes, total)``, and ends with ``done(ok, sentence)``. It
    answers True when the converter is here at the end. A second import waits
    for the first one's prompt, then asks again only if it is still missing.
    """

    WAIT_S = 600.0                  # no answer in ten minutes: Not now

    def __init__(self, fetch_fn=None):
        self._lock = threading.Lock()
        self._answered = threading.Event()
        self._cancel = threading.Event()
        self._choice = None
        self._fetch = fetch_fn or fetch

    def answer(self, choice) -> dict:
        self._choice = "download" if choice == "download" else "not_now"
        self._answered.set()
        return {"ok": True}

    def cancel(self) -> dict:
        self._cancel.set()
        if not self._answered.is_set():
            self.answer("not_now")
        return {"ok": True}

    def run(self, show, progress, done, ready=None) -> bool:
        ready = ready or is_ready
        with self._lock:
            if ready():
                return True
            self._answered.clear()
            self._cancel.clear()
            self._choice = None
            if not show({"stage": "offer", "mb": download_mb()}):
                return False
            if not self._answered.wait(self.WAIT_S) or self._choice != "download":
                done(False, "")
                return False
            try:
                self._fetch(progress, self._cancel)
            except Cancelled:
                done(False, "")
                return False
            except Exception as e:  # noqa: BLE001 — download, check, unpack, disk: one sentence
                done(False, failure_sentence(e))
                return False
            done(True, "")
            return True
