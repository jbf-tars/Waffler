# Changelog

All notable changes to Waffler will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [3.12.6] - 2026-05-09

### Added
- **`OPENAI_STYLE_MODEL` env-var override** for the OpenAI styling model. Defaults to `gpt-4o-mini`; setting `OPENAI_STYLE_MODEL=gpt-4.1-mini` (or any other compatible chat-completions model) flips the styler without a code change. `gpt-4.1-mini` follows strict prompts noticeably better than `gpt-4o-mini` for the same speed bracket and is the recommended upgrade for users who want tighter rule-following.
- **53 new regression cases** in `scripts/auto_test_corpus.py`, taking the suite from 38 to 91 hand-built tests. New coverage:
  - Greeting variations: `Hey James,` / `Morning Sarah,` / `Afternoon team,` / `Dear Mr Thompson,` / `Hi everyone,` / `Hi all,` / `Hi James and Sarah,` / `Hi folks,` / `Hey both,` / `Evening Rohan,`
  - Sign-off variations: `Cheers` (no name), `Best regards, James Farrelly` (full name), `Sincerely,` `Speak soon,` `All the best,` `Thanks again,`
  - Combinations: email + numbered list in body, email + bullets in body, multi-paragraph email
  - Edge: currency (`£2,500` / `$3,300` / `€2,900`), dates and timezones (`12th May 2026 at 3pm UK time`), acronyms (`PR EOD API SIEM`), hyphenated names (`Mary-Jane`), apostrophes (`O'Brien`), URLs, email addresses, version numbers, file paths, mixed `twenty-five` vs `250`, profanity, British spelling
  - Real-world: stand-up update, bug report, code-review comment, Slack-casual, rambling thought
  - Hallucination guards: real `Thank you.` ending, dictated `subscribe to the newsletter` (not Whisper outro), digits/units/percentages, trailing `Amen.`
  - Self-correction: multi-correction, `I mean` as clarification, backtrack-and-restart
  - Negative guards: `Highest priority` (not `Hi-` greeting), `cheers` as celebration in body, `thanks card` in body

### Fixed
- **Sign-off list now includes `Thanks again`, `Thanks so much`, `Many thanks`, `All the best`, `Yours sincerely`, `Yours truly`** in the prompt's recognised sign-off triggers. Previously only `Thanks` (bare) was matched, so `Thanks again, James.` stayed inline at the end of the body instead of splitting into two lines.
- **Numbers, times, dates, units, currency, versions and acronyms are now strictly preserved.** Earlier: dictating `"meeting on 12th May 2026 at 3pm UK time, 230ms response time"` was being normalised by the styler to `"12th may 2026 at 3 PM UK time, 230 ms response time"` — month lowercased, AM/PM uppercased and space-injected, unit space-injected. The `FORMATTING` rule now spells out concrete preservation examples for times (`3pm` stays `3pm`), dates (`12th May 2026` stays exactly), units (`230ms` stays joined), currency (`£2,500`), versions (`v3.12.5`), acronyms (`EOD ASAP API` stay uppercase), URLs and email addresses.

## [3.12.5] - 2026-05-09

### Added
- **Sign-off auto-split in Normal mode.** Mirroring the v3.12.4 greeting-line-break rule, when a transcript ends with a sign-off + name (`Cheers, James` / `Regards, James` / `Thanks, James` / `Best, James` / `Best regards, James` / `Kind regards, James` / `Speak soon, James` / `Talk later, James`), the styler now splits them into two lines: the sign-off ending with a comma on one line, the name on its own line below, with a blank line above the sign-off block. Combined with the greeting rule, dictating a full email-shaped utterance now produces proper email layout in Normal mode without needing the dedicated Email mode. Trigger requires the sign-off pattern to be at the very END of the transcript and includes a guard against false positives where the sign-off word is being used mid-body to address someone (e.g. "Thanks for meeting today, James — it was really useful." stays as a single sentence). Four new corpus tests (3 positive variants + 1 negative guard) lock the behaviour in.

## [3.12.4] - 2026-05-09

### Added
- **Greeting auto-line-break in Normal mode.** When the transcript starts with a clear greeting addressed to a person or group (`Hi James,` / `Hello team,` / `Hey Rohan,` / `Dear Sam,` / `Morning all,`), Normal mode now puts the greeting on its own line with a blank line below it before the body — without needing the user to switch to the dedicated Email mode. Trigger requires (a) sentence-start greeting word, (b) a name or group address, AND (c) a punctuation mark already present in the input. The styler does not invent a comma where none exists. False-positive guards: the rule does NOT fire when the input is talking *about* a greeting (e.g. "Hi guys would never work as an opener") — the next word being a verb/auxiliary with no punctuation tells the styler this is meta-language, not a greeting. Six new corpus tests (4 positive + 2 negative) lock the behaviour in.

