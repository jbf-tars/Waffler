#!/usr/bin/env python3
"""Write THIRD-PARTY-NOTICES.txt (repo root) from the build venv's own licence data.

    python scripts/third_party_notices.py            # (re)write the file
    python scripts/third_party_notices.py --check    # exit 1 if it is out of date
    python scripts/third_party_notices.py --stdout   # print, write nothing

Which Python packages: the requirement trees of requirements.txt (the Mac
build, as an Apple Silicon Mac evaluates its markers) and
requirements_windows.txt (the Windows build, as 64-bit Windows evaluates
them), plus llama-cpp-python, which the Mac release workflow installs on its
own. Extras are skipped. Build and test tools are left out (EXCLUDED), and so
is onnxruntime, which both spec files exclude. The list errs on the side of
too many: PyInstaller only bundles what is imported.

For a package installed in this venv, everything comes from its installed
metadata (importlib.metadata): version, License-Expression / License /
classifiers, home page, and the text of its licence files (LICENSE*,
LICENCE*, COPYING*, NOTICE*, and anything under a dist-info licenses/ folder).
A package that is not installed here (the Windows-only ones, on a Mac) must
have a hand-written entry in NOT_INSTALLED, which says so in the file; one
with neither is an error, so nothing is left out silently.

Bundled programs and libraries that are not Python packages (FFmpeg, NVIDIA's
libraries, the fonts, ...) are hand-written below (BINARIES, UI_ASSETS,
DOWNLOADS). Full GPL / LGPL texts come from licenses/ in the repository.

Offline and deterministic: no network, no dates, everything sorted. The output
depends on the versions installed, so --check is only meaningful in the venv
the file was generated in (or one with the same packages): regenerate it there
after changing requirements, and commit the result. Generate it on a Mac with
the Mac build's packages (plus llama-cpp-python) installed: NOT_INSTALLED
covers the Windows-only side, not the Mac-only one.
"""
from __future__ import annotations

import argparse
import difflib
import importlib.metadata as md
import re
import sys
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "THIRD-PARTY-NOTICES.txt"
LICENSES_DIR = ROOT / "licenses"

MAC, WIN = "macOS", "Windows"

# Marker environments for the two release builds. Python's own version is the
# venv's: a different Python can change a few markers (see the module doc).
_BASE = default_environment()
ENVS = {
    MAC: {**_BASE, "sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix",
          "platform_machine": "arm64", "extra": ""},
    WIN: {**_BASE, "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt",
          "platform_machine": "AMD64", "extra": ""},
}
REQUIREMENT_FILES = {MAC: ROOT / "requirements.txt", WIN: ROOT / "requirements_windows.txt"}
# Installed by the Mac release workflow outside requirements.txt.
EXTRA_ROOTS = {MAC: ["llama-cpp-python"], WIN: []}

# Not shipped: build / test tooling, and what the specs exclude.
EXCLUDED = {canonicalize_name(n) for n in (
    "pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph",
    "macholib", "pefile", "pywin32-ctypes", "pytest", "pluggy", "iniconfig", "pyflakes",
    "onnxruntime",          # excluded in Waffler_mac.spec and Waffler_windows.spec
)}

