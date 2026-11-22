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