### Fixed
- **Email-mode dropdown choice was never persisted.** Selecting "Email" from the sidebar dropdown updated the running pipeline in memory, but `set_mode()` never wrote the choice to `~/.waffler-hosted/settings.json`. On every app restart the user was silently back on Normal. `set_mode` now persists `prompt_style`; `Config._load_env_vars` reads it back so the UI's choice survives restarts. Precedence: settings.json (UI choice) > `PROMPT_STYLE` env var (advanced override) > "normal" default.

## [3.12.3] - 2026-05-09

### Added
- **Email mode.** New `prompts/email.txt` mode that the user explicitly selects from the sidebar dropdown ("Normal" / "Email"). Email mode inherits every never-invent-content / never-paraphrase / never-censor rule from Normal mode and adds permissive paragraphing plus dedicated greeting / sign-off lines when the speaker actually dictated them. If the speaker did not say "Hi Sam," or "Cheers, James", email mode does not invent them — half-an-email stays as plain prose. This replaces the abandoned auto-detect approach (which misfired on prose that vaguely resembled an email).
- **Bigram-collapse vocab fuzzy-matching.** When Whisper splits a compound name into two words ("Ashkan" → "Nash can", "Ashcan", "Ash can"), the existing single-word fuzzy matcher couldn't find it. We now glue every adjacent word pair together and fuzzy-match the joined form against single-word vocab entries, with a slightly looser similarity threshold (0.70 vs 0.75 for unigrams) because gluing always inflates max-length by one. Verified against the actual "Nash can" → "Ashkan" failure from history.
- **Esc as universal cancel for sticky-mode recordings.** macOS swallows the Fn key at HID level on some Macs (M3 Max in particular), so Fn+Space cancel was unreliable. Esc is never typed during dictation, so it's a safe escape hatch in any state. The existing Fn+Space path still works.
- **Regression harness — `scripts/auto_test_corpus.py`** — 28 hand-built cases across six lengths (very-short / short / medium / long / very-long / extreme) and eight categories (prose, numbered-list, bulleted-list, email, double-words, self-correction, hallucination-bait, code/technical). Each case declares must-contain / must-not-contain / must-match / must-not-match assertions plus a word-retention range. CLI flags `--delay` (seconds between calls; default 0.5, bump to 3-5 to test on Groq without tripping per-minute limits) and `--filter` (substring-match a single label for targeted re-runs). Drives the styler directly with text inputs to skip audio capture during prompt iteration.

### Fixed
- **Windows rate-limit toast was never actually visible.** Three real bugs caused "Cleanup skipped" toasts to fire silently on Windows while showing reliably on macOS: (a) the toast `Toplevel` was created *after* the root pill was withdrawn, and Tkinter on Windows won't reliably paint a Toplevel whose master is currently in withdrawn state — Mac's AppKit overlay path doesn't share this Tk quirk; (b) no `update_idletasks()`/`lift()` to force the first paint of a borderless Toplevel; (c) the OS demoted the toast's `-topmost` attribute the moment the active app reclaimed focus, pushing it behind the user's window. All three are addressed: build the toast first then withdraw the pill, force paint after canvas build, re-assert `-topmost` at 100/400/1200 ms after creation. Auto-hide also bumped from 6 s to 9 s so users have time to actually read the rate-limit guidance.
- **Pill not appearing in fullscreen 90% of the time on macOS.** Re-asserts the `collectionBehavior` flags on every "show" so macOS re-evaluates which Space the pill belongs to; switched from `makeKeyAndOrderFront:` to `orderFrontRegardless:` so we no longer steal focus from the fullscreen app. The pill now appears reliably over fullscreen apps without yanking focus.
- **Hallucination stoplist** — when the entire styled output equals a known YouTube outro phrase ("Thanks for watching!", "Please subscribe", "[Music]" and a dozen related variants), we discard the output rather than pasting it. The vocab-echo filter alone didn't catch these because they don't overlap with vocab tokens. The end-of-text strip already caught them as suffixes, but a clip that transcribed *only* to "Thanks for watching!" was being preserved.
- **Word-level stutters in short transcripts.** Short clips (≤10 words, <15% hard-filler density) bypass the LLM via `_is_simple()` and run through `_basic_clean` only. `_basic_clean` previously stripped um/uh but left literal word-repeat stutters intact — "I I think we should ship it." came out unchanged. A new regex collapses any whitespace-separated repeat of the same alphabetic token: "I I" → "I", "the the" → "the". Punctuation between repeats blocks the collapse on purpose ("I, I think" might be a deliberate restart).
- **`Hi Sam.` greetings being dropped by the OpenAI styler.** The HARD RULE against removing spoken greetings was being interpreted inconsistently — Groq honoured it, OpenAI's gpt-4o-mini dropped greetings entirely. Added a worked example to the rule with the exact failing input shape; OpenAI now keeps "Hi Sam." reliably.
- **Sentence-leading `Yeah, …` not being stripped.** The previous filler rule listed "yeah (mid-sentence)", which the LLM read literally and left "Yeah, I do really like the job" untouched. New explicit entry plus worked example: "Yeah, I do really like the job." → "I do really like the job." Same logic for sentence-leading "Right," / "OK," when no clause-linking job is being done.
- **WKNO-MEMPHIS / station-attribution caption credits leaking through.** Real instance from history (`"CLOSED CAPTION PROVIDED BY WKNO-MEMPHIS."`) was leaking past the existing subtitles/translated/captioned-by patterns. Added explicit closed-caption pattern with provider-name match. Mid-text "closed captioning" usage in real speech is preserved.
- **Paragraph break heuristic** loosened. Was: only on explicit "new paragraph" cue. Now: also at concrete topic-shift discourse markers ("so anyway", "moving on", "another thing", "by the way", "ok so" preceded by a complete thought). Long monologues are easier to read.

