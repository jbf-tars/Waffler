"""The one place that knows how an audio file soundfile cannot read becomes
sound Waffler can write down ("Import audio" in Meetings, through
service.load_any_audio).

soundfile (libsndfile) reads wav, flac, aiff, ogg (Vorbis, Opus) and mp3
itself; those never come here. What does come here is m4a/AAC above all (an
iPhone voice memo), Apple Lossless, mp4/mov sound tracks, caf, 3gp, adts .aac,
and on Windows also wma, webm and the rest. No GPL program or library is in
either installer (owner's decision, 26 Nov 2026), so:

* macOS: the system's own ``/usr/bin/afconvert`` (part of every macOS; no
  licence question) converts to 16-bit PCM WAV at 16 kHz in a temporary
  folder, which is read with soundfile and removed. It reads m4a (AAC-LC,
  HE-AAC, HE-AACv2, Apple Lossless), mp4 and mov sound tracks, caf, 3gp,
  adts .aac, aiff, mp3 and, on recent macOS, Ogg. It does NOT read webm/mkv,
  wma/asf, avi, flv, RealMedia, Monkey's Audio or WavPack: for those the
  answer is one sentence naming what works (``Unsupported``). No second
  decoder is added.

* Windows: a decoder program fetched into the data folder the first time
  someone uses Import audio (``helper_path``). Until it is there
  ``available()`` says ``needs_download`` and ``decode`` raises
  ``NeedsDownload``. The Windows session implements the fetch (see
  ``helper_path``). The program is run with ``helper_args`` only: a plain
  decode of the first sound track to 16 kHz mono 16-bit PCM WAV, using only
  FFmpeg's native demuxers/decoders, libswresample and the native pcm_s16le
  writer, so an LGPL build of FFmpeg (no --enable-gpl, no --enable-nonfree,
  no external encoders) is enough.

* Anywhere else (Linux, a server): an ``ffmpeg`` the person installed on the
  PATH, run the same way as the Windows helper; none means ``how: "none"``.

Public surface (what load_any_audio, self_check and the bridge use)::

    decode(path, *, platform=None) -> (samples, rate)
        samples: 1-D float32 numpy array, mono, in [-1, 1]; rate: always RATE
        (16000). Raises one of the DecodeError subclasses below.
    available(platform=None) -> {"ready": bool, "needs_download": bool,
                                 "how": "system" | "download" | "none"}
    helper_path(platform=None) -> Path | None     (Windows: the fetched program)
    helper_install_path() -> Path                 (where the fetch must put it)
    helper_args(src, dst) -> list[str]            (the helper's command line)
    bridge_answer(err) -> {"ok": False, "reason": err.reason, "error": str(err)}

Exceptions (all RuntimeError, so callers that caught RuntimeError still do;
``str(e)`` is one plain sentence for the page, ``e.reason`` a fixed word):

    NotAudio        reason "not_audio"       not audio, or damaged
    NoSound         reason "no_sound"        a video with no sound track
    Protected       reason "protected"       DRM (FairPlay .m4p, encrypted mp4)
    Unsupported     reason "unsupported"     a format this Mac cannot read
    NeedsDownload   reason "needs_download"  Windows: the decoder is not here yet
    DecoderFailed   reason "decoder_failed"  the decoder broke or took too long

The bridge (src/meetings_bridge.py, POST /import) answers a failed import
with status 400 and the body
``{"detail": <sentence>, "ok": False, "reason": <reason>, "error": <sentence>}``.
The Meetings frame (ui/meetings-app, a build of another repo, never edited
here) shows ``detail`` as it shows any failed import. For
``reason == "needs_download"`` the parent window (app.py Api.meetings_api,
which passes the bridge's answer through) is where the Windows session
attaches the download prompt: it sees the reason before the frame does.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from .audioproc import RATE

AFCONVERT = "/usr/bin/afconvert"
HELPER_DIRNAME = "audio-decoder"            # <data folder>/audio-decoder/
HELPER_NAME = "ffmpeg.exe"                  # an LGPL FFmpeg build (Windows)

MAC_READS = "Waffler on Mac reads m4a, mp3, wav, aiff, flac, ogg and mp4. Convert this file first."
NOT_AUDIO = "This file isn't audio Waffler can read, or it is damaged."
NO_SOUND = "This file has no sound in it, only video."
PROTECTED = ("This file is copy-protected (bought from the iTunes Store, for example), "
             "so Waffler can't read it.")
NEEDS_DOWNLOAD = ("To read this kind of file, Waffler needs its audio converter, a one-time "
                  "download. Import the file again to get it.")
NO_DECODER = "This kind of audio file can't be read on this computer. Convert it to wav, mp3 or flac first."
TOO_SLOW = "Reading this audio file took too long, so Waffler stopped."
BROKE = "The audio converter couldn't read this file."


class DecodeError(RuntimeError):
    reason = "decoder_failed"
    sentence = BROKE

    def __init__(self, sentence: str | None = None, detail: str = ""):
        super().__init__(sentence or self.sentence)
        self.detail = detail                 # for the log: the decoder's last words


class NotAudio(DecodeError):
    reason, sentence = "not_audio", NOT_AUDIO


class NoSound(DecodeError):
    reason, sentence = "no_sound", NO_SOUND


class Protected(DecodeError):
    reason, sentence = "protected", PROTECTED


class Unsupported(DecodeError):
    reason, sentence = "unsupported", MAC_READS


class NeedsDownload(DecodeError):
    reason, sentence = "needs_download", NEEDS_DOWNLOAD


class DecoderFailed(DecodeError):
    reason, sentence = "decoder_failed", BROKE


def bridge_answer(err: DecodeError) -> dict:
    """What the page is told when an import fails here."""
    return {"ok": False, "reason": err.reason, "error": str(err)}


# ── where the decoder is ────────────────────────────────────────────────────

def _platform(platform: str | None) -> str:
    return platform or sys.platform


def _data_dir() -> Path:
    try:
        from src.data_paths import data_dir
    except ImportError:
        from data_paths import data_dir
    return data_dir()


def helper_install_path() -> Path:
    """Where the Windows decoder must be: <data folder>/audio-decoder/ffmpeg.exe."""
    return _data_dir() / HELPER_DIRNAME / HELPER_NAME


def helper_path(platform: str | None = None) -> Path | None:
    """The fetched decoder program on Windows, or None when it is not there
    yet (and always None elsewhere).

    TO DO, Windows session: fetch it in the style of src/llama_pack.py and
    src/gpu_pack.py: a pinned URL, size and SHA-256 of an LGPL FFmpeg build
    (no --enable-gpl, no --enable-nonfree), resumed when cut short
    (src/local_stt.py _fetch), progress to the page, unpacked so that the
    program lands at ``helper_install_path()``. Only ``ffmpeg.exe`` (and the
    DLLs it needs, beside it) is wanted. Keep this function the only lookup:
    available(), decode() and self_check all go through it.
    """
    if _platform(platform) != "win32":
        return None
    p = helper_install_path()
    return p if p.is_file() else None


def helper_args(src: Path | str, dst: Path | str) -> list[str]:
    """The helper's command line after the program itself (FFmpeg's syntax):
    the first sound track, decoded and resampled to 16 kHz mono 16-bit PCM
    WAV at ``dst``. Nothing here needs a GPL part of FFmpeg. The helper must
    exit 0 with the WAV written, or non-zero with its reason on stderr."""
    return ["-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(src),
            "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(RATE),
            "-c:a", "pcm_s16le", "-rf64", "auto", "-f", "wav", "-y", str(dst)]


def _helper_argv(helper: Path | str) -> list[str]:
    """How the helper program is started (tests run a Python stand-in)."""
    return [str(helper)]


def _path_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def available(platform: str | None = None) -> dict:
    """Can a file soundfile cannot read be imported here, before anyone picks one?"""
    plat = _platform(platform)
    if plat == "darwin":
        ready = os.path.isfile(AFCONVERT) and os.access(AFCONVERT, os.X_OK)
        return {"ready": ready, "needs_download": False, "how": "system" if ready else "none"}
    if plat == "win32":
        ready = helper_path(plat) is not None
        return {"ready": ready, "needs_download": not ready, "how": "download"}
    ready = _path_ffmpeg() is not None
    return {"ready": ready, "needs_download": False, "how": "system" if ready else "none"}


# ── decoding ────────────────────────────────────────────────────────────────

def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _timeout(path: Path) -> float:
    """Two minutes, plus two seconds a megabyte. afconvert read a 40-minute,
    35 MB m4a in about 5 s on an M-series Mac, so this is generous."""
    try:
        mb = path.stat().st_size / 1e6
    except OSError:
        mb = 0.0
    return 120.0 + 2.0 * mb


def _read_mono(wav: Path) -> np.ndarray:
    """The converted file as float32 mono, filled block by block so a long
    stereo file is never in memory twice."""
    import soundfile as sf
    with sf.SoundFile(str(wav)) as f:
        rate, frames = f.samplerate, f.frames
        out = np.empty(frames, dtype=np.float32)
        pos = 0
        for block in f.blocks(blocksize=RATE * 60, dtype="float32", always_2d=True):
            n = len(block)
            out[pos:pos + n] = block[:, 0] if block.shape[1] == 1 else block.mean(axis=1)
            pos += n
    out = out[:pos]
    if rate != RATE:                         # the converters are asked for RATE; just in case
        from .audioproc import resample
        out = resample(out, rate)
    return out


def _last_words(done) -> str:
    said = [line.strip() for line in ((done.stdout or "") + (done.stderr or "")).splitlines() if line.strip()]
    return said[-1][:300] if said else f"exit {done.returncode}"


def _all_words(done) -> str:
    """Everything the decoder said. FFmpeg 9 says why before its last line
    ("Stream map '' matches no streams." ... "Error opening output files:
    Invalid argument"), so the reason is looked for here; the last line is
    what the log keeps."""
    return "\n".join(x for x in (done.stdout, done.stderr) if x)


def _run(argv: list[str], path: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace",
                              timeout=_timeout(path), stdin=subprocess.DEVNULL,
                              creationflags=_no_window())
    except subprocess.TimeoutExpired:
        raise DecoderFailed(TOO_SLOW) from None
    except OSError as e:
        raise DecoderFailed(detail=f"{type(e).__name__}: {e}") from None


def decode(path, *, platform: str | None = None) -> tuple[np.ndarray, int]:
    """A file soundfile cannot read, as (16 kHz mono float32 samples, RATE)."""
    path = Path(path)
    plat = _platform(platform)
    if not path.is_file():
        raise NotAudio(detail="no such file")
    with tempfile.TemporaryDirectory(prefix="waffler-decode-") as tmp:
        out = Path(tmp) / "decoded.wav"
        if plat == "darwin":
            if not os.path.isfile(AFCONVERT):
                raise DecoderFailed(NO_DECODER, detail="no afconvert")
            # RF64 is a plain WAV until it passes 4 GB; LEI16@16000 has
            # afconvert resample; the channels are kept and averaged here.
            done = _run([AFCONVERT, "-f", "RF64", "-d", f"LEI16@{RATE}", str(path), str(out)], path)
        else:
            helper = helper_path(plat) if plat == "win32" else _path_ffmpeg()
            if helper is None:
                if plat == "win32":
                    raise NeedsDownload()
                raise DecoderFailed(NO_DECODER, detail="no ffmpeg on the PATH")
            done = _run(_helper_argv(helper) + helper_args(path, out), path)
        if done.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            raise _why(path, plat, _last_words(done), _all_words(done))
        try:
            return _read_mono(out), RATE
        except Exception as e:  # noqa: BLE001 — the converter wrote something unreadable
            raise DecoderFailed(detail=f"{type(e).__name__}: {e}") from None


# ── why a file could not be read ────────────────────────────────────────────

# Containers afconvert cannot open, by their first bytes and by extension.
_MAGIC_UNSUPPORTED = (
    (b"\x1a\x45\xdf\xa3", "webm/mkv"),
    (bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"), "wma/asf"),
    (b"FLV\x01", "flv"),
    (b".RMF", "realmedia"),
    (b"MAC ", "ape"),
    (b"wvpk", "wavpack"),
)
_EXT_UNSUPPORTED = {".webm", ".mkv", ".mka", ".wma", ".wmv", ".asf", ".avi", ".flv",
                    ".rm", ".ra", ".ape", ".wv"}
_PROTECTED_ENTRIES = {b"drms", b"drmi", b"drmp", b"enca", b"encv", b"encs", b"enct"}
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta"}
_TOP_LEVEL = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot", b"uuid", b"styp"}


def _head(path: Path, n: int = 16) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _unsupported_on_mac(path: Path) -> bool:
    head = _head(path)
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return True
    return path.suffix.lower() in _EXT_UNSUPPORTED or any(head.startswith(m) for m, _ in _MAGIC_UNSUPPORTED)


def _boxes(buf: bytes, start: int = 0, end: int | None = None):
    """(type, payload start, payload end) for each ISO-BMFF box in buf[start:end]."""
    end = len(buf) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", buf[pos:pos + 8])
        head = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            return
        yield kind, pos + head, pos + size
        pos += size


def mp4_tracks(path: Path, max_moov: int = 64 * 1024 * 1024) -> list[dict] | None:
    """The tracks of an mp4/m4a/mov file, as [{"handler": "soun"|"vide"|...,
    "entries": [sample entry types], "protected": bool}], or None when the
    file is not one (or its index cannot be found). Reads only the box
    headers and the moov box, never the media data."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            first = f.read(8)
            if len(first) < 8 or first[4:8] not in _TOP_LEVEL:
                return None
            pos, moov = 0, None
            while pos + 8 <= size:
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 8:
                    break
                box, kind = struct.unpack(">I4s", hdr[:8])
                head = 8
                if box == 1 and len(hdr) >= 16:
                    box, head = struct.unpack(">Q", hdr[8:16])[0], 16
                elif box == 0:
                    box = size - pos
                if box < head:
                    break
                if kind == b"moov":
                    if box > max_moov:
                        return None
                    f.seek(pos)
                    moov = f.read(box)
                    break
                pos += box
    except OSError:
        return None
    if moov is None:
        return None
    tracks = []
    for kind, a, b in _boxes(moov):
        if kind != b"moov":
            continue
        for tkind, ta, tb in _boxes(moov, a, b):
            if tkind == b"trak":
                tracks.append(_track(moov, ta, tb))
    return tracks


def _track(buf: bytes, a: int, b: int) -> dict:
    out = {"handler": "", "entries": [], "protected": False}

    def walk(start, end):
        for kind, pa, pb in _boxes(buf, start, end):
            # The media's own hdlr comes first; a QuickTime minf has a second
            # one, for the data reference ("alis"), which is not the kind.
            if kind == b"hdlr" and pb - pa >= 12 and not out["handler"]:
                out["handler"] = buf[pa + 8:pa + 12].decode("latin-1")
            elif kind == b"stsd" and pb - pa >= 8:
                for ekind, ea, eb in _boxes(buf, pa + 8, pb):
                    out["entries"].append(ekind.decode("latin-1"))
                    if ekind in _PROTECTED_ENTRIES or b"sinf" in buf[ea:eb]:
                        out["protected"] = True
            elif kind in _CONTAINERS:
                walk(pa, pb)
    walk(a, b)
    return out


def _why(path: Path, plat: str, said: str, heard: str = "") -> DecodeError:
    """The plain reason a decoder refused a file. `said` is its last line (for
    the log), `heard` everything it said (where the reason is looked for)."""
    if path.suffix.lower() == ".m4p":
        return Protected(detail=said)
    tracks = mp4_tracks(path)
    if tracks is not None:
        sound = [t for t in tracks if t["handler"] == "soun"]
        if any(t["protected"] for t in sound):
            return Protected(detail=said)
        if not sound and any(t["handler"] == "vide" for t in tracks):
            return NoSound(detail=said)
    if plat == "darwin" and _unsupported_on_mac(path):
        return Unsupported(detail=said)
    low = (heard or said).lower()
    if plat != "darwin" and ("matches no streams" in low or "does not contain any stream" in low):
        return NoSound(detail=said)
    if plat == "darwin" or "invalid data found" in low or "no such file" in low or "moov atom not found" in low:
        return NotAudio(detail=said)
    return DecoderFailed(detail=said)
