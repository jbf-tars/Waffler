# Waffler 4.0.0: release readiness

Branch `one-app`, 26 Nov 2026. Nothing pushed, tagged or released.

## Go / no-go (read this first)

| # | Check | State |
|---|-------|-------|
| 1 | 3.17.2 can update itself to 4.0.0 (names, digest, sizes, timeouts) | **Go** (tests; see below) |
| 2 | A notarized 4.0.0 app passes 3.17.2's Mac checks (codesign --deep --strict, spctl) | **Go** for the build installed on this Mac (25 Nov, team 653JYT4U23, ~1 s each). Re-check the tagged build. |
| 3 | A 3.17.2 data folder opens in 4.0.0 with nothing lost and no setup again | **Go** (29 tests) |
| 4 | Security items 3, 6, 7, 8, 9 | **Go**: done, with the notes below |
| 5 | GPLv3 FFmpeg bundled with an MIT app | **Owner decides** (below) |
| 6 | Meeting double-tap now on for people who never used hands-free taps | **Owner confirms** (product call) |
| 7 | Full suite | Green (last line at the end of this file) |
| 8 | Real-machine runs still needed | Windows: update 3.17.2 to 4.0.0 once with the real release; Mac: the same; the CSP check on WebView2 (`scripts/check_csp_webview.py`) |

If 1, 2 (re-checked on the tagged DMG), 3 and 8 hold and 5 and 6 are decided: go.

## 1. The update from 3.17.2 (the one that matters most)

Every existing user reaches 4.0.0 through **3.17.2's** update code, so it was
checked against what the 4.0.0 workflows publish. 3.17.2's `src/updater.py`
and its `check_for_updates` / `start_update_download` are frozen in
`tests/fixtures/v3_17_2/` and driven by `tests/test_update_from_3_17_2.py`
against a stand-in GitHub.

- **Release lookup**: 3.17.2 lists `/repos/jbf-tars/waffler/releases`
  (per_page 20), takes the highest non-draft, non-prerelease tag, and the
  first asset ending `.dmg` (Mac) or `.exe` (Windows). The workflows publish
  exactly one of each: `Waffler-4.0.0-mac.dmg` and `Waffler-Setup-4.0.0.exe`
  (names read from the workflow and the .iss in the test). Both publish with
  `make_latest: 'true'`, neither as draft or prerelease. **Match.**
- **Download address**: `https://github.com/jbf-tars/waffler/releases/download/v4.0.0/...`,
  accepted by 3.17.2's allow-list (https, github.com, `/releases/download/`). **Match.**
- **Digest**: 3.17.2 reads the asset's `digest` from
  `/releases/tags/v4.0.0` (GitHub fills it for every uploaded asset) and
  requires `sha256:<64 hex>` to equal the file. Nothing in the workflows
  changes the file after upload. **Match.**
- **Sizes and timeouts**: no size cap anywhere in 3.17.2. Windows: requests
  with connect 15 s and 30 s between bytes, a 45 s stall rule; Mac: curl with
  only `--connect-timeout 15` on the body (the HEAD has `--max-time 15`).
  The test downloads a full 105 MB installer and a 75 MB disk image through
  3.17.2's own code and verifies the digest. **No limit tripped.**
- **Mac checks**: 3.17.2 runs `codesign --verify --deep --strict` and
  `spctl --assess --type execute` on the app inside the disk image and on
  the staged copy (120 s each). On the notarized 4.0.0 build installed on
  this Mac (157 MB, 405 files) both pass in about 1 s and 1.5 s; the 4.0.0
  team pin passes too. The 120 s `hdiutil attach` limit is far above what a
  75 MB image needs.
- **Version bookkeeping**: 3.17.2 records "4.0.0" (from the file name) before
  restarting; 4.0.0's `check_pending_update` reads it as done and now shows
  "Updated to Waffler 4.0.0" once on Home.
- **Install in place**: Windows: AppId, `{autopf}\Waffler`, `Waffler.exe`,
  per-user install unchanged, so 3.17.2's batch (kill every Waffler.exe, run
  `/VERYSILENT`, relaunch its own path) lands on the same folder. The new
  licence page is skipped by `/VERYSILENT`. Mac: bundle id `com.waffler.app`,
  minimum macOS 14.0 unchanged, so permissions carry over.
- **Known gap (not new)**: the two workflows publish into the same release
  one after the other. During the minutes between them, a 3.17.2 user on the
  other platform sees "no installer" and gets the release page in the
  browser. Not stranded, but publishing both before announcing is kinder.

**What would strand users**: a 4.0.0 DMG whose app fails `codesign --deep
--strict` or `spctl` (an unsigned nested binary). Notarization would reject
that build first, so it would fail the release rather than ship. Re-run the
two commands on the tagged DMG's app before announcing.

## 2. The data folder from 3.17.2

`tests/test_upgrade_from_3_17_2.py` builds a 3.17.2-shaped folder (formats
read from the tag: history with and without ids, a Not sent card with its
WAV, transforms, edited entries, settings with 30 keys, `.env`, vocabulary
with sounds and stars, usage.json and usage.jsonl, snippets, notifications,
setup_complete.json) and runs 4.0.0's own loaders on it: nothing lost, ids
continue, keys read by both the app and the meetings engine, setup counted
complete, no onboarding, no account screen.

Fixed on the way: 3.17's hands-free double-tap (off by default, on for some)
would have been taken over by 4.0's meeting double-tap. Someone who had it on
keeps it (`meeting_double_tap: false`, once). **Owner to confirm**: everyone
else gets two quick taps = start a meeting, which is 4.0's design but new to
them. Also changed in 4.0 (deliberate, not touched): "Suggest words" is off
until switched on.