### Reliability
- **Updater robustness.** `src/updater.py` gains a 45 s stall-timeout (download hangs no longer hold the app), a real-browser User-Agent header (some CDNs reject bare `python-requests` UAs), atomic `.partial → final` rename to prevent half-downloaded files being treated as complete on a crash, and download-progress logging. The "Update available" modal gains a "Download in browser" fallback button when the in-app updater can't fetch.

## [3.12.2] - 2026-04-30

### Fixed
- **Long dictations were silently truncated mid-sentence by the styler.** The two API call sites in `src/style_openai.py` had `max_tokens=512` hardcoded — about 380 words of output. A 732-word recording in the user's history was cut at ~421 styled words, dropping ~290 words including the entire closing section about Waffler itself. Worse, the constructor accepted a `max_tokens=1024` parameter and stored it on `self.max_tokens` but the API calls ignored it entirely, so even the configured default was being silently overridden. The styler now sizes the output token budget against the input length (`max(1024, min(8192, input_words * 3))`) so short utterances get the standard 1024-token headroom and a 30-minute monologue gets up to ~6000 words of room — well past any realistic single dictation. Verified with the actual truncated entry: same 732-word input now produces 716 cleaned words, ending correctly at the final phrase.

## [3.12.1] - 2026-04-24

### Changed
- **OpenAI transcription model upgraded** from `whisper-1` (original 2022 model) to `gpt-4o-mini-transcribe`. The new model is half the price ($0.003/min vs $0.006/min) *and* measurably better at filler words, punctuation, and accents — which is the main quality complaint users hit on the OpenAI fallback path when Groq is rate-limited. The model is configurable via a new `OPENAI_WHISPER_MODEL` environment variable; set it to `gpt-4o-transcribe` for maximum quality at the old whisper-1 price, or `whisper-1` to force the legacy baseline.
- **Custom vocabulary is no longer passed to the styler.** It was being injected into the LLM's system prompt as *"If any of these words were intended by the speaker, use these exact spellings: X, Y, Z."* — and the model treated that as *"use these words"*, substituting vocab entries into clean transcripts (reproducible: "the cost of the project" was at risk of becoming "the COBie of the project"). Whisper's `prompt=` parameter and the post-transcription fuzzy matcher already handle legitimate vocab biasing — the styler has no audio and can only hallucinate, so it never sees the vocab list now.

### Fixed
- **Smart list formatting restored and extended.** A prior prompt rewrite had replaced the original list-aware behaviour with *"do not convert to bullet points or numbered lists unless the speaker clearly dictates a short list"*, so even explicit dictation like *"Number one, X. Number two, Y."* came out as two paragraphs. The FORMATTING section is now rebuilt from scratch with MUST-language, concrete input/output transformations, and triggers for every count phrase the user actually says: `number one/two/…`, `first/second/third/…`, `first of all`, `firstly/secondly/thirdly`, and `next`. The count word itself is always stripped and replaced with `1. `, `2. `, `3. `; the lead-in ("Number one, ..." → "1. ...") is handled correctly.
- **Bullets for unnumbered sequences.** Grocery lists, *"the three things are X, Y, and Z"*, *"runs on Mac, Windows, and Linux"*, and similar parallel-enumeration patterns now auto-bullet with a clean lead-in and one item per line. Conversational prose with commas ("the meeting went well, but the team pushed back, so we revisit Tuesday") still stays prose — the bullet rule requires parallel, discrete items of similar shape, not every comma.
- **Prose lead-in before a list is preserved.** "Here's what we need to do this sprint. Number one, hire… Number two, onboard…" now outputs the lead-in as its own paragraph followed by the numbered list, instead of deleting the intro sentence to make a "pure list" (which violated the hard rule against dropping whole sentences).
- **Whisper hallucination strip extended** beyond the original `thank you` / `thanks for watching` / `please subscribe` trio to cover the full YouTube-outro family: *remember to subscribe*, *don't forget to subscribe*, *like and subscribe*, *subscribe to my channel*, *see you in the next one / next time / later*, *hit/smash the like button*, and tolerance for trailing punctuation variants. Added a short-remainder rule so fragments like "web outfits," left over after stripping a hallucinated tail are also discarded rather than pasted into the clipboard.
- **Vocab-echo guard tightened** so Whisper's prompt-regurgitation on silent audio is caught more reliably: output consisting entirely of vocab tokens is now discarded regardless of length, and short outputs (≤ 10 distinct words) are discarded at ≥ 50% vocab density. Verified live — today's smoke test produced `"Ashkan, COBieQC, COBie"` on silent audio from both `gpt-4o-mini-transcribe` and `gpt-4o-transcribe`, and both were suppressed end-to-end.

