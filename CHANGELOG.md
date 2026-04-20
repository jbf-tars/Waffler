# Changelog

All notable changes to Waffler will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [3.10.4] - 2026-04-23

### Fixed
- **Private Mode failed with `[Errno 2] No such file or directory: 'ffmpeg'` on both macOS and Windows.** `mlx-whisper` and `faster-whisper` shell out to `ffmpeg` to decode recorded audio, but the packaged builds never bundled it. Both specs now copy the build host's `ffmpeg` binary into the `.app` / `.exe` distribution, and `app.py` prepends the bundle directories to `PATH` at startup so the subprocess spawn can find it. CI workflows ensure ffmpeg is installed on the runner before the spec runs.

## [3.10.3] - 2026-04-23

### Fixed
- **Packaged `.app` crashed on launch with `SIGKILL (Code Signature Invalid)`.** `mlx-whisper` transitively depends on `numba`/`llvmlite`, which JIT-compile native code at import time. The hardened-runtime-signed app lacked the required entitlements, so macOS killed the process the first time llvmlite called `mprotect` on an executable page. Added `com.apple.security.cs.allow-jit` and `com.apple.security.cs.allow-unsigned-executable-memory` to `entitlements.plist`.
- **`CFBundleShortVersionString` was stuck at `2.1.19` in the .app bundle plist.** CI's "Sync app version from tag" step only rewrote `src/__init__.py`, not the spec. Spec now reads the version from `src/__init__.py` at build time so the bundle plist, menu "About" string, and update-check compare against the same value.

## [3.10.2] - 2026-04-23

### Fixed
- **Private Mode → Local Whisper failed with `No module named 'mlx_whisper'` in the packaged macOS `.app`.** `mlx-whisper` (Apple Silicon) and `faster-whisper` (Intel) were never listed in `requirements.txt` or bundled by the PyInstaller spec, so the frozen interpreter could not import them. Added them to `requirements.txt` with platform markers and to `Waffler_mac.spec` via `collect_all`, which also pulls in `mlx`'s Metal shaders and native libraries. Local Whisper now works out of the box — no external install step required.

## [3.9.0] - 2026-04-21

### Security
- Fixed XSS vulnerability in update banner (replaced unsafe innerHTML with safe DOM construction)
- Fixed JavaScript injection in notify_js_status (added json.dumps escaping)
- Added URL scheme validation to prevent arbitrary protocol execution (open_url now only allows http/https)

### Changed
- Updated .gitignore to prevent build artifacts (*.dmg, build.log) from being committed
- Added code signing configuration placeholders to .env.example for macOS builds

This release hardens Waffler for open-source distribution with critical security fixes.

## [3.8.7] - 2026-04-18

### Fixed
- **Vocabulary pasted as output on empty/silent recordings.** When the user accidentally pressed the hotkey and released without speaking (or recorded pure silence), Whisper would regurgitate the `prompt` parameter (the custom vocabulary list) verbatim as the transcription — the user's vocab words got pasted into whatever app they were in. This is a documented Whisper failure mode under silence. Added `_is_vocab_echo()` post-filter that discards any transcription that is effectively a repeat of the vocab prompt (exact match, or ≥70% vocab-word overlap in a short output). Real speech containing vocab words (e.g. "I just spoke to Ashkan about Waffler") is unaffected.

