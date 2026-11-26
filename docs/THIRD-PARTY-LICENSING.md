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
- **Windows: done.** An LGPL build of FFmpeg is fetched into the data
  folder the first time someone imports a file soundfile cannot read and says
  yes to "Import audio needs a one-time download, 77 MB." The import waits
  for it and then carries on with the same file (`app.py`
  `_import_with_decoder`, `ui/decoder.js`, `src/audio_decoder_pack.py`). It
  is pinned like the writing engine's server (URL, size, SHA-256), resumed
  when cut short, and checked before anything is unpacked from it; only
  `ffmpeg.exe`, the seven FFmpeg libraries it loads and `LICENSE.txt` are
  kept (155 MB on disk). `imageio-ffmpeg` is out of `requirements_windows.txt`
  and `Waffler_windows.spec` excludes it, as the Mac spec does.
- **Windows: done.** PyAV is excluded from the Windows build; a runtime hook
  (`hooks/rthook_no_pyav.py`) meets the `import av` faster-whisper makes as it
  loads. Waffler passes faster-whisper arrays, and faster-whisper decodes only
  what is not an array, so nothing ever asks it for more
  (`tests/test_no_pyav_windows.py` imports faster-whisper against the
  stand-in and transcribes an array). sounddevice's unused PortAudio builds
  are left out too.
- **The Windows installer** went from 107,102,685 bytes (4.0.0 at 823f876)
  to 87,491,305 without PyAV and the extra PortAudio builds, and to
  59,496,599 without imageio-ffmpeg as well (measured on the PC).

## What was found on Windows (verified by the Windows session)

| Component | Facts | Status |
|---|---|---|
| `imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v9.0.2.exe` | gyan.dev FFmpeg 9.0.2 "essentials", configured with `--enable-gpl --enable-version3`, with libx264 and libx265: GPLv3. Run as a separate program for Import audio. | Done: not in the Windows build; the LGPL converter below comes on first use instead. |
| PyAV 19.0.2 `av.libs` (FFmpeg's shared libraries) | The libraries report "LGPL version 3 or later", but they are configured **with** `--enable-libx264 --enable-libx265`, and `avcodec-63.dll` imports `libx264-165.dll` and `libx265.dll`. So those GPL libraries load inside Waffler's own process whenever `av` is imported, and faster-whisper imports it. The earlier text here, "no x264/x265", was wrong. | Done: excluded from the Windows build, with a stand-in for faster-whisper's import (`hooks/rthook_no_pyav.py`). |
| sounddevice's PortAudio | The wheel also ships PortAudio builds with ASIO support (Steinberg's ASIO SDK, whose licence the wheel does not state), and 32-bit, arm64 and macOS builds. 64-bit Windows loads `libportaudio64bit.dll` only. | Done: the build keeps `libportaudio64bit.dll` and the README (PortAudio's MIT notice) only. |
| Import audio's converter: `ffmpeg-n9.0.2-39-g7adfbe40dc-win64-lgpl-shared-9.0.zip` from BtbN's FFmpeg builds (release `autobuild-2026-10-31-12-59`, linked from ffmpeg.org's download page; BtbN keeps one build a month for two years) | 76,576,818 bytes, SHA-256 `e907b01fa4783913151237e37df2a10fa97d99a1598a021bf59d4ae62ffed4c0` (the same as GitHub's digest for the asset). From the program itself: configured with `--enable-version3`, no `--enable-gpl`, no `--enable-nonfree`, and `--disable-libx264 --disable-libx265 --disable-libxvid`; `ffmpeg -L` reports the GNU Lesser General Public License, version 3 or later; the zip's `LICENSE.txt` is LGPL-3.0. `ffmpeg.exe` loads avcodec-63, avdevice-63, avfilter-12, avformat-63, avutil-61, swresample-7 and swscale-10. | Fetched on first use (Part 5 of the notices). Move the pin before BtbN drops this build, about October 2028. |

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
  `CREATE_NO_WINDOW`. Checked with BtbN's 9.0.2 build on the PC: m4a, wma
  and webm/Opus decode, and each refusal reads right (not audio, video only
  in mp4, mkv and webm, copy-protected, damaged m4a and wma). FFmpeg 9 says
  why before its last line ("Stream map '' matches no streams." ... "Error
  opening output files: Invalid argument"), so the decoder looks through
  everything it says; the last line is what the log keeps.

## What shipping an LGPL FFmpeg involves (for the Windows download)

The LGPL asks for the licence text and the source of the exact build
(FFmpeg's source and its configuration), and that the library or program can
be replaced. A program fetched on request into the data folder, not in the
installer, belongs in Part 5 of `THIRD-PARTY-NOTICES.txt` ("Downloaded on
request") with its version, build, licence and source location. As done:
the program comes from BtbN's own release (Waffler's installer does not carry
it); `LICENSE.txt` (LGPL-3.0) is kept beside it; Part 5 names the build, its
configuration, the licence and the sources (FFmpeg's release/9.0 branch at
commit 7adfbe40dc, and BtbN's build scripts). It is a separate program in
the data folder, so it can be replaced.

## Other copyleft components, for completeness

- libsndfile (in soundfile's wheel, both builds): LGPL-2.1-or-later, a
  separate shared library.
- pystray (Windows tray): LGPL-3.0, bundled as Python modules inside the
  PyInstaller archive.
- PyInstaller's bootloader: GPL-2.0-or-later with an exception that allows
  distributing it in programs of any licence.
