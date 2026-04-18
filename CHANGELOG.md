# Changelog

All notable changes to Waffler will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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