### Changed
- Upgraded Groq Whisper model from `whisper-large-v3-turbo` to `whisper-large-v3`. ~15% better accuracy on rare words, proper nouns, and technical terms at the cost of ~400ms extra latency (still well under a second on Groq's LPU hardware).

## [3.8.6] - 2026-04-18

### Fixed
- "Check for Update" said "You're up to date, running vcurrent" even when a newer release existed. Two chained bugs:
  1. GitHub's REST endpoint `/releases/latest` returns **404 Not Found** on this repo because historic releases were never flagged `make_latest=true`. The backend silently treated the 404 as "no update available".
  2. On the 404 (or any API failure), the backend returned `{update_available: false}` with no `current_version`, so the UI fell back to literal text "vcurrent".
- **Fix:** rewrote `check_for_updates` to query `/releases` (list all) and pick the highest-semver non-draft, non-prerelease tag. Always returns `current_version`. Returns an `error` field on failure so the UI can show the actual reason instead of silently claiming "up to date".
- **UI:** distinguishes three states explicitly — update available / up to date / check failed with reason.
- **Workflows:** both release workflows now pass `make_latest: 'true'` so the REST `latest` endpoint works for future releases too (belt + suspenders).

## [3.8.5] - 2026-04-18

### Fixed
- macOS toast popups (e.g. "We couldn't hear you", cancel confirmation) rendered with the border, waffle icon, and buttons but **no heading or body text** — the rectangle looked empty. Cause: the toast drawing code called `drawInRect_withAttributes_` directly on Python strings, relying on PyObjC's implicit `str` → `NSString` bridge, which is no longer reliable in current PyObjC. Switched to constructing an `NSAttributedString` explicitly and calling `drawInRect_`, which works regardless of bridging behaviour. Applied to heading text, body text, and button labels.
- Toast button text also had a wrong attribute dictionary key (`NSMutableParagraphStyle` class object used as a key instead of the `NSParagraphStyleAttributeName` string constant), which meant button text alignment was never applied. Fixed.

## [3.8.4] - 2026-04-18

### Fixed
- macOS "This application can't be opened — error -10661" on launch. The release workflow was notarizing and stapling the DMG but not the .app inside it. When users dragged Waffler.app to /Applications, the stapled ticket stayed on the DMG and the .app had to fall back to online Gatekeeper verification, which fails intermittently on Sequoia 15.2+. The workflow now zips the .app, submits it to Apple for notarization, staples the ticket directly to the .app bundle, then packages the stapled .app into the DMG. The DMG continues to be signed + notarized + stapled for the initial-open check.

## [3.8.3] - 2026-04-18

### Added
- **In-app auto-update**: new "Check for Update" button in Settings → About. Click to check GitHub for a newer release. If one exists, a modal offers to download the installer with a live progress bar. On completion, Waffler closes, the installer applies the upgrade, and the app relaunches automatically.
- Windows path uses Inno Setup's `/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS` silent-upgrade flags.
- macOS path mounts the DMG, swaps `/Applications/Waffler.app`, unmounts, and relaunches via a detached helper script (no Gatekeeper re-prompt since the new .app is already signed).

### Fixed
- Internal version string was not being bumped at build time — shipped installers reported a stale `__version__`, which made the auto-update banner show incorrectly even on the latest version. Both release workflows now substitute `src/__init__.py` `__version__` from the git tag at build time.
- v3.8.2 was tagged but its CI build failed (the version-sync step raised on no-op replacements). Reshipped as v3.8.3 with the regex replaced by a match-count check.

## [3.8.1] - 2026-04-17

### Fixed
- Phantom email wrapping: cleanup no longer injects `Dear Team,` / `Hi,` greetings or `Best regards,\n[Your Name]` sign-offs when the speaker never dictated them
- Meta-preamble leakage: `Here is the cleaned text:` / `Output:` no longer appears in output
- Hallucinated bullet/numbered lists: only applied when the speaker explicitly dictates structure
- Mid-output self-talk (`Wait, it seems there was a pause...`) no longer emitted
- Regex shortcut no longer strips meaning-bearing words (`like`, `basically`, `you know`) from short utterances; only hard fillers (um, uh, erm, ah, er) are stripped without the LLM

### Changed
- Styling model upgraded from `llama-4-scout-17b` to `llama-3.3-70b-versatile` on Groq — stronger instruction-following, preserves letter-spellings and technical terms more reliably
- `prompts/normal.txt` rewritten in Wispr-style: context-aware filler removal, light grammar smoothing allowed, synonym swaps forbidden, question marks preserved, contextual phrases (`to be honest`, `I can't lie to you`, etc.) kept
- Paragraph breaks now triggered by dictated enumeration (`number one...`, `number two...`)

### Added
- Deterministic post-processor (`_strip_hallucinations`) as a belt-and-suspenders guardrail over LLM output — strips leading preambles, injected greetings (only when raw didn't start with one), sign-offs with `[placeholder]` tokens, and collapses 3+ consecutive newlines
- Test harnesses: `tests/test_strip_hallucinations.py` (12 cases), `tests/test_e2e_real.py` (real-pipeline regression), `tests/test_model_bakeoff.py` (Groq model comparison)

## [2.1.19] - 2026-03-27

### Fixed
- Stripped phantom trailing words and prevented phrase relocation in cleanup
- Prevented duplicate prose+list output in cleanup prompt
- Lowered silence detection thresholds for better Whisper acceptance
- Lowered audio sensitivity thresholds for Whisper support
- Fixed permissions page and API key validation

### Changed
- Bumped `requests` dependency from 2.31.0 to 2.33.0

## [2.1.13] - 2026-03-22

### Fixed
- Fn key detection on macOS
- Windowed RMS so pauses don't trigger false silence detection
- Mac-only Fn key enforcement, disabled custom hotkeys on Mac

### Added
- API key guide link to setup wizard
- Developer ID code signing support for macOS builds

## [2.1.2] - 2026-03-20

### Fixed
- Sidebar logo uses waffle icon image with single-colour text
- Tray icon renders correctly on Windows (direct HICON loading)
- Double-paste bug resolved (hook now ignores injected keystrokes)
- Auto-paste restored after SendInput regression
- Brand icon restored, hidden waffle on error toast, fixed key release glitch

### Changed
- Replaced sound wave icon with waffle-with-syrup icon everywhere
- Regenerated icon.ico from brand icon

## [2.0.0] - 2026-03-15

### Added
- Full desktop GUI via pywebview (replacing CLI-only mode)
- Setup wizard for first-run onboarding
- Local transcription history (searchable, stays on device)
- Windows support with native hotkey handling
- macOS menu bar icon
- Recording overlay with VU meter animation
- Snippet/template system
- Audio device selection

### Changed
- Switched from Deepgram to OpenAI Whisper / Groq for transcription
- Switched from MiniMax to GPT-4o-mini for text cleanup
- Complete rewrite of hotkey system (platform-specific implementations)
