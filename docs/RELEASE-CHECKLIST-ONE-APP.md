# Release checklist: the one-app branch, Mac

What a signed Mac release of this branch needs, written after building it
locally on 21 Nov 2026 (branch `mac-installer`, cut from `one-app`). Nothing
was released, signed with a Developer ID, notarised or uploaded.

The existing runbook is `RELEASE.md`; this page is only what is new or
different for a build that carries meetings, on-device speech and writing,
accounts, the Claude connector, Try it and the new pills.

---

## 1) What was built and proved here

Built with the repo's own script, on an Apple Silicon Mac (macOS 26.4):

```bash
PYTHON=.build-env/venv/bin/python WAFFLER_BUILD_NO_COPY=1 ./build_mac.sh
```

- Python 3.11.14 (a uv-managed build, kept inside the worktree in
  `.build-env/`; the release uses `actions/setup-python` 3.11, which is a
  different build of the same version), PyInstaller 6.22.3.
- `requirements.txt`, plus `llama-cpp-python==0.3.37` compiled with the pip
  line written in `requirements.txt` (no CMake was installed on the Mac: pip
  fetched it for the build; Xcode's compiler was there).
- Signed ad hoc (`codesign -s -`), which is what `build_mac.sh` does.

| Proof | Result |
|---|---|
| `Waffler --self-check --complete` in the built app | passes: 25 modules, 17 files and the meetings page's own scripts, whisper.cpp and llama.cpp both answer with Metal, the computer-sound helper, anthropic and ffmpeg are inside |
| `Waffler --mcp` over a seeded meeting and three dictations | `initialize`, `tools/list` (8 tools), `get_latest_meeting`, `search_dictations` all answer; stdout is protocol only |
| `Waffler --overlay`, driven by `scripts/drive_overlay.py --app` | drew Done, Your call, Problem, Working then Done; its log has a `pill:` line for each; both buttons answered |
| `Waffler --self-check --audio memo.m4a`, no ffmpeg on the PATH | a 2.0 s `.m4a` made with `afconvert` is decoded by the app's own ffmpeg |
| DMG mounts; `codesign --verify --deep --strict` on the app inside | valid; the app copied out of the DMG passes the same three checks |
| `scripts/check_macos_minos.py dist/Waffler.app` | OK against the declared 14.0.0 |
| Hardened runtime (ad hoc, on a scratch copy) with `entitlements.plist` | everything above still passes, see section 3 |

`scripts/check_built_app.py dist/Waffler.app` runs the first, second and
fourth rows in one go, with no window. `.github/workflows/install-smoke.yml`
was run by hand up to the point where it starts the app with its window
(mount, copy out, `codesign`, `spctl`, minimum macOS); the windowed start was
replaced by `check_built_app.py`, because a second Waffler could not be
opened on this Mac while the owner's was running.

### What had to be fixed to get there

1. **The computer-sound helper was never built or bundled.**
   `src/meetings/recorder.py` looks for `natter-sysaudio` inside the app, but
   neither the spec, `build_mac.sh` nor the workflow built `mac/sysaudio`. A
   built app would have recorded the microphone only, silently. The spec now
   bundles `mac/build/natter-sysaudio` (it lands in `Contents/Frameworks`,
   inside the signature) and the recorder looks there.
2. **`imageio-ffmpeg` and `anthropic` were installed by neither requirements
   file.** Import audio refused `.m4a` (iPhone voice memos) and choosing
   Anthropic for notes failed at the import. Both are now in
   `requirements.txt` and `requirements_windows.txt` and named in both specs.
   `keyring` is left out on purpose; `--self-check` reports
   `"meeting_keys": {"keyring": false, "store": "memory"}`.
3. **`build_mac.sh` had its own, stale dependency list** (no soundfile, no
   speech engine) and always used the first `python3`. It now installs
   `requirements.txt`, takes `PYTHON=`, builds the helper, and stops if the
   built app's `--self-check` fails.
4. **The DMG step failed with a Waffler disk already open** in `/Volumes`
   (a downloaded release). `dmg-assets/create-dmg.sh` was also still naming
   v3.2.3. It now fills the disk at a mount point of its own.
5. **`--self-check`** did not exist (`src/self_check.py`, tested in
   `tests/test_self_check.py`).

---

## 2) What must change in `.github/workflows/macos-release.yml`

The workflow was not edited on this branch. These are the changes it needs.

**a. Install the writing engine.** `requirements.txt` does not install
`llama-cpp-python` (PyPI has no binary wheel; pip compiles it). Without this
step the build still succeeds and the app simply never offers the writing
model. After `pip install -r requirements.txt`:

```yaml
          MACOSX_DEPLOYMENT_TARGET=14.0 \
          CMAKE_ARGS="-DGGML_METAL=on -DCMAKE_OSX_DEPLOYMENT_TARGET=14.0" \
            pip install "llama-cpp-python==0.3.37" --no-binary llama-cpp-python
```

The deployment target matters: without it the libraries are built for the
runner's macOS (15) and `scripts/check_macos_minos.py` fails the build against
the declared 14.0. Built this way here, every llama library reports minos 14.0.
The compile adds a few minutes to a run that RELEASE.md describes as "~3
minutes".

**b. Build the computer-sound helper before PyInstaller.**

```yaml
      - name: Build the computer-sound helper
        run: sh mac/build_sysaudio.sh
```

It needs `swiftc` and `lipo` (Xcode, present on `macos-15`). It builds a
universal binary for macOS 13.0 and signs it ad hoc; the workflow's existing
"Sign all frameworks" loop (`find Contents/Frameworks -type f -perm +111`)
re-signs it with the Developer ID and hardened runtime, and the same loop
covers the bundled `ffmpeg`. No new signing step is needed, but see risk 3.

**c. Check the signed app before it is notarised.** Straight after the bundle
is signed:

```yaml
          codesign --verify --deep --strict --verbose=2 dist/Waffler.app
          python scripts/check_built_app.py dist/Waffler.app
```

This is the first moment the engines, the helper lookup, ffmpeg and the
connector are run under the real signature with hardened runtime and library
validation on. It opens no window and needs no screen, so it works on a
runner. It fails the run if an engine, the helper, anthropic or ffmpeg is
missing (`--self-check --complete`).

**d. Nothing else in the workflow has to change** for the new dependencies:
`pywhispercpp`, `imageio-ffmpeg` and `anthropic` come from `requirements.txt`,
and the spec bundles them. The "Package DMG" step is fine on a runner (it
assumes nothing else is mounted at `/Volumes/Waffler`).

**e. RELEASE.md, step 3** says the DMG is "~24 MB". It will be about 77 MB
(section 5). Change the expected size or the check will look wrong.

---

## 3) Entitlements and Info.plist

`entitlements.plist` today: `device.audio-input`, `automation.apple-events`,
`network.client`, `app-sandbox` false, `device.input-monitoring`.

| New feature | Needs | Status |
|---|---|---|
| Meeting microphone (`--meeting-mic`, the same executable) | `com.apple.security.device.audio-input` + `NSMicrophoneUsageDescription` | both present |
| Computer sound in a meeting (`natter-sysaudio`, ScreenCaptureKit) | no entitlement: it is the "Screen & System Audio Recording" permission, granted by the person in System Settings. The helper is signed with hardened runtime and no entitlements of its own | nothing to add; must be tested signed (section 4) |
| Accounts, sync, model downloads, Anthropic | outgoing connections. The app is not sandboxed, so `network.client` is not what allows them, but it is there | nothing to add |
| whisper.cpp and llama.cpp with Metal, under hardened runtime | no `allow-jit`, `allow-unsigned-executable-memory` or `disable-library-validation`, as long as every bundled binary carries the same Team ID | checked as far as an ad hoc signature can, below |
| The Claude connector (`--mcp`) | nothing: it reads the data folder and talks on stdin/stdout | checked |

**The hardened-runtime check done here.** A scratch copy of the app was
signed ad hoc with `--options runtime`, binary by binary in the workflow's
order, with the repo's `entitlements.plist`. With exactly those entitlements
it would not start at all: an ad hoc signature has no Team ID, so library
validation refused `libpython3.11.dylib`. With one test-only addition
(`com.apple.security.cs.disable-library-validation`, in a scratch file, not in
the repo) the self-check, both engines with Metal, the `.m4a` import through
ffmpeg, the connector and the pills all passed. So nothing in the new code
needs more than the present entitlements **provided the Developer ID signs
every binary**: that proviso is what step 2c checks. Do not add
`disable-library-validation` to the repo to "fix" a failure there; find the
binary that was not signed.

**Info.plist strings** (in `Waffler_mac.spec`), wording for the owner to decide:

- `NSMicrophoneUsageDescription` says "for voice transcription". Meetings now
  record the microphone for as long as a meeting lasts; the prompt should say
  so (for example "…to type what you dictate and to record your side of a
  meeting").
- `NSLocalNetworkUsageDescription` ends "No data is sent over the network."
  With accounts and sync that sentence is no longer true as a general
  statement, even though the string is only about the local network.
- There is no usage string for screen or system-audio capture through
  ScreenCaptureKit; macOS writes that prompt itself. (If the helper is ever
  moved to Core Audio taps, `NSAudioCaptureUsageDescription` becomes
  required. Not needed today.)
- `LSMinimumSystemVersion` stays 14.0.0: the helper is built for 13.0, ffmpeg
  for 12.0, llama for 14.0 with the pip line above.

---

## 4) Recording computer sound: what only a signed build can show

The helper was **not started** here, not even once: starting it asks for the
Screen & System Audio Recording permission on the owner's Mac, and the answer
is remembered against the signature. An ad hoc build has a new identity every
time it is built, so a grant given to one build says nothing about the next,
and nothing about the release. All of this needs the Developer ID build:

- [ ] The first meeting asks for "Screen & System Audio Recording" and the
      prompt names **Waffler**, not `natter-sysaudio` or Terminal.
- [ ] After allowing it, does Waffler have to be quit and reopened before the
      helper gets sound? (macOS usually asks for that.) Does the app say so?
- [ ] The helper starts under hardened runtime with no entitlements of its
      own (`ready 48000` on its stderr) and the meeting has an "Others" track
      with real sound in it.
- [ ] Refusing the permission gives the plain message, and the meeting still
      records the microphone.
- [ ] Installing the next signed version over this one keeps the grant (same
      Team ID and bundle identifier `com.waffler.app`).
- [ ] On macOS 15 and later: whether the system's recurring reminder about
      apps that record the screen appears for Waffler, and the menu-bar
      indicator while a meeting runs. The helper captures a
      display in order to get its sound, so macOS treats Waffler as a screen
      recorder, and people will see that wording.
- [ ] Dictation's microphone and a meeting's microphone at the same moment
      (two processes, one device).

---

## 5) Size

| | App | DMG |
|---|---|---|
| The last release, per RELEASE.md | | about 24 MB |
| This branch, engines and helper, before ffmpeg and anthropic | 99 MB | 49,561,243 bytes (49.6 MB) |
| This branch as it is now | 153 MB | 76,960,629 bytes (77.0 MB) |

So `imageio-ffmpeg` and `anthropic` together add about 54 MB to the app and
about 27 MB to the download. (The DMG's size moves by a megabyte or so from
one build of the same code to the next; three builds gave 77.0 to 78.8 MB.)
Almost all of it is the ffmpeg program (49 MB on disk); anthropic is Python
source and compresses to very little.

Where the 153 MB goes: ffmpeg 49 MB, llama.cpp 35 MB, Python itself 16 MB,
NumPy 7 MB, the rest small. The llama.cpp figure is three times what it needs
to be: pip installs each library under three names (`libllama.dylib`,
`libllama.1.dylib`, `libllama.1.0.0.dylib`) as three real files, and
`collect_dynamic_libs` copies all 21. Keeping one of each and linking the
other names would save about 23 MB on disk (less in the DMG, which compresses
them). Not done here: it changes what gets signed, so it belongs with a signed
test.

No model is in the build. The speech model (about 0.6 GB) and the writing
model (5.2 GB) are downloaded from Settings.

---

## 6) What the owner does by hand

1. **Decide which repository releases.** The workflow and its secrets live in
   the public repository (`origin`). This branch exists only in the private
   one. GitHub secrets belong to one repository: a tag pushed to the private
   repository would run the workflow there without them. And `softprops/action-gh-release` publishes to whichever
   repository ran it, while the website's `bump-release` script reads
   releases from the public one.
2. **Secrets** (names only; values are in GitHub, Settings, Secrets and
   variables, Actions, of the repository that releases, and nowhere in this
   repository): `DEVELOPER_ID_APPLICATION_CERT` (the .p12, base64),
   `DEVELOPER_ID_APPLICATION_PASSWORD`, `KEYCHAIN_PASSWORD`,
   `SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_APP_PASSWORD`, `APPLE_TEAM_ID`.
   Whether they are all present and current was not checked here.
3. **Certificate and Apple account.** The Developer ID Application
   certificate has not expired; the app-specific password still works; no new
   Apple developer agreement is waiting to be accepted (notarytool refuses
   until it is).
4. **Make the three workflow changes in section 2**, bump `__version__` and
   CHANGELOG together (RELEASE.md step 1).
5. **Dry run before any tag.** Run the workflow by hand (`workflow_dispatch`)
   on the branch: it builds, signs and notarises but publishes nothing,
   because only a `v*` tag publishes. Read the log for step 2c. Then run
   `install-smoke.yml` against that run. **Pushing a `v*` tag publishes at
   once, marked latest**, which is where the app's own version check and the
   website look.
6. **On a Mac that has never had Waffler**, with the notarised DMG: it opens
   with no Gatekeeper warning; the main window opens (never opened here);
   dictation still works; the Meetings tab loads; section 4; the speech model
   downloads and transcribes; the writing model downloads and writes notes;
   an `.m4a` voice memo imports; sign in and sync; connect to Claude from
   Settings and ask Claude for the latest meeting.
7. **ffmpeg's licence.** The bundled ffmpeg is a GPL build (`--enable-gpl`,
   with x264 and x265). Waffler runs it as a separate program, but shipping
   it carries GPL obligations for that program (its licence text, and where
   its source can be had). This needs a decision by the owner, not by a build
   script.
8. Website bump and announcement as in RELEASE.md.

---

## 7) Risks seen

1. **The release would ship different library versions from the ones the
   tests ran on.** `requirements.txt` only sets lower bounds, so a clean build
   takes today's newest: openai 3.57.0 (the test environment has 2.32.0, a
   major version apart), groq 1.7.0 (1.2.0), pyobjc 13.0 (12.1), NumPy 2.4.6
   (2.4.4). The suite passes with them (section 8), but the suite never
   calls the real services, and the main window and a real dictation were
   not run against these versions. Pinning the versions a release is built
   with would close this.
