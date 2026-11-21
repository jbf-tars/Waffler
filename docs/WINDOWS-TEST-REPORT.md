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
- **Pop-ups (`one-app` 672d27d):** the Windows drawing runs. Every kind draws
  at 40 px with nothing clipped, never takes focus, and shows one at a time
  above the dictation pill. An ignored "Your call" pill stays up. The left end
  matches the design spacing exactly, so nothing was changed. See "Pop-ups".

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
before any change here. `src/meetings/llm.py:28` lists, in `_OPENAI_PREFERRED`,
the older OpenAI model that its stale-openai-styler-model check looks for.
(The name is left out here so this report does not trip the same check.) If
that fallback list is meant, the guard asks for
`# doc-drift-ok` on the line; otherwise the list wants updating. It is shared
code, so I left it alone.

## 6. Not checked yet (needs James at the PC)

Dictation by voice, setup's practice step, a full 30-second meeting with a
video playing, notes after it, the Meetings tab, the double tap, signing in and
syncing. The test copy is open on the PC with the model downloaded, ready for
these. Nothing on the Mac side was touched.

## Pop-ups

Checked on `one-app` at 672d27d, the merge of `popups`. There,
`src/overlay_process_windows.py`, `src/pill.py`, `src/overlay.py` and
`src/user_messages.py` are the same as on `popups` (a3e6f89), which was
checked first with the same results.

**How.** The real Windows helper (`app.py --overlay`) was started on its own,
with a data folder of its own, and sent the app's own messages the way
`src/overlay.py` sends them. For each pill, the window's size, styles and place
were read, the foreground window was noted before and after, and the window's
own pixels were saved. That was PrintWindow on the helper's window, not a
screen capture. Buttons were pressed with the helper's `_toast_click` command.
The pictures are in `docs/windows-test/`; on screen the black corners are
transparent.

**1. Drawing.** Every kind draws: 40 px high, with the width set by its words
(132 to 269 px). The waffle mark, the badge, the words, the button and the
cross sit on one line, and nothing is clipped. The badge is a tick for Done, a
red dot for a Problem, and three dots for Working.

Measured from the pictures: the mark starts 14 px from the left edge and is
22 px wide (28 px with its badge). The words start 14 px after it. Those are
`PAD_LEFT`, `MARK` and `MARK_GAP` in `src/pill.py` exactly, so the left end is
no tighter on Windows than in the design, and nothing was changed.

Scaling: this PC is at 100%, and the scaling was not changed (it is a system
setting). Neither `Waffler.exe` nor `python.exe` declares itself DPI-aware in
its manifest, and the helper does not ask for it. So at 125% or 150% Windows
stretches the helper's windows as pictures: the same layout with nothing
clipped, but slightly soft edges and text. Making it sharp would mean
declaring DPI awareness and scaling every pixel size in `pill.py` by the
display's scale. It was left as is, like the dictation pill.

**2. Each kind.** These were sent as the app sends them. The live events still
need James at the PC: a real dictation, a Transform, a muted microphone, the
network off, and a tap of the hotkey.

| Message sent | Pill on Windows | What it did |
|---|---|---|
| Transform done (Polish), with Show in Journal | Done, "Polished", tick | Went by itself after 2 s |
| `take_limit_stopped(20)` | Working, "Tidying", three dots | Became Done in place on `working_end` |
| Still working on it (Keep waiting, Send later, Cancel) | Your call, "Still working", button "Send later", cross | Stayed up when ignored (still there after 3 s, nothing sent); the button sent `send_later` |
| We couldn't hear you | Problem, "Didn't hear you", red dot, button "Mic" | Stayed until taken down |
| Couldn't reach Groq | Problem, "Couldn't reach Groq", red dot, no button | Stayed until taken down |
| `hold_hint_message("Win + Ctrl")` | Tip, "Hold Win + Ctrl to talk", cross | Stayed until taken down |
| Recording the meeting | Done, "Recording the meeting", tick | Went after 2 s |
| Meeting finished | Done, "Meeting finished", tick | Went after 2 s |

**3. Focus, place, one at a time.**
- Every pill window has `WS_EX_NOACTIVATE`, along with `TOPMOST`,
  `TOOLWINDOW` and `LAYERED`. Showing a pill never moved the foreground.
- With the dictation pill up, the message pill sits 8 px above it, centred on
  it, and does not overlap it.
- Two messages back to back left one pill window: the second replaced the
  first.
- Found on the way, and older than the pills: when the helper starts, its
  hidden dictation-pill window takes the foreground if Windows lets it, and
  keeps it until the dictation pill is next shown and hidden. The old cards on
  the `windows` branch did exactly the same in the same test, so it is not from
  this change. It is worth a separate look, because keys typed in that moment
  go to an invisible window.

**4. Ignoring "Your call".** It is safe at the helper: the pill stays and sends
nothing until a button is pressed. The app's side (it keeps waiting, and
nothing that saves the recording or the words is lost) is covered by
`tests/test_pill_keeps_words.py` and `tests/test_pill_table.py`. Both pass on
Windows; the suite result is below.

**5. Spacing.** As measured in 1, it matches the design. No change was made.

**Suite on `one-app` 672d27d, on Windows:** **8,453 passed, 86 skipped, 0 failed** in 7 min 1 s (8,516 pass on the Mac; the 86 skipped are the Mac-only and POSIX-shell tests, as before).
