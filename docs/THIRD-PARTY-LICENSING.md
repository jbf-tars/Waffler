# Third-party licensing: open questions

Waffler's own code is under the MIT licence (`LICENSE`). Every third-party
component in the builds is listed, with its licence, in
`THIRD-PARTY-NOTICES.txt`, which `scripts/third_party_notices.py` generates and
both builds ship at their resource root. This page records one question that
is open. It states facts only and does not decide anything.

## FFmpeg under the GPL, bundled with an MIT app

### What is bundled, and how

Both builds include imageio-ffmpeg (requirements.txt and
requirements_windows.txt), whose wheel carries a ready-built `ffmpeg`
program. PyInstaller's imageio_ffmpeg hook copies it into the bundle.

| Build | File | Licence the binary declares |
|---|---|---|
| Mac (Apple Silicon) | `imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v9.0` (osxexperts.net build, FFmpeg 9.0) | `ffmpeg -version` shows `--enable-gpl` without `--enable-version3`, with libx264, libx265, libvvenc, libvpx, libaom, libsvtav1 and others statically linked. `ffmpeg -L`: GPL version 2 or later. Checked on this Mac. |
| Windows x64 | `imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v9.0.2.exe` (gyan.dev "essentials" build, FFmpeg 9.0.2) | gyan.dev's essentials builds use `--enable-gpl --enable-version3` and include libx264 and libx265: GPL version 3. Not checked here: confirm with `ffmpeg -version` and `ffmpeg -L` on a Windows build. |

How Waffler uses it: as a **separate program run as a subprocess**, not as a
linked library. `src/meetings/service.py` (`load_any_audio`, used by "Import
audio" for formats soundfile cannot read, such as m4a) first tries macOS's
`afconvert`, then an `ffmpeg` on the PATH, and only then
`imageio_ffmpeg.get_ffmpeg_exe()`, and runs it with `subprocess.run` to write
a temporary FLAC file. `src/self_check.py` only checks that the program is
there. Nothing in Waffler links against FFmpeg's libraries through this path.

### What shipping a GPL binary generally involves

For the binary itself (not for the program that starts it), distributing a
GPL executable generally means:

- giving the GPL licence text with it (`THIRD-PARTY-NOTICES.txt` now includes
  the full GPL-2.0 and GPL-3.0 texts and names each build);
- giving the complete corresponding source code of that exact build (FFmpeg's
  source plus the configuration and the source of every library compiled
  into it, such as x264 and x265), either alongside the binary or through a
  written offer valid for at least three years (GPLv2 section 3; GPLv3
  section 6 also allows a network location for as long as the binary is
  offered). Pointing to the upstream builder's page (osxexperts.net,
  gyan.dev) is not, by itself, the same as Waffler providing the source;
- not adding restrictions on the recipients' rights to that binary.

### The open question

Whether putting a GPL program into the same installer and app bundle as the
MIT-licensed Waffler is "mere aggregation" (two separate programs distributed
together, which the GPL permits without affecting the other program's
licence) or creates obligations for Waffler itself. The facts that bear on it:
FFmpeg is a separate executable, started with command-line arguments, with
data exchanged through files; it is inside the same app bundle (and code
signature, when the build is signed) and installed by the same installer. Separately from
that question, the source-availability obligation above applies to the binary
as long as it is distributed.

### Two alternatives that avoid a GPL binary

(a) **Ship an LGPL-only FFmpeg build**: built without `--enable-gpl` and
`--enable-version3`, so without libx264, libx265 and other GPL parts (an
audio-only build for decoding is enough for Import audio). LGPL still asks for
the licence text and the source of the build, but it is the licence most
desktop apps that bundle FFmpeg use. It means producing (or finding) and
pinning such builds for Mac and Windows instead of using imageio-ffmpeg's
bundled binaries.

(b) **Download FFmpeg on first use of Import audio** instead of bundling it,
in the same way Waffler already fetches models and NVIDIA's libraries on
request (pinned URL, size and SHA-256, into the data folder). The installer
then contains no FFmpeg; the person fetches it from its distributor when they
need it.

## PyAV's FFmpeg libraries (Windows)

The Windows build bundles PyAV (`av`), which faster-whisper uses to decode
audio. PyAV itself is BSD-3-Clause; its wheels carry FFmpeg's shared
libraries (in `av.libs`), which PyAV's extension modules link to dynamically.
The PyAV project builds them itself (pyav-ffmpeg) as LGPL builds of FFmpeg,
without the GPL components, which makes them LGPL-2.1-or-later. This was not
checked on a Windows machine: the DLL names in `_internal/av.libs` (no
libx264 or libx265) and the licence files in the av wheel would confirm it.
As separate DLLs they can be replaced, which is what the LGPL asks of
dynamically linked libraries; the licence text and source location are in
`THIRD-PARTY-NOTICES.txt`.

## Other copyleft components, for completeness

- libsndfile (in soundfile's wheel, both builds): LGPL-2.1-or-later, a
  separate shared library.
- pystray (Windows tray): LGPL-3.0, bundled as Python modules inside the
  PyInstaller archive.
- PyInstaller's bootloader: GPL-2.0-or-later with an exception that allows
  distributing it in programs of any licence.
