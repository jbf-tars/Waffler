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

## Re-check on `one-app` 139fe11

`windows` is now `one-app` 139fe11 (which already held the earlier Windows
commits), plus this report and its pictures.

**Suite:** **8,623 passed, 89 skipped, 0 failed** in 7 min 43 s on Windows (86 were skipped last time, and all of those were Mac-only or POSIX-shell tests) (the Mac has 8,689 passed, 18 skipped).

**1. Account file lock (icacls).** The account tests pass on Windows (they are
in the suite). On a real file in a temporary data folder, with a made-up
session and no server contacted:

- `_write_session` wrote `account.json`, and no `.tmp` file was left behind.
- `icacls` then listed one entry: the current user, with full control (F). No
  SYSTEM, no Administrators, nothing inherited.
- Reading it back gave the same session.
- A second write worked and kept the same single entry.
- `sync_credentials()` returned the token, and `forget()` removed the file.

So the icacls path works on Windows as written.

**2. The waffle record mark (Home, Meetings, top bar).** The pictures come from
the app's own WebView2, on a test copy run from source, taken through its
DevTools port. Nothing on James's screen was captured and nothing was recorded.

- In light and dark: `home-idle-*`, `home-recording-*`, `meetings-idle-*`.
- The recording look was made with the states the app pushes:
  `waffler_status('listening')` for the top bar and the live strip, and the Home
  button's `is-on` state with its clock and "Finish", which is what
  `ui/meetings.js` sets from `meetings_state`.
- Nothing is clipped, and the marks are sharp at 100%. They are CSS, so WebView2
  scales them itself at 125% or 150%.
- Not shown: the Meetings tab's own record control while recording. Its state
  comes from the native bridge and changes only in a real meeting, which needs
  James.
- On this test copy (speech on this computer, no key), the Meetings tab shows
  "Add a key in Waffler's Settings to begin" instead of a record button. The
  Advanced text says notes need a key either way, so this looks intended.
  Flagging it in case recording without a key, with notes later, was meant to
  work.

**3. Settings > Keys and providers.** It renders in light and dark
(`settings-keys-*`):

- The two choices are one radio group.
- "On this computer" reads "Free", "Private. One download.", "Ready, 486 MB"
  and has a Remove button. That state is right: `small` is downloaded in this
  test folder.
- "Your own key" reads "Faster" and has the provider picker (Groq, OpenAI,
  Cerebras), the key box, Save, and "Get a free Groq key".
- Advanced opens (`aria-expanded` true; `settings-keys-advanced-*`), showing the
  flow, the provider order and the notes.
- The picker is a native select, so its open list cannot be pictured; its
  options are Groq, OpenAI and Cerebras. No key was typed anywhere.

One bug found here:
- With Advanced open, `P#providerOrderLive.sr-only` (absolute, 1 px) sits at
  y=806 in a 741 px window. It is placed against the page instead of the
  settings pane, so the page itself becomes 65 px taller than the window and can
  scroll: by scroll chaining past the end of the pane, by the keyboard, or by
  `scrollIntoView`.
- In dark mode `html` keeps its light background (rgb 253, 252, 252, while
  `body` is rgb 18, 16, 14). So that scroll shows a 65 px light strip at the
  bottom; `settings-keys-advanced-dark.png` shows it.
- Likely fixes: keep `.sr-only` elements inside the pane (`position: relative`
  on `#settingsScroll`, or `top: 0; left: 0` on `.sr-only`), and give `html`
  the theme's background.
- It is shared CSS, so I left it for the Mac side. It is probably the same in
  WKWebView.

**4. Sync.** Covered by the sync tests in the suite, which pass on Windows. It
was not run against the server, because signing in needs the code from James's
email.