2. **The frozen app's main window was never opened.** Everything proved here
   went through `--self-check`, `--mcp` and `--overlay`. pywebview, the menu
   bar item, the hotkey tap and the pages themselves are unproved in this
   build.
3. **The workflow's signing loops end in `|| true`.** A binary that fails to
   sign is passed over, and the first sign of it is a notarisation refusal or
   a launch failure. There are three new kinds of binary to sign (llama's 21
   libraries, `natter-sysaudio`, `ffmpeg`). Step 2c is the guard.
4. **Two different ggml libraries are loaded in one process**: whisper.cpp
   brings ggml 0.13.1 and llama.cpp brings its own. Both initialised Metal in
   the same process in the self-check, but no model was loaded and nothing
   ran both at once (a meeting being transcribed while notes are written).
5. **macOS will describe Waffler as recording the screen** (section 4).
6. **Download size triples** (24 MB to 77 MB), and the app asks for 0.6 GB
   and 5.2 GB more on first use of the on-device engines.
7. **Apple Silicon only.** `macos-15` runners are arm64 and the build is
   thin arm64; `pywhispercpp` is only installed there. The helper is
   universal, nothing else is. An Intel Mac gets no build from this workflow.
8. **`ui/meetings-app` is a committed build of another repository's source.**
   Nothing in this repository rebuilds it; a release ships whatever was last
   copied in. `--self-check` only confirms the files its `index.html` names
   are present.