# ── hand-written: Python packages not installed on this machine ─────────────
# Windows-only (or CI-only) packages. Licence and source as their projects
# publish them; `requires` continues the walk (runtime requirements only).
NOT_INSTALLED = {
    "av": dict(
        name="PyAV (av)", license="BSD-3-Clause",
        url="https://github.com/PyAV-Org/PyAV",
        requires=[],
        note="Pulled in by faster-whisper (Windows). The wheel carries FFmpeg's shared "
             "libraries, linked to libx264 and libx265: see \"FFmpeg libraries inside PyAV\" "
             "in Part 1 (to be left out of the Windows build)."),
    "clr-loader": dict(
        name="clr_loader", license="MIT", url="https://github.com/pythonnet/clr-loader",
        requires=["cffi"], note="Pulled in by pythonnet (Windows)."),
    "colorama": dict(
        name="colorama", license="BSD-3-Clause", url="https://github.com/tartley/colorama",
        requires=[], note="Pulled in on Windows by tqdm and click."),
    "ctranslate2": dict(
        name="CTranslate2", license="MIT", url="https://github.com/OpenNMT/CTranslate2",
        requires=["numpy", "pyyaml"],
        note="Pulled in by faster-whisper (Windows). Its Windows wheel carries "
             "ctranslate2.dll and Intel's OpenMP runtime libiomp5md.dll: see Part 1."),
    "faster-whisper": dict(
        name="faster-whisper", license="MIT", url="https://github.com/SYSTRAN/faster-whisper",
        requires=["ctranslate2", "huggingface-hub", "tokenizers", "onnxruntime", "av", "tqdm"],
        note="Windows build (and Intel Macs, which are not a release target)."),
    "pillow": dict(
        name="Pillow", license="MIT-CMU (HPND)", url="https://github.com/python-pillow/Pillow",
        requires=[], note="Windows build (system tray)."),
    "pyaudiowpatch": dict(
        name="PyAudioWPatch", license="MIT", url="https://github.com/s0d3s/PyAudioWPatch",
        requires=[],
        note="Windows build (meeting audio through WASAPI loopback). A fork of PyAudio "
             "(MIT); its wheel carries PortAudio (MIT, https://www.portaudio.com/)."),
    "pystray": dict(
        name="pystray", license="LGPL-3.0 (GNU Lesser General Public License v3)",
        url="https://github.com/moses-palmer/pystray",
        requires=["pillow", "six"],
        note="Windows build (system tray). Bundled as Python modules inside the app's "
             "PyInstaller archive. Full LGPL-3.0 and GPL-3.0 texts: Part 6."),
    "pythonnet": dict(
        name="pythonnet", license="MIT", url="https://github.com/pythonnet/pythonnet",
        requires=["clr-loader"], note="Windows build (pywebview's Edge WebView2 back end)."),
    "six": dict(
        name="six", license="MIT", url="https://github.com/benjaminp/six",
        requires=[], note="Pulled in by pystray (Windows)."),
    "tokenizers": dict(
        name="tokenizers", license="Apache-2.0", url="https://github.com/huggingface/tokenizers",
        requires=["huggingface-hub"], note="Pulled in by faster-whisper (Windows)."),
    "imageio-ffmpeg": dict(
        name="imageio-ffmpeg", license="BSD-2-Clause (the package); its ffmpeg program is GPL-3.0",
        url="https://github.com/imageio/imageio-ffmpeg", requires=[],
        note="Windows build of 4.0.0 only; no longer in the Mac build. Carries a GPL ffmpeg "
             "program: see Part 1. TO DO, WINDOWS SESSION: remove it from "
             "requirements_windows.txt and Waffler_windows.spec (no GPL program in either "
             "installer, decided 26 Nov 2026)."),
    "llama-cpp-python": dict(
        name="llama-cpp-python", license="MIT", url="https://github.com/abetlen/llama-cpp-python",
        requires=["typing-extensions", "numpy", "diskcache", "jinja2"],
        note="Mac build, compiled in CI. Bundles llama.cpp and ggml (MIT, "
             "https://github.com/ggml-org/llama.cpp)."),
}