### Added
- `scripts/test_*.py` regression harnesses (6 files) — live probes against the real Whisper / styler APIs, 30s between calls, covering the numbered/bullet/prose prompt cases, the vocab injection guard, the hallucination strip, and the OpenAI model-switch contract. Useful anchors for the next prompt change.

## [3.12.0] - 2026-04-24

### Removed
- **Gemini styling backend.** Gemini was cleanup-only — it has no Whisper equivalent for transcription — so running it alongside Groq and OpenAI (which both cover STT *and* cleanup) just added surface area. Dropped the provider pill from the setup wizard, the settings row, the `validate_gemini_key` IPC endpoint, the Gemini path in `style_openai.py`, and the `google-genai` dependency from both requirements files and both PyInstaller specs. Users who had configured only a Gemini key will now be prompted to add a Groq or OpenAI key.
- **Dev-only launchers and duplicates.** `setup.sh`, `setup_windows.bat`, `run.sh`, `run_windows.bat`, `LaunchWaffler.command`, `install-run.bat`, and `install_local_whisper.bat` are gone — end users install from the GitHub release, and the README already shows the three-line `pip install && python app.py` sequence for running from source. `requirements-windows.txt` (hyphen) was an outdated shadow of `requirements_windows.txt` (underscore) and has been removed; the underscore file is what the Windows build actually uses.

### Fixed
- **"Cleanup skipped" toast leaked internals and looked unfinished.** The old toast read *"Groq \`org_01j44ka3s2fc0s81tyzhp399xn\` service tier \`on_demand\` on tokens per day (TPD) reached. Try again in 15m43.488s., or add an OpenAI key..."* — the rate-limit regex in `_style_groq` greedily matched the first `on ... :` in the Groq error, so the org ID and service tier leaked into the UI, and the raw millisecond-precision retry string was passed through untouched. The regex is now anchored on Groq's actual limit vocabulary (`tokens|requests|audio seconds` per `minute|hour|day`) and the pipeline maps that to a readable label ("daily token limit", "per-minute token limit", etc.) and rounds the retry duration up to whole minutes. Toast now reads like a sentence: *"Groq daily token limit hit. Try again in about 16 minutes, or add an OpenAI key in Settings as a fallback."*

### Performance
- **Skip Groq during its own 429 window.** When Groq returns a 429 it also tells us exactly when to retry (e.g. `15m43.488s`). The styler now parses that into an absolute deadline and bypasses Groq entirely until the window expires — every recording during the lockout goes straight to OpenAI instead of wasting a ~200–500ms round-trip hitting Groq just to be rejected again. The next request after the deadline automatically goes back to Groq with no user action; no API or UI change.

### Changed
- **README.** Tech-stack table corrected to match reality — Mac hotkey is Quartz / CoreGraphics, Windows hotkey is a low-level Win32 keyboard hook via `ctypes` (not `pynput`), and the menubar/tray row lists `rumps` (Mac) + `pystray` (Windows). Usage section split into push-to-talk, hands-free (hotkey + Space), and cancel (click × on the recording overlay) so the Space toggle is explicit on both platforms. Run-from-source snippet now points Windows users at `requirements_windows.txt`.

## [3.11.8] - 2026-04-23

### Changed
- **Pre-public-release polish.** Cleaned up unused imports across `src/`, removed unused locals (`latency`, `os`, `Optional`, `NSObject`, etc.), and added a lightweight CI workflow (`.github/workflows/ci.yml`) that runs on every push and PR: pyflakes static analysis (skipping known platform-gated false positives), an "import every module" smoke test, and the styling guardrail test against the real failure-mode corpus. No behaviour change — purely hygiene.

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
