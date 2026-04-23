# Changelog

All notable changes to Waffler will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [3.11.7] - 2026-04-23

### Fixed
- **Pipeline froze on long recordings ("stuck on processing for ages").** `sounddevice`'s `_stream.stop()` calls into CoreAudio (PortAudio on Windows). Both can wedge for tens of seconds — sometimes indefinitely — on long recordings or after a device hot-swap. While `_stream.stop()` was hung, the recording-stream lock was held forever, so every subsequent recording's `start()` and `stop()` blocked behind it. The pill stayed visible (looked like the app was "still processing"), but the pipeline was actually deadlocked on a kernel call before any LLM request was ever made. `audio.stop()` now snapshots the recorded buffer first, runs the blocking close in a daemon thread with a 1.5 s watchdog, and continues regardless — the captured audio is returned and processed even if the underlying stream object never finishes closing.

## [3.11.6] - 2026-04-23

### Fixed
- **"Cleanup skipped" toast gave unhelpful advice.** The old copy ("Groq limit hit — pasted raw. Try again in a minute.") was wrong in two ways: Groq's TPD (tokens-per-day) limit doesn't reset in a minute, and it didn't tell the user what to actually do. The Groq 429 response carries the exact retry time and which specific limit was tripped — `_style_groq` now parses both and passes them through. The toast reads the real thing: e.g. "Groq tokens per day reached. Try again in 8m52s, or add an OpenAI key in Settings as a fallback." Honest and actionable.

## [3.11.5] - 2026-04-23

### Fixed
- **"We couldn't hear you" toast fired on speech it should have accepted.** Silence detection windowed the recording into 1-second chunks and required RMS ≥ 30 in at least one. A short "hello" (~0.5 s) got diluted by the silence around it in the 1 s window to well under 30, so it was rejected despite being clearly audible. Shrank windows from 1 s → 0.25 s so brief utterances aren't diluted, and dropped the threshold from 30 → 12 (room tone is ~3-8 on a well-gained mic, so 12 still filters true silence without killing soft speech).
- **Toast and pill competed visually.** The toast has its own sad-waffle icon; the pill (which sits right below the toast) has the regular waffle icon. Users saw two waffles stacked right on top of each other, which looked cluttered and made the toast feel pasted-on. The pill now hides while any toast is visible and restores itself when the toast dismisses — same behaviour on both platforms.

## [3.11.4] - 2026-04-23

### Fixed
- **App opened twice on first launch (macOS).** The bundle's `Info.plist` had no `LSMultipleInstancesProhibited`, so when a race or re-invocation happened during startup (e.g. Dock click while the app was still coming up, or `open -a Waffler` firing during initialization), macOS's Launch Services would helpfully spin up a second full Waffler. Users ended up with two pills, two history watchers, and double the memory. Set `LSMultipleInstancesProhibited: True` so subsequent invocations reactivate the running instance instead of launching another copy.
- **`CFBundleShortVersionString` was hardcoded at `2.1.19` in every shipped `.app`.** CI's "Sync app version from tag" step rewrote `src/__init__.py` but never touched the PyInstaller spec's Info.plist. Result: the version the app self-reported in the UI (`__version__`) was correct, but every macOS-level surface — Finder's Get Info, the About menu, the bundle's plist — lied about the version. Spec now reads the version from `src/__init__.py` at build time so every surface agrees.

## [3.11.3] - 2026-04-23

### Fixed
- **Toast body text was cut off.** The body rect was 30 px tall — only enough for two lines — but several of the newer messages wrapped onto three. Third line silently dropped off the bottom of the toast. Raised toast height from 170 → 210 px on both platforms and the body rect from 30 → 60 px. All messages now render in full.
- **Dismiss button was unresponsive on macOS.** The toast lives in a borderless floating `NSWindow`, which can't become "key". Without `acceptsFirstMouse_` on the content view, the first click was swallowed as a window-activation attempt instead of hitting the Dismiss zone. Added the method so clicks fire on the first try, every time.
- **Toasts stayed on screen forever.** Neither platform had an auto-dismiss — a transient warning could sit there indefinitely if the user didn't click Dismiss. Now `warn` and `error` toasts auto-dismiss after 6 seconds on both platforms (macOS via `threading.Timer` + command queue; Windows via `tk.after`). `cancel` toasts still require explicit user action, since they ask a question.
- **Waffle icon had no sad face on macOS.** Windows has been drawing worried eyes, a curved frown, and a syrup tear since the feature landed; the macOS port only ever drew the 3×3 grid. Added the full face (two eyes, Bezier frown, tear from the left eye) so the icon matches.
- **Fallback toast copy was unclear.** "Styling rate-limited" / "Styling offline" / "Styling unavailable" made users ask "what does that mean?" All three now share the heading "Cleanup skipped" with a body that states what happened and what to do — e.g. "Groq limit hit — pasted raw. Try again in a minute."

## [3.11.2] - 2026-04-23

### Fixed
- **Toasts sometimes didn't fire on macOS.** If the overlay subprocess had died (e.g. after a pipeline error), `show_toast()` silently dropped the call instead of reviving the subprocess — so the toast that was supposed to explain *why* things broke never appeared. `show_toast()` now runs the same auto-restart path as `update_level()`: if the subprocess is dead, relaunch it, send a `show`, then fire the toast.
- **Non-mic errors showed a useless "Select mic" button.** Connection failures, rate-limit hits, access-denied responses, recording-too-long warnings, and the generic "Something went wrong" toast all offered Select mic as the first action — which obviously doesn't fix any of those problems. Those six scenarios now use the `warn` style (single centred Dismiss). Select mic only appears on the one toast that's actually about the mic: "We couldn't hear you".
- **Toast body text colour on macOS didn't match the theme.** Was flat grey `#888888` against a warm dark-brown fill + gold border — looked pasted-in. Now warm tan `#A89070`, matching Windows. Heading also tuned from cream to pale gold to match.

## [3.11.1] - 2026-04-23

### Fixed
- **macOS toast buttons styled inconsistently with Windows.** The "Select mic" button on macOS was rendered in Tailwind violet (`#7c3aed`) left over from an old revision; Windows was already on the waffle-gold theme (`#C8A256` / `#D4A843`). The Discard button was also flat red on Mac but a proper dark-red-with-red-outline on Windows. Mac now matches Windows exactly — one palette, one theme.
- **Silent quality degradation when all styling providers fail.** When Groq rate-limited (free tier: 30 rpm, 500k tokens/day on llama-3.3-70b) and no OpenAI / Gemini key was configured, the styler fell through to a regex-only filler-word stripper (`_basic_clean`) with no user-visible signal. Users would paste near-raw transcripts and not know why cleanup stopped working. Now: the fallback still runs (so you still get text), but a toast appears with a clear reason — "Styling rate-limited" / "Styling offline" / "Styling unavailable" — and the body explains what's happening.

### Added
- Third toast style, `warn`, with a single centred **Dismiss** button (applies to both platforms).

## [3.11.0] - 2026-04-23

### Removed
- **Private Mode.** Local Gemma 4 styling took minutes per long clip even on an M3 Max (LLMs max out around 50 tok/s on Apple Silicon vs. Groq's 500+ tok/s on LPU hardware), so the fully-offline pipeline was never going to match cloud latency for the cleanup step. The v3.10.x line shipped with a series of platform-packaging patches (mlx-whisper bundling, JIT entitlements, ffmpeg bundling) that are also rolled back here since they only existed to support the local stack. Revert scope: the v3.10.0 feature merge plus v3.10.1 → v3.10.4. Transcription / styling routes back through Groq + OpenAI only.

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