# ── hand-written: bundled programs and libraries ────────────────────────────
# (title, builds, body). Bodies are facts as found; how they were found is in
# docs/THIRD-PARTY-LICENSING.md.
BINARIES = [
    ("CPython (the Python interpreter and standard library)", "macOS, Windows", """\
Licence: Python Software Foundation License Version 2 (PSF-2.0); the
interpreter's own LICENSE file also lists the licences of code it includes
(and, in the Windows build, the OpenSSL, libffi, SQLite, Tcl/Tk, XZ, zlib and
bzip2 libraries it ships with). PyInstaller copies the interpreter and the
standard library modules the app uses into the bundle.
Source: https://www.python.org/downloads/source/ (the release builds use
Python 3.11; see .github/workflows/*-release.yml)."""),
    ("PyInstaller bootloader", "macOS, Windows", """\
The small launcher at the start of the Waffler executable comes from
PyInstaller. Licence: GPL-2.0-or-later with PyInstaller's bootloader
exception, which allows the bootloader to be distributed in programs built
with it under any licence (https://pyinstaller.org/en/stable/license.html).
Source: https://github.com/pyinstaller/pyinstaller"""),
    ("FFmpeg program from imageio-ffmpeg (Windows x64)", "Windows", """\
File: imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v9.0.2.exe, a separate
executable that the Windows build of 4.0.0 starts as a subprocess for "Import
audio" of formats soundfile cannot read. imageio-ffmpeg takes it from
gyan.dev's "essentials" build, FFmpeg 9.0.2. Checked on a Windows build:
configured with --enable-gpl --enable-version3, with libx264 and libx265, so
the binary is GPL-3.0 (or later). Full GPL-3.0 text: Part 6.
TO DO, WINDOWS SESSION: no GPL program goes in either installer (decided
26 Nov 2026). This program is to leave the Windows build (imageio-ffmpeg out
of requirements_windows.txt and Waffler_windows.spec); an LGPL FFmpeg build is
fetched instead the first time someone uses Import audio
(src/meetings/audio_decode.py), and this entry moves to Part 5. The Mac build
no longer has FFmpeg at all: it uses macOS's own afconvert.
Source: FFmpeg 9.0.2 source, https://ffmpeg.org/releases/ (ffmpeg-9.0.2.tar.xz);
the build and the configuration of each library it includes,
https://www.gyan.dev/ffmpeg/builds/; the packaging,
https://github.com/imageio/imageio-ffmpeg."""),
    ("FFmpeg libraries inside PyAV (av)", "Windows", """\
PyAV's wheels carry FFmpeg's shared libraries (avcodec, avformat, avutil,
swresample, swscale, ... in the wheel's av.libs folder on Windows), which the
av extension modules link to. faster-whisper imports PyAV; the Windows build
bundles it. PyAV itself is BSD-3-Clause. Checked on a Windows build (PyAV
19.0.2): the libraries report "LGPL version 3 or later" but are configured
WITH --enable-libx264 --enable-libx265, and avcodec-63.dll imports
libx264-165.dll and libx265.dll, so those GPL libraries load inside Waffler's
own process whenever av is imported. (An earlier version of this file said
"no x264/x265": that was wrong.) Full LGPL-3.0 and GPL texts: Part 6.
TO DO, WINDOWS SESSION: PyAV's libraries are to be left out of the Windows
build (decided 26 Nov 2026: no GPL program or library in either installer).
Source: https://github.com/PyAV-Org/PyAV and https://github.com/PyAV-Org/pyav-ffmpeg
(build scripts), FFmpeg source at https://ffmpeg.org/releases/."""),
    ("libsndfile (inside soundfile)", "macOS, Windows", """\
soundfile's wheel carries libsndfile (_soundfile_data/libsndfile_*.dylib on
the Mac, a DLL on Windows), loaded through cffi. Licence: GNU LGPL 2.1 or
later; its COPYING is printed with the soundfile entry in Part 2, and the full
LGPL-2.1 text is in Part 6. It is a separate shared library file in the bundle
and can be replaced.
Source: https://github.com/libsndfile/libsndfile and the build scripts in
https://github.com/bastibe/libsndfile-binaries."""),
    ("PortAudio (inside sounddevice)", "macOS, Windows", """\
sounddevice's wheel carries PortAudio (_sounddevice_data/portaudio-binaries).
Licence: MIT-style PortAudio licence. On Windows the wheel also carries
PortAudio builds with ASIO support, which Waffler never loads (checked on a
Windows build); TO DO, WINDOWS SESSION: leave them out of the build.
Source: https://github.com/PortAudio/portaudio and
https://github.com/spatialaudio/portaudio-binaries."""),
    ("whisper.cpp and ggml (inside pywhispercpp)", "macOS", """\
pywhispercpp's wheel carries libwhisper and libggml-* (Metal among them) in
pywhispercpp/.dylibs. Licence: MIT.
Source: https://github.com/ggml-org/whisper.cpp and
https://github.com/absadiki/pywhispercpp."""),
    ("llama.cpp and ggml (inside llama-cpp-python)", "macOS", """\
llama-cpp-python, compiled in the Mac release workflow, carries libllama,
libggml-* and libmtmd in llama_cpp/lib. Licence: MIT.
Source: https://github.com/ggml-org/llama.cpp and
https://github.com/abetlen/llama-cpp-python."""),
    ("CTranslate2 and Intel OpenMP runtime (libiomp5md.dll)", "Windows", """\
The Windows build collects ctranslate2.dll and libiomp5md.dll from the
ctranslate2 package folder (Waffler_windows.spec, collect_dynamic_libs).
ctranslate2.dll is CTranslate2's own library (MIT). libiomp5md.dll is Intel's
OpenMP runtime, which CTranslate2's Windows wheels ship because the library is
built with Intel oneMKL; Intel distributes this runtime under the Intel
Simplified Software License (redistributable), and oneMKL code compiled into
ctranslate2.dll is under the same licence. NOT VERIFIED ON THE GENERATING
MACHINE: ctranslate2 is not installed here; the licence files in the
ctranslate2 Windows wheel are authoritative.
Source: https://github.com/OpenNMT/CTranslate2; Intel's licence,
https://www.intel.com/content/www/us/en/developer/articles/license/onemkl-license-faq.html"""),
    ("natter-sysaudio (mac/sysaudio)", "macOS", """\
The helper that records what the Mac is playing during a meeting is
Waffler's own code (Swift, ScreenCaptureKit), under Waffler's licence."""),
]