**Still needs James at the PC:** signing in and a sync, a real meeting (the
Meetings tab's record control while recording), and dictation by voice.

## Re-check on `one-app` b5a4156

`windows` now holds `one-app` b5a4156 (merged), plus this report and its
pictures. As before, it was run from source on a test copy with temporary data
folders, and checked through the WebView2 DevTools port. Nothing was signed
into and no code was requested.

**Suite:** **8,828 passed, 1 failed, 89 skipped** in 8 min 8 s on Windows. The one failure was Windows-only and in a test, not the app: tests/test_page_never_taller_than_window.py read ui/style.css without saying utf-8, and Windows reads it as cp1252, which fails on a character in it (UnicodeDecodeError, byte 0x90). That is fixed on this branch (both reads now say utf-8), and the file passes on Windows (2 passed) (the Mac has 8,895 passed, 18 skipped).

**1. The light strip (247b010): fixed.** The test used the same window size,
1084 x 741, in dark mode, with Settings > Keys and providers > Advanced open:

- The page is exactly the window's height (`scrollHeight` 741).
- `<html>` paints nothing (`rgba(0, 0, 0, 0)`).
- Trying to scroll the page itself leaves it at 0.
- `r4-settings-keys-advanced-dark.png` shows the bottom is clean.

**2. Meetings without a key (f766780, b54553d).** All three states seen:

- **Free model, no key:** the Meetings tab shows "New recording", in the
  sidebar and in the middle (`r4-meetings-free-model-no-key.png`).
- **A finished meeting:** I imported an 11.5 s synthetic spoken clip through
  the same `import` route the Import audio button uses. It was turned into
  text on this computer.
  - The Notes tab shows the one quiet line "Notes need a key." with "Open
    Settings" (`r4-meetings-imported-note.png`).
  - The Transcript tab shows the words correctly
    (`r4-meetings-imported-transcript.png`).
- **No model and no key:** the button reads "Download the free model"
  (`r4-meetings-no-model-no-key.png`).

**3. Waffler Plus, signed out: no trace.** The word "Plus" was searched for in
the visible text of Home, every Settings section, the Meetings tab and the
first-run screen, in three states: free model, no model and no key, and first
run. It does not appear. "Waffler Plus" is not in the page source either, not
even hidden.

**4. Suite:** see above.

**Still needs James at the PC:** signing in (which is also where Plus would
show), a sync, a real meeting with the microphone and the computer's sound,
and dictation by voice.

## Re-check on `one-app` a83b306

`windows` now holds `one-app` a83b306 (merged as 66ba100). The merge met one
conflict, in `tests/test_page_never_taller_than_window.py`, where `one-app` had
made the same utf-8 fix as this branch. It was resolved by taking `one-app`'s
copy, which is the same code without the comment.

**Suite:** **8,840 passed, 89 skipped, 0 failed** in 8 min 10 s on Windows (the
Mac has 8,906 passed, 18 skipped). The new
`tests/test_meetings_import_and_parts.py` passes here. Only the suite was run
this time; the Meetings import screen was not looked at on the PC.

## Windows fix: the overlay helper no longer takes the foreground

This is the older problem noted under Pop-ups, now fixed on this branch in
1c6939d. It touches the Windows-only `src/overlay_process_windows.py`; the Mac
is unchanged.

**What was wrong.** When the overlay helper started, Tk built its withdrawn
dictation window as the main loop began, and made it the foreground window
whenever the helper was allowed to take the foreground. Keys typed in the
person's app then went to an invisible window until the dictation pill was
next shown and hidden. Stepping through the start-up showed the moment: the
foreground moved once `mainloop()` began, or at the first `update_idletasks()`,
with the window withdrawn and never visible.

**What changed.** `main()` now:
- notes the foreground window before Tk starts;
- hides the root straight away;
- builds it, marked `WS_EX_NOACTIVATE`, before the main loop;
- hands the foreground back if Tk took it (`_keep_foreground`).

**Measured on this PC,** three runs of each, with the helper started on its
own and the foreground read at each step:
- Before: the helper's hidden window held the foreground from the start until
  the dictation pill was hidden.
- After: the window the person was in kept it through the start, a message
  pill, the dictation pill shown and hidden, and quit.

The pop-up checks above were run again on the fixed helper: same sizes, same
timings, one at a time, and the "Your call" button still answers. The new
`tests/test_windows_overlay_keeps_focus.py` checks the rule with fakes, so it
runs on any platform. It fails on the old helper (5 of 5) and passes on the
new one.

## Windows fix: Sign out no longer leaves the computer signed in

Fixed on this branch in 30e6bb0. It touches the shared `src/account.py` and
`src/atomic_json.py`; the Mac sees a change only if a delete fails there too.

**What was wrong.** A full run of the suite after the overlay fix failed
`tests/test_account.py::test_signing_out_through_the_api_stops_the_sync_state`.
Run alone, it failed between one run in twelve and four in five. The cause
was found by logging the error that `forget()` ignored: `PermissionError`,
winerror 32, "being used by another process". Deleting `account.json` straight
after it was written failed while something still held the file, probably a
scanner or the indexer. `forget()` swallowed the error, `status()` still read
the session, and Sign out answered "signed in". In the app that means the
computer can stay signed in after Sign out.

**What changed.** `forget()` deletes through the new
`atomic_json.unlink_with_retry`. It retries on `PermissionError` with the same
50 to 400 ms waits `replace_with_retry` already uses for the same Windows
reason. It never raises, and it reports False if the file is still there.

**Measured on this PC:** that test passed 12 runs in 12 with the fix. The new
`tests/test_account_forget_retry.py` fails on the old code (4 of 4) and passes
on the new.

**Suite with both fixes, on `one-app` 3272dd7 (merged as cfb1c06):**
**8,859 passed, 89 skipped, 0 failed** in 7 min 57 s on Windows.

## Re-check on `one-app` d8b545e, with two Windows changes

Merged as 2c7f630, with 5c994fa (the graphics card) and 60488e0 (a quiet
microphone in Meetings) on top. **Suite: 8,997 passed, 89 skipped, 0 failed**
in 11 min 57 s on Windows.

**The language follows the person (09faa0d).** On this PC Windows gives
`GetUserDefaultLocaleName` "en-GB" (and `locale.getlocale()` "English_United
Kingdom", which is skipped), so `languages.system_language()` is "en".
`resolve(None)` and `resolve("")` are "en"; `resolve("auto")` sends none.

**The made-up audio test, through the app's own Meetings path** (on-device
`small`, synthetic audio: a SAPI voice with gaps, two blips in silence, a
quiet room with one burst):

| Language | Real lines kept | What the model wrote over the quiet room | Kept of it |
|---|---|---|---|
| unset | 5 of 5 | nothing | 0 |
| "" (this computer's language) | 5 of 5 | nothing | 0 |
| "auto" (the audio rule alone) | 5 of 5 | 3 lines of Welsh | 0 |

The "you" lines written over the blips were dropped in every case.

**Real dictation takes through `_take_is_invented`.** 20 takes on this PC with
their own texts (10 from the test copy on-device today, 10 from the installed
Waffler on 20 November), handed over as the pipeline hands them (boosted when
quiet, padded). Counted and measured only; no text was printed or kept:
20 of 20 kept, at 2.8 to 17.0 letters per voiced second (the limit is 60).
All 20 were quiet recordings: speech at -44 to -60 dBFS, boosted 28 to 30 dB.

**Windows fix: a quiet microphone in Meetings (60488e0).** Dictation lifts a
quiet take before judging it; Meetings judged voice against a fixed
-46 dBFS floor. The same 20 takes, heard raw as a desktop meeting would hear
them from this microphone: **4 never sent, 7 dropped as written over
nothing, 9 kept**. `voiced_mask` now reckons dictation's own lift (towards
-20 dBFS, at most 30 dB, voice 4 times the room) before its floor: **0 not
sent, 0 dropped, 20 kept**, at 7.8 to 21.2 letters per voiced second. The
three Welsh lines above are still dropped, and a normal-level take, steady
noise and digital silence come out as before.
`tests/test_meetings_quiet_mic.py` fails on the old code (4 of 6).

**Windows change: the graphics card (5c994fa).** On this PC "On this
computer" ran on the processor: `cublas64_12.dll not found`, because the
packaged app ships without NVIDIA's libraries. Real dictations took 2.4 to
4.5 s to write down; cloud ones take 0.58 s at the median (last 294).
`src/gpu_pack.py` fetches NVIDIA's own Windows wheels from PyPI on one click
in Settings, never on its own. They are pinned by size and SHA-256:
cuBLAS 12.9.2.10 and cuDNN 9.28.1.4, 1.25 GB. It keeps the 12 DLLs (1.86 GB)
in `<data folder>/gpu`, and the next dictation uses the card with no
restart. The real download through the module: 1,249 MB in 42 s, 54 s in
all. A fresh process with only those files then ran `small` on the card in
0.22 to 0.36 s, against 1.22 to 1.38 s on the processor. In the test copy,
with the same libraries linked in, the first real dictation took 0.43 s.
Where speech runs on the processor, the card's line and setup's free card
now say "Slower on this computer, but free and private."

## Re-check on `one-app` 8ac4fc1 (the pitch check)

Merged as 3116f94; `src/meetings/audioproc.py` is one-app's (my lift, with
8ac4fc1's pitch check on top). **Suite: 9,004 passed, 89 skipped, 0 failed**
in 8 min 44 s on Windows.

The 20 real takes on this PC, heard raw as a desktop meeting: **2 not sent,
0 dropped, 18 kept** (with the lift alone, the same 20 files: 0, 0, 20). The
two lost takes are the quietest and shortest: 1.4 s with one word, and 2.4 s
with 28 letters. Both had their loud windows at -60 dBFS, and 0.21 s of
pitch counted against the 0.3 s needed (median strength 0.66 and 0.39).
Their numbers went to the Mac session to tune the check. The made-up audio
test is unchanged: real lines 5 of 5, and every made-up line dropped.

## Windows: the tidy-up and meeting notes on this computer

`src/local_llm_server.py` (07dafc2) and `src/llama_pack.py`. Windows had no
engine for the writing model, so "On this computer" pasted text as it was
said: no tidy-up, no paragraphs. Now llama.cpp's own server runs the model the
Mac uses (Gemma 4 E4B, 5.2 GB, downloaded and checked here through the app's
own downloader in 166 s), and comes down with it: the CUDA build and NVIDIA's
CUDA runtime with a card, the processor build without.

Measured on this PC through the app's own clean-up and `write_notes`, with no
key, and no connection beyond the computer:

| | On the card (43 of 43 layers, 3.1 GB) | On the processor |
|---|---|---|
| Dictations of 34 to 69 words | 0.41 to 0.76 s | 3.1 to 6.0 s |
| A 200-word meeting's notes | 2.3 s | 16 s |
| First read of the clean-up prompt | about 1 s | about 40 s |

End to end as a new PC gets it: the processor build fetched from GitHub and
run; the CUDA build (this PC's own copy of the pinned zip, SHA-256 checked)
with the runtime fetched from PyPI, run on the card from Waffler's files
only. **Suite: 9,032 passed, 89 skipped, 0 failed** in 8 min 38 s.
