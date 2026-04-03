# Changelog

All notable changes to Waffler will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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