UI_ASSETS = [
    ("Inter (ui/fonts)", "SIL Open Font License 1.1", "ui/fonts/OFL-Inter.txt",
     "https://github.com/rsms/inter"),
    ("Source Serif 4 (ui/fonts, ui/meetings-app/fonts)", "SIL Open Font License 1.1",
     "ui/fonts/OFL-SourceSerif4.txt", "https://github.com/adobe-fonts/source-serif"),
    ("Geist (ui/fonts, ui/meetings-app/fonts)", "SIL Open Font License 1.1",
     "ui/fonts/OFL-Geist.txt", "https://github.com/vercel/geist-font"),
    ("Geist Mono (ui/fonts, ui/meetings-app/fonts)", "SIL Open Font License 1.1",
     "ui/fonts/OFL-GeistMono.txt", "https://github.com/vercel/geist-font"),
    ("Lucide icons (ui/icons.js)", "ISC (portions MIT, from Feather)", "ui/LICENSE-Lucide.txt",
     "https://github.com/lucide-icons/lucide"),
]

REACT_NOTE = """\
ui/meetings-app/assets/*.js is a production build that includes React,
react-dom, react/jsx-runtime and scheduler. Their banners read: "Copyright (c)
Facebook, Inc. and its affiliates. This source code is licensed under the MIT
license". Source: https://github.com/facebook/react. MIT licence text:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

# ── hand-written: downloaded on request, not bundled ────────────────────────
DOWNLOADS = [
    ("Whisper speech models, whisper.cpp format (src/local_stt.py)", "macOS", """\