What a 3.17.2 user sees on first launch: Home, their Journal, the hotkey
working at once; after an in-app update one dismissible line "Updated to
Waffler 4.0.0 / See what's new in Settings, About"; About shows "New in 4.0".
Screen Recording is asked only when they first record a meeting with the
computer's sound.

## 3. Security items

- **(3) Input-source shortcuts** (`src/input_source_popup.py`): nothing is
  changed at launch any more. Settings, Shortcuts shows "Stop the
  keyboard-language popup" only when it would help (Fn is the key, a shortcut
  is on, more than one keyboard language) or while it is on. On: remember
  both entries, then turn them off keeping their keys. Off: put back exactly
  what was there. People who upgrade from a version that changed it silently
  (entries holding only `enabled=false`, which macOS never writes) are
  recorded once, read-only, so the switch shows as on and can be undone.
  Delete all my data and the full reset put them back. Every test uses a fake
  `defaults`. Not run against the real `defaults` on a Mac: try the switch
  once on a Mac with two keyboard languages.
- **(6) Window**: the page reaches only the 161 allow-listed Api methods
  (`src/bridge_gate.py`; `js_api` is an object with no attributes, each
  allowed method is exposed by name). Private names, dunders and attribute
  paths reach nothing (tested through pywebview's own `js_bridge_call`).
  A Content-Security-Policy on `ui/index.html` and the Meetings frame: own
  files only, no inline script, no inline handlers (185 moved to `data-on*`
  attributes bound by `ui/handlers.js`, which evaluates nothing). The page is
  served by pywebview's server on 127.0.0.1 on both platforms, so `'self'`
  works in WKWebView and WebView2. **Remaining**: `script-src` keeps
  `'unsafe-eval'` because pywebview builds its bridge with `new Function` and
  runs `evaluate_js` through `eval`; without it the window has no bridge
  (shown by `scripts/check_csp_webview.py --without-unsafe-eval`). Checked in
  the real WKWebView: PASS. **Run the same script on Windows (WebView2).**
  Also noted, not changed: pywebview's local server answers on a random
  127.0.0.1 port with permissive CORS on its `/js_api/<uid>` endpoint (a
  random id, used only by other platforms' back ends).
- **(7) Download Logs**: no vocabulary files (a count instead); in every text
  file the home folder, user name, computer name and owner-named devices are
  replaced; hotkey.log included; crash.log and hotkey.log capped at 5 MB like
  app.log.
- **(8) Licences**: `scripts/third_party_notices.py` generates
  `THIRD-PARTY-NOTICES.txt` from the venv's own metadata plus hand-written
  entries for bundled binaries; both specs ship it and `LICENSE`; Settings,
  About, Licences opens it; the Windows installer's licence page shows the
  MIT licence. Windows-only packages, the Windows FFmpeg configure line,
  PyAV's DLLs and ctranslate2's libiomp5md were written from documentation,
  not inspected: check them on a Windows build (`ffmpeg -version`,
  `_internal/av.libs`).
- **(9) Personal text**: the prompt example, corpus cases OPL2-4, FT3, EXT1,
  names from the owner's word list, paths, the PC's name and a VPN address
  are neutral now (history not rewritten). The corpus scripts no longer read
  the keys file when a test imports them (they did, so earlier test runs
  loaded the owner's `.env` into the test process; nothing was printed).
  Left: "James" as a generic example name in the prompt (changing it moves a
  measured prompt hash), author lines, two first names (Ashkan, Nour) many
  sound-alike tests depend on, and one comment in `app.py` inside the
  dictation path (another session is editing there).

## 4. The licence question (owner decides)

Waffler is MIT. The builds bundle FFmpeg executables from imageio-ffmpeg:
on the Mac an osxexperts build configured `--enable-gpl` (GPL v2 or later,
with x264/x265 linked in); on Windows a gyan.dev "essentials" build, normally
`--enable-gpl --enable-version3` (GPLv3). The app runs it as a separate
program for Import audio. Shipping a GPL binary generally means shipping its
licence text and its exact source (or a written offer) with it; whether one
installer holding the MIT app and a GPL executable is mere aggregation is the
open question. Two clean alternatives:

1. Ship an LGPL-only FFmpeg build (no `--enable-gpl`, no x264/x265).
2. Do not bundle FFmpeg: download it on first use of Import audio.

Details: `docs/THIRD-PARTY-LICENSING.md`.

## 5. Docs

CHANGELOG 4.0.0 checked against `git log v3.17.2..HEAD` (claims that the code
did not support were corrected; a Security and privacy section added).
README, CONTRIBUTING, SECURITY, RELEASE (asset sizes) and the Settings
wording describe the merged product; no "Natter" in user-facing text.
Import from Granola falls back to a button on the Meetings panel when it
cannot be planted in the frame.

## 6. Merge note for the Windows session

This branch renamed every `onclick=` / `onchange=` ... in `ui/index.html` and
in markup `ui/app.js` draws (the key card button `openKeyEditor` included) to
`data-onclick=` / `data-onchange=` ...; any new markup must use the `data-on*`
form or `tests/test_csp.py` fails. A new public `Api` method must be added to
`src/bridge_gate.py ALLOWED` or the window cannot call it
(`tests/test_bridge_gate.py` says so).

## Full suite

`9561 passed, 22 skipped, 1 warning in 369.11s` at 1ea60a2 (this file added after, docs only).
