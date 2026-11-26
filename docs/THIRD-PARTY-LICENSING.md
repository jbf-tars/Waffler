# Third-party licensing

Waffler's own code is under the MIT licence (`LICENSE`). Every third-party
component in the builds is listed, with its licence, in
`THIRD-PARTY-NOTICES.txt`, which `scripts/third_party_notices.py` generates and
both builds ship at their resource root.

## The decision (26 Nov 2026)

**No GPL program or library goes in either installer.** Concretely:

- **Mac: done.** The Mac build no longer ships FFmpeg. "Import audio" reads
  what soundfile cannot (m4a/AAC, Apple Lossless, mp4/mov sound tracks, caf,
  3gp, .aac) with macOS's own `/usr/bin/afconvert`, which is part of every
  macOS, so there is no licence question (`src/meetings/audio_decode.py`).
  `imageio-ffmpeg` is out of `requirements.txt`, and `Waffler_mac.spec` lists
  `imageio_ffmpeg` under `excludes`, so even a build venv that still has the
  package cannot put its ffmpeg into the app.
- **Windows: to do, Windows session.** An LGPL build of FFmpeg is fetched
  into the data folder the first time someone uses Import audio on a file
  soundfile cannot read, in the same way as the writing engine's server and
  NVIDIA's libraries (pinned URL, size and SHA-256, resumable, progress to
  the page). The installer carries none. The seam is in place
  (`audio_decode.helper_path`, `helper_install_path`, `helper_args`, and the
  `needs_download` answer from the bridge); the download itself is not.
- **Windows: to do, Windows session.** PyAV's FFmpeg libraries are left out
  of the Windows build (below).

## What was found on Windows (verified by the Windows session)

| Component | Facts | Status |
|---|---|---|
| `imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v9.0.2.exe` | gyan.dev FFmpeg 9.0.2 "essentials", configured with `--enable-gpl --enable-version3`, with libx264 and libx265: GPLv3. Run as a separate program for Import audio. | To do, Windows session: drop `imageio-ffmpeg` from `requirements_windows.txt` and `Waffler_windows.spec`; fetch an LGPL decoder on first use instead. |
| PyAV 19.0.2 `av.libs` (FFmpeg's shared libraries) | The libraries report "LGPL version 3 or later", but they are configured **with** `--enable-libx264 --enable-libx265`, and `avcodec-63.dll` imports `libx264-165.dll` and `libx265.dll`. So those GPL libraries load inside Waffler's own process whenever `av` is imported, and faster-whisper imports it. The earlier text here, "no x264/x265", was wrong. | To do, Windows session: leave PyAV's libraries out of the Windows build. |
| sounddevice's PortAudio | The wheel also ships PortAudio builds with ASIO support. Waffler never loads them. | To do, Windows session: leave them out of the build. |

## How Import audio decodes now

`src/meetings/service.py` `load_any_audio` tries soundfile first (wav, flac,
aiff, ogg, mp3). Anything else goes to `src/meetings/audio_decode.py`
`decode()`:

- **Mac:** `afconvert -f RF64 -d LEI16@16000 <file> <temp>.wav` (no shell, a
  timeout of two minutes plus two seconds a megabyte), read back with
  soundfile block by block, channels averaged, the temporary folder removed.
  Formats afconvert cannot open (webm/mkv, wma/asf, avi, flv, RealMedia,
  APE, WavPack) get one sentence naming what works. DRM-protected files
  (FairPlay `.m4p`, encrypted mp4) and video with no sound track are named as
  such.
- **Windows:** the fetched program, run with `helper_args()`: the first sound
  track decoded and resampled to 16 kHz mono 16-bit PCM WAV, using only
  FFmpeg's native demuxers and decoders, libswresample and the native
  pcm_s16le writer. No filters and no external encoders, so an LGPL build
  (no `--enable-gpl`, no `--enable-nonfree`) is enough. Started with
  `CREATE_NO_WINDOW`.

## What shipping an LGPL FFmpeg involves (for the Windows download)

The LGPL asks for the licence text and the source of the exact build
(FFmpeg's source and its configuration), and that the library or program can
be replaced. A program fetched on request into the data folder, not in the
installer, belongs in Part 5 of `THIRD-PARTY-NOTICES.txt` ("Downloaded on
request") with its version, build, licence and source location.

## Other copyleft components, for completeness

- libsndfile (in soundfile's wheel, both builds): LGPL-2.1-or-later, a
  separate shared library.
- pystray (Windows tray): LGPL-3.0, bundled as Python modules inside the
  PyInstaller archive.
- PyInstaller's bootloader: GPL-2.0-or-later with an exception that allows
  distributing it in programs of any licence.