ggml-large-v3-turbo-q5_0.bin and ggml-small-q5_1.bin from
https://huggingface.co/ggerganov/whisper.cpp (pinned commit). Converted from
OpenAI's Whisper weights, which OpenAI released under the MIT licence
(https://github.com/openai/whisper)."""),
    ("Whisper speech models, CTranslate2 format (src/local_stt.py)", "Windows", """\
Systran/faster-whisper-small, Systran/faster-whisper-base and
dropbox-dash/faster-whisper-large-v3-turbo from https://huggingface.co (pinned
commits). Conversions of OpenAI's Whisper weights (MIT); the repositories
state the MIT licence."""),
    ("Gemma 4 E4B writing model (src/local_llm.py)", "macOS, Windows", """\
gemma-4-E4B_q4_0-it.gguf from https://huggingface.co/google/gemma-4-E4B-it-qat-q4_0-gguf
(pinned commit), Google's 4-bit build. Licence: Apache-2.0, as Google states on
the model card."""),
    ("llama.cpp server for Windows (src/llama_pack.py)", "Windows", """\
llama-b11168-bin-win-cpu-x64.zip or llama-b11168-bin-win-cuda-12.4-x64.zip,
llama.cpp's official release b11168 (https://github.com/ggml-org/llama.cpp/releases),
fetched with the writing model. Licence: MIT. The CUDA build includes
NVIDIA CUDA runtime components under NVIDIA's CUDA EULA (below)."""),
    ("NVIDIA cuBLAS, cuDNN and CUDA runtime (src/gpu_pack.py, src/llama_pack.py)", "Windows", """\
On one click in Settings, and only on a PC with a suitable NVIDIA card, Waffler
fetches NVIDIA's own Windows wheels from PyPI (pinned by version, size and
SHA-256), takes the DLLs out into the data folder and deletes the wheels:
nvidia_cublas_cu12 12.9.2.10 and nvidia_cudnn_cu12 9.28.1.4 (src/gpu_pack.py),
and nvidia_cuda_runtime_cu12 12.9.79 with the CUDA llama.cpp server
(src/llama_pack.py). They are never in the installer. Licence: NVIDIA's
proprietary terms (the NVIDIA CUDA Toolkit End User License Agreement for
cuBLAS and the CUDA runtime, https://docs.nvidia.com/cuda/eula/; the NVIDIA
cuDNN Software License Agreement for cuDNN,
https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html), which
allow redistribution of these runtime libraries with an application.
Source: not open source; the wheels are at https://pypi.org/project/nvidia-cublas-cu12/,
https://pypi.org/project/nvidia-cudnn-cu12/ and
https://pypi.org/project/nvidia-cuda-runtime-cu12/."""),
]

FULL_TEXTS = [("GNU General Public License, version 2", "GPL-2.0.txt"),
              ("GNU General Public License, version 3", "GPL-3.0.txt"),
              ("GNU Lesser General Public License, version 2.1", "LGPL-2.1.txt"),
              ("GNU Lesser General Public License, version 3", "LGPL-3.0.txt")]

_LICENCE_FILE = re.compile(r"^(licen[cs]e|copying|notice)", re.I)
_SKIP_SUFFIX = (".py", ".pyc", ".pyi", ".so", ".dylib", ".dll", ".pyd", ".html", ".js")


# ── the walk ────────────────────────────────────────────────────────────────

def _dist(name: str):
    try:
        return md.distribution(name)
    except md.PackageNotFoundError:
        return None