9. **`--self-check` prints nothing in a windowed Windows build** (no stdout);
   only its exit code says how it went there.
10. **Running the workflow's DMG step by hand on a Mac with a Waffler disk
    open** detaches the wrong disk (`hdiutil detach /Volumes/Waffler`). Use
    `dmg-assets/create-dmg.sh` locally instead; it does not place the icons.
11. **PyInstaller warned** that `libswiftCoreMedia.dylib` could not be
    resolved for pyobjc's CoreMedia module. It is a system library on macOS
    14 and the engines and pills ran, but nothing here used CoreMedia from
    Python.
12. **The developer environment at `/Users/james/waffler/venv` has neither
    `imageio-ffmpeg` nor `anthropic`** (it was not touched). Running from
    source there, Import audio still refuses `.m4a` unless ffmpeg is on the
    PATH, until `pip install -r requirements.txt` is run in it.

---

## 8) Tests

- The whole suite, in the environment the tests are normally run in
  (`/Users/james/waffler/venv`, Python 3.14.4), after the last change:
  **8,530 passed, 18 skipped, 0 failed** (8,516 passed before this branch;
  the 14 more are 11 in `tests/test_self_check.py` and 3 that came with the
  merge of `one-app`).
- The whole suite once in the build environment too (Python 3.11.14 with the
  versions a clean release build installs, openai 3.57.0 among them):
  8,517 passed, 21 skipped, 1 failed. The one failure was a packaging test
  that reads `Waffler_mac.spec` for an exact line this branch had changed; it
  was corrected and that file then passed in both environments. The full
  suite was not run a second time in the build environment.
- `scripts/check_doc_drift.py` is clean. It had one finding on `one-app`
  (the meetings' OpenAI model preference list in `src/meetings/llm.py`),
  settled the way the guard asks, with `# doc-drift-ok` on that line.

---

## 9) Not verified

- The main window of the frozen app, and anything reached through it.
- A Developer ID signature, notarisation, stapling, Gatekeeper on a clean Mac
  (`spctl` rejects the ad hoc build, as it should).
- The computer-sound helper actually recording (section 4), and the meeting
  microphone helper (`--meeting-mic`), which would have asked for the
  microphone.
- An Intel Mac, and any macOS older than 26.4. The declared minimum of 14.0
  was checked from the binaries' headers only.
- A real model in the frozen app: both engines were started, neither was
  given a model.
- The Windows build. `Waffler_windows.spec` and `requirements_windows.txt`
  were edited to match (imageio-ffmpeg, anthropic) and are covered by a
  text-reading test only.
- The DMG's Finder layout (icon positions over the background): the Finder
  script was not run here.
