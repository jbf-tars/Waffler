# Windows test report: `one-app` on Windows

Tested on 21 November 2026 on James's Windows PC, branch `windows` (made from
`one-app` at 1db2a4a).

**The PC:** Windows 11 Home (10.0.26200), Intel Core i7-14700F (28 threads),
32 GB of memory, NVIDIA RTX 4070 SUPER with 12 GB (most of its memory was in use
by other programs during the test, so speech ran on the processor), Python
3.13.5.

## Summary

- **Test suite:** 7,796 passed, 86 skipped, 0 failed on `one-app`. No
  Windows-only failures. On `windows` after the fix: 7,799 passed, 86 skipped, 0 failed, the 3 new tests included.
- **Fixed:** Meetings never recorded the computer's sound on Windows.
  PyAudioWPatch, which the Windows recorder needs, was neither installed by
  `requirements_windows.txt` nor named in the Windows spec. With it the
  computer's sound is captured, and the on-device engine turns it into the right
  words.
- **From source:** the app starts, and the first-run model download works
  (`small`, 486 MB in 18 seconds). The live checks that need James at the PC
  (dictation by voice, a full meeting, the double tap, signing in) are not done
  yet; see "Not checked".
- **AI apps:** `--mcp` works from source and from the frozen build.
- **Frozen build:** builds from a clean virtualenv with only
  `requirements_windows.txt`, is 247 MB, and contains every new part.
  An installer made from it with the release script is 75 MB; today's public 3.17.2 installer is 38 MB.
- **On both platforms:** three libraries are imported but installed by neither
  requirements file: keyring, imageio-ffmpeg and anthropic. Each has a fallback;
  details in section 5.

## 1. Test suite

How it was run: a virtualenv from `requirements_windows.txt`, then
`python -m pytest -q -p no:cacheprovider`, with `PYTHONPATH=src`, a temporary
`WAFFLER_DATA_DIR`, and `--basetemp` set to a folder of its own. The last one is
needed because this PC cannot write `%TEMP%\pytest-of-james`; that is this
machine, not the code.

- `one-app` 1db2a4a: **7,796 passed, 86 skipped, 0 failed** in 6 min 43 s.
- `windows` with the fix: 7,799 passed, 86 skipped, 0 failed, the 3 new tests included.
- Why tests were skipped: every skipped test is Mac-only or needs a POSIX shell: 50 need Quartz, 23 AppKit, 5 a POSIX shell, 2 IOKit, and 6 other Mac-only parts (rumps, Core Audio, NSSound, the Mac hotkey listener). None of them is a Windows gap.

## 2. Running from source, with a temporary data folder

The app was started with `python app.py` through a small launcher that set
`WAFFLER_DATA_DIR` to an empty temporary folder (James's real Waffler data was
never read or written) and `WAFFLER_ACCOUNT_SERVER` to the test server. A
Python audit hook wrote down every DNS lookup and connection the app's own
process made.

| Check | Result |
|---|---|
| Starts, tray icon, window, first-run screen | Works |
| First run, "Download the free model and get to work" | Works: `small` (486 MB) came from Hugging Face in 18 s and was ready |
| Internet use during that run | Only api.github.com (the update check), huggingface.co and us.aws.cdn.hf.co (the download) |
| Setup's practice step | Opened twice; no key press reached it while I watched, but James had not tried it yet, so this is not a finding |
| Dictation by voice | Not checked yet |
| Dictating with the network off | Checked a different way: the on-device engine run with an audit hook made no connection at all (below). The network was not switched off on James's PC. |
| Meeting with the computer's sound | The parts work (below); a full meeting in the app is not checked yet |
| Notes written after a meeting | Not checked: they need an AI key, and the test folder has none |
| Meetings tab | Not checked yet |
| Double tap to start and finish | `src/windows_hotkey.py` implements `set_meeting_double_tap`; not tried live yet |
| Settings > Account, sign in and sync | Not done: the test server answers from this PC over Tailscale, but signing in needs the 6-digit code from James's email, which only he can type |
| Settings > AI apps | `python app.py --mcp` answers `initialize` (waffler 3.17.2, protocol 2025-06-18), lists 8 tools, and a `search_meetings` call returns a valid empty result; clean exit, nothing on stderr. Adding it to Claude Desktop's settings was not done, because that changes another app's configuration |

**The computer's sound, checked part by part.** With PyAudioWPatch 0.2.12.9,
`SystemSource` (src/meetings/recorder.py) opened the loopback of the default
output (48 kHz, 2 channels) and captured a spoken clip played through the
speakers: 7.4 s, RMS 0.19. The on-device engine (`local_stt_ct2.Ct2Engine`,
`small` on the processor) turned that captured audio into the right words in
1.1 s, with no network connection.

## 3. Fixes on this branch

- `9ffd163` Windows meetings: install and bundle PyAudioWPatch, so computer
  sound is recorded. Changes `requirements_windows.txt` and
  `Waffler_windows.spec` only, plus a new test,
  `tests/test_windows_meeting_audio_packaging.py`, which fails without the fix.
  No shared code is touched, so nothing changes on the Mac.

## 4. Frozen build

- Built with `Waffler_windows.spec` in a clean virtualenv holding only
  `requirements_windows.txt` and PyInstaller (63 packages), as the release
  workflow does. It builds, and `dist/Waffler` is **247 MB**.
- In the build: pyaudiowpatch (with `_portaudiowpatch.cp313-win_amd64.pyd`),
  faster_whisper and ctranslate2, soundfile with `_soundfile_data`, the
  meetings package, meetings_ctl, local_stt_ct2, account, claude_connector, and
  `ui/meetings-app`.
- Not in the build: keyring, imageio_ffmpeg, anthropic (section 5).
- `Waffler.exe --mcp` from the build gives the same answers as from source.
- The built `Waffler.exe` was not started as the full app: the installed
  Waffler and the source run hold the single-instance lock.
- An installer was made from this build with `installer/windows/Waffler.iss` and Inno Setup 6, as the release workflow does: 75.3 MB (75,265,828 bytes). It was not run.

## 5. Imported, but installed by neither requirements file

These are not Windows-only. The Mac's `requirements.txt` does not list them
either, so a clean build on either platform leaves them out.

- **keyring** (src/meetings/keys.py). Without it, a key saved through the
  meetings key store lives in memory only and is gone after a restart. Keys in
  Waffler's `.env` still reach meetings through the environment fallback in
  `get_key`, and nothing in the app calls `set_key` today.
- **imageio-ffmpeg** (src/meetings/service.py, `load_any_audio`). Only needed to
  import audio that soundfile cannot read, such as m4a. Without it, and with no
  ffmpeg on PATH, the import says "This audio format needs ffmpeg".
- **anthropic** (src/meetings/llm.py). Only used when Anthropic is chosen for
  notes.
- pynvml (src/local_stt_ct2.py) is optional and falls back by itself; no
  action needed.

Your call on the Mac side: list them in both requirements files, or keep them
optional and say so where they are used.

Also seen: `scripts/check_doc_drift.py` reports one issue on `one-app` itself,
before any change here. `src/meetings/llm.py:28` lists `gpt-4o-mini` in
`_OPENAI_PREFERRED`. If that fallback list is meant, the guard asks for
`# doc-drift-ok` on the line; otherwise the list wants updating. It is shared
code, so I left it alone.

## 6. Not checked yet (needs James at the PC)

Dictation by voice, setup's practice step, a full 30-second meeting with a
video playing, notes after it, the Meetings tab, the double tap, signing in and
syncing. The test copy is open on the PC with the model downloaded, ready for
these. Nothing on the Mac side was touched.