def _requirements_file(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            yield Requirement(line)


def _applies(req: Requirement, env: dict) -> bool:
    return req.marker is None or req.marker.evaluate(env)


def collect() -> dict:
    """canonical name -> {"platforms": set, "dist": Distribution | None, "requested_by": set}."""
    found: dict = {}
    for platform, env in ENVS.items():
        queue = [(canonicalize_name(r.name), "requirements file")
                 for r in _requirements_file(REQUIREMENT_FILES[platform]) if _applies(r, env)]
        queue += [(canonicalize_name(n), "release workflow") for n in EXTRA_ROOTS[platform]]
        seen = set()
        while queue:
            key, parent = queue.pop()
            if key in EXCLUDED or key in seen:
                continue
            seen.add(key)
            dist = _dist(key)
            entry = found.setdefault(key, {"platforms": set(), "dist": dist, "requested_by": set()})
            entry["platforms"].add(platform)
            entry["requested_by"].add(parent)
            if dist is not None:
                for raw in dist.requires or []:
                    req = Requirement(raw)
                    if _applies(req, env):
                        queue.append((canonicalize_name(req.name), dist.metadata["Name"]))
            elif key in NOT_INSTALLED:
                queue += [(canonicalize_name(n), NOT_INSTALLED[key]["name"])
                          for n in NOT_INSTALLED[key]["requires"]]
            else:
                raise SystemExit(f"{key} ({platform}) is not installed here and has no "
                                 f"hand-written entry in NOT_INSTALLED")
    return found


# ── one package ─────────────────────────────────────────────────────────────

def _licence_summary(meta) -> str:
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr
    lic = (meta.get("License") or "").strip()
    classifiers = sorted(c.split("::")[-1].strip() for c in meta.get_all("Classifier") or []
                         if c.startswith("License ::"))
    if lic and "\n" not in lic and len(lic) <= 200:
        return lic + (f" ({'; '.join(classifiers)})" if classifiers else "")
    if classifiers:
        return "; ".join(classifiers)
    return "see the licence text below" if lic else "not stated in the package metadata"


def _url(meta) -> str:
    urls = {}
    for item in meta.get_all("Project-URL") or []:
        label, _, url = item.partition(",")
        urls[label.strip().lower()] = url.strip()
    for label in ("source", "source code", "repository", "github", "code", "homepage", "home"):
        if label in urls:
            return urls[label]
    return (meta.get("Home-page") or "").strip() or (sorted(urls.values())[0] if urls else "")


def _licence_files(dist) -> list:
    """[(relative path, text)] of the dist's licence files, sorted, deduplicated."""
    out, seen = [], set()
    for f in dist.files or []:
        parts = f.parts
        name = parts[-1]
        in_licenses_dir = any(p.endswith(".dist-info") for p in parts) and "licenses" in parts
        if not (in_licenses_dir or _LICENCE_FILE.match(name)) or name.lower().endswith(_SKIP_SUFFIX):
            continue
        if any(p == "__pycache__" for p in parts):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            try:
                text = Path(f.locate()).read_bytes().decode("utf-8", "replace")
            except OSError:
                continue
        if text is None:
            continue
        text = text.replace("\r\n", "\n").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rel = "/".join(parts[1:]) if parts[0].endswith(".dist-info") else "/".join(parts)
        out.append((rel, text))
    return sorted(out)


def _platforms(entry) -> str:
    return ", ".join(p for p in (MAC, WIN) if p in entry["platforms"])


def _rule(char="-") -> str:
    return char * 78


# ── the file ────────────────────────────────────────────────────────────────

def render() -> str:
    found = collect()
    lines = []
    w = lines.append

    w("THIRD-PARTY NOTICES FOR WAFFLER")
    w(_rule("="))
    w("""\
Waffler itself is under the licence in its LICENSE file (MIT). The Mac and
Windows builds include, or fetch when asked, the third-party software below,
each under its own licence. This file is generated by
scripts/third_party_notices.py from the build environment's installed package
metadata plus hand-written entries; where a fact could not be checked on the
machine that generated it, the entry says so.

  Part 1. Programs and libraries bundled as binaries
  Part 2. Python packages, from their installed metadata
  Part 3. Python packages not installed where this file was generated
  Part 4. Fonts, icons and web code in the user interface
  Part 5. Downloaded on request, never bundled
  Part 6. Full texts of the GNU licences referred to above""")
    w("")

    w(_rule("="))
    w("PART 1. PROGRAMS AND LIBRARIES BUNDLED AS BINARIES")
    w(_rule("="))
    for title, builds, body in BINARIES:
        w("")
        w(f"{title}  [{builds}]")
        w(_rule())
        w(body)
        if title.startswith("CPython"):
            psf = Path(argparse.__file__).resolve().parent / "LICENSE.txt"
            if psf.is_file():
                w(f"Licence text (Python {sys.version_info.major}.{sys.version_info.minor} "
                  f"LICENSE.txt, from the generating interpreter):")
                w("")
                w(psf.read_text(encoding="utf-8").replace("\r\n", "\n").strip())
                w("")
        if title == "PyInstaller bootloader":
            dist = _dist("pyinstaller")
            for rel, text in (_licence_files(dist) if dist else []):
                w(f"Licence text (pyinstaller {dist.version}, {rel}):")
                w("")
                w(text)
                w("")
    w("")

    installed = {k: v for k, v in found.items() if v["dist"] is not None}
    missing = {k: v for k, v in found.items() if v["dist"] is None}

    w(_rule("="))
    w("PART 2. PYTHON PACKAGES, FROM THEIR INSTALLED METADATA")
    w(_rule("="))
    w("Builds: which release build's requirement tree includes the package.")
    texts_seen: dict = {}
    for key in sorted(installed):
        entry = installed[key]
        dist = entry["dist"]
        meta = dist.metadata
        name = meta["Name"]
        w("")
        w(f"{name} {dist.version}  [{_platforms(entry)}]")
        w(_rule())
        w(f"Licence: {_licence_summary(meta)}")
        url = _url(meta)
        if url:
            w(f"Source: {url}")
        files = _licence_files(dist)
        if not files:
            w("Licence text: none included in the installed package.")
        for rel, text in files:
            if text in texts_seen:
                w(f"Licence text ({rel}): the same as {texts_seen[text]} above.")
                continue
            texts_seen[text] = f"{name} ({rel})"
            w(f"Licence text ({rel}):")
            w("")
            w(text)
            w("")
    w("")

    w(_rule("="))
    w("PART 3. PYTHON PACKAGES NOT INSTALLED WHERE THIS FILE WAS GENERATED")
    w(_rule("="))
    w("""\
Hand-written entries: these packages are in a release build's requirement tree
but were not installed in the environment that generated this file (typically
Windows-only packages, generated on a Mac). Their licence and source are as
their projects publish them; the licence text is in the package's wheel.""")
    for key in sorted(missing):
        info = NOT_INSTALLED[key]
        w("")
        w(f"{info['name']}  [{_platforms(missing[key])}]  (hand-written: metadata not available locally)")
        w(_rule())
        w(f"Licence: {info['license']}")
        w(f"Source: {info['url']}")
        if info.get("note"):
            w(info["note"])
    w("")

    w(_rule("="))
    w("PART 4. FONTS, ICONS AND WEB CODE IN THE USER INTERFACE")
    w(_rule("="))
    for title, licence, path, url in UI_ASSETS:
        w("")
        w(f"{title}  [macOS, Windows]")
        w(_rule())
        w(f"Licence: {licence}")
        w(f"Source: {url}")
        w(f"Licence text ({path}):")
        w("")
        w((ROOT / path).read_text(encoding="utf-8").replace("\r\n", "\n").strip())
        w("")
    w("")
    w("React (ui/meetings-app)  [macOS, Windows]")
    w(_rule())
    w(REACT_NOTE)
    w("")

    w(_rule("="))
    w("PART 5. DOWNLOADED ON REQUEST, NEVER BUNDLED")
    w(_rule("="))
    w("""\
Waffler fetches these only when the person asks for them in Settings, into its
data folder. They are not part of the installer or the app bundle.""")
    for title, builds, body in DOWNLOADS:
        w("")
        w(f"{title}  [{builds}]")
        w(_rule())
        w(body)
    w("")

    w(_rule("="))
    w("PART 6. FULL TEXTS OF THE GNU LICENCES REFERRED TO ABOVE")
    w(_rule("="))
    for title, fname in FULL_TEXTS:
        w("")
        w(title)
        w(_rule())
        w((LICENSES_DIR / fname).read_text(encoding="utf-8").replace("\r\n", "\n").strip())
        w("")

    text = "\n".join(lines)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.rstrip("\n") + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if THIRD-PARTY-NOTICES.txt differs from what would be written")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = ap.parse_args(argv)
    text = render()
    if args.stdout:
        sys.stdout.write(text)
        return 0
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current == text:
            print(f"{OUT.name} is up to date")
            return 0
        diff = difflib.unified_diff(current.splitlines(), text.splitlines(),
                                    "committed", "generated", lineterm="", n=1)
        print(f"{OUT.name} is out of date: run scripts/third_party_notices.py "
              f"(in the build venv) and commit the result", file=sys.stderr)
        for i, line in enumerate(diff):
            if i >= 40:
                print("...", file=sys.stderr)
                break
            print(line, file=sys.stderr)
        return 1
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
