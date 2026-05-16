# Changelog

All notable changes to Waffler will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [3.14.40] - 2026-05-16

### Fixed
- **Setup wizard API-key validation parity across providers.** Groq and OpenAI key inputs in the setup wizard already had client-side prefix checks (`gsk_` / `sk-`) that surface "wrong provider — paste a different key here" before any network call. Cerebras input didn't — pasting a Groq key into the Cerebras field went all the way to a remote auth round-trip and came back with a confusing "Invalid Cerebras API key" instead of the obvious diagnostic. Added the `csk-` prefix check on both the client (`ui/app.js`) and the server (`app.py::validate_cerebras_key`) to match the other two providers.

### Changed
- **Wizard success messages now state only what was actually checked.** Previously the Groq success message said "Groq key is valid. **Free tier active.**" — but the validate endpoint just confirms the key works; tier info is never queried. Users on Groq's Developer tier (paid) would see a false claim. Changed to plain "Groq key is valid." Same fix for the Cerebras JS-side fallback message which claimed "Fastest tier enabled" — dead code in practice (server always returns its own message), but misleading if it ever fired.

## [3.14.39] - 2026-05-16

### Fixed
- **"and more" Whisper-layer hallucination stripped on short clips.** Complementary to v3.14.38, which addressed the same symptom at the styling-prompt layer (forbidding the LLM from generating filler-tails). This fix handles the upstream case: Whisper itself emits the literal phrase "and more." on near-silent <1 s audio, where local pass-through styling (no LLM call) never gets a chance to filter it. Evidence from one user's app.log on 14 May: 28 instances of `Done: and more.` with `styling (local): 0ms` — every one was a Whisper-layer hallucination, not a styler abridgement. Added "and more" + variants (`.`, `!`, `...`) to the exact-match `_WHISPER_HALLUCINATIONS` frozen set so bare outputs are discarded; added trailing-pattern regex `(?:and|with|plus)\s+(?:many|much|lots\s+)?more` so real content with a hallucinated `...and more` tail is stripped to just the content. Existing ≤2-word-remainder safeguard prevents discarding edge-case noise as real content. Also added a batch of short-filler hallucinations seen on noise-floor clips: `bye` / `goodbye` / `thank you` / `thanks` / `okay` / `yeah` / `uh` / `um` / `hmm` / `mhm`.

### Tests
- **5 regression cases added to `scripts/auto_test_corpus.py` (FT1–FT5)** guarding the v3.14.38 anti-filler-tail prompt rule. Each is a verbatim shape from the user's real-failure logs ("multi-feature request → 'and many more'" / "trailing-example file list → 'etc.'" / "long multi-clause sentence") or its negative-control complement (speaker LITERALLY said `and so on` / `etc` → rule must NOT strip it). Run with `python scripts/auto_test_corpus.py --filter FT`. Closes the regression-coverage gap: v3.14.38's prompt rule had no automated test.

### Docs
- **README + `.env.example` refresh.** Documentation had drifted 2+ months behind the v3.14 feature set. Now mentions: Cerebras (the primary styler since v3.14.0) and how to get a key for it; Esc as the universal cancel hotkey (v3.14.37); the in-app auto-updater; smart hallucination filtering; custom vocabulary. Fixed self-contradiction in Known Issues (doc said both "macOS signed and notarised" *and* "Gatekeeper will warn"). Fixed installer-filename pattern to match actual asset names (`Waffler-Setup-<version>.exe`, `Waffler-<version>-mac.dmg`). Added sign-up URLs for each provider so new users don't have to hunt.

## [3.14.38] - 2026-05-15

### Fixed
- **Styler no longer truncates long dictations with "and many more" filler.** User reported two transcripts where the model collapsed real content (a list of UI requests and a multi-feature suggestion) into the literal string "and many more" at the end — `I'd like it to and many more.` / `either that, and many more.`. Both outputs were grammatically broken because the filler was being spliced in where actual content should've gone. Root cause: the prompt's existing "NEVER drop whole sentences" rule didn't *explicitly* name the filler-tail phrases the model defaults to when it decides to abridge. Added an explicit hard-rule in `prompts/normal.txt` forbidding `"and many more"`, `"and so on"`, `"etc."`, `"etc etc"`, `"and other things"`, `"amongst others"`, `"to name a few"`, `"and the like"`, `"and similar"`, `"and others"` unless the speaker literally said those words. Mirrored the rule into the inline system message used by Groq, Cerebras, and OpenAI provider calls (higher-priority instruction-following slot). Two worked failure examples included so the model has a concrete pattern to recognise and avoid.

## [3.14.37] - 2026-05-15

### Added
- **Esc is now a global cancel hotkey for active recordings.** Previously the only way to discard a recording was clicking the small X on the floating overlay — fiddly and slow, especially in sticky mode. Now pressing **Esc** at any time during a push-to-talk hold or sticky recording immediately cancels: audio is dropped, no transcription fires (saves API quota on accidental hits), no paste, clipboard is cleared, sticky state is reset. Goes through the same handler as the overlay X-button click. Esc is a no-op when no recording is in flight, so it stays available for dialogs, vim, file pickers, etc.
- **Wizard step 2 now teaches Esc-to-cancel.** Added a fourth instruction tile alongside "Hold to record" / "Release to stop" / "Sticky mode" so users learn the shortcut up-front. Also updated the legacy sidebar hint string from `Hold to record • +Space = sticky mode` to `Hold to record • Space = sticky • Esc = cancel`.

### Wired through
- `SmartHotkeyListener` (macOS) and `WindowsHotkeyListener` both gain an optional `on_cancel` constructor parameter. The Mac event tap's existing Esc handler was repurposed from "sticky-mode-only escape hatch" to "universal cancel"; Windows' low-level keyboard hook + polling-fallback paths got new VK_ESCAPE branches that fire the cancel callback when state != IDLE. The pipeline wires `on_cancel=self._on_overlay_cancel` at both creation sites (initial start + hotkey-config restart). Wizard test-listeners (step 2 / step 4 / FnKeyMonitor) leave `on_cancel=None` so Esc behaves normally during onboarding.

## [3.14.36] - 2026-05-15

### Fixed
- **Fn-spam no longer stacks up "We couldn't hear you" toasts or burns transcription rate limits.** Pressing Fn 20 times in a row used to fire 20 transcription API calls (hitting Groq's 20-req/min cap almost instantly) and surface 20 toasts on screen. Root cause: the existing short-tap discard at `app.py:2403` was *dead code* — it checked `len(audio_bytes) < 44 + 9600` (i.e. < 0.3 s of bytes), but the recorder's 500 ms pre-roll buffer guarantees every recording has > 0.3 s of bytes regardless of how briefly Fn was pressed, so the check never fired. Replaced with a proper **time-based** check on `recording_duration` (press-to-release wall time): anything under 0.5 s is silently discarded — no transcription, no styling, no toast, no history entry. 0.5 s is comfortably below any deliberate dictation but well above typical Fn-mistap durations. The audio buffer is still drained so the next press starts clean.

## [3.14.35] - 2026-05-15

### Fixed
- **"Select mic" button on the "We couldn't hear you" toast actually does something now (macOS).** The toast button click was being silently swallowed: the handler called `subprocess.Popen(["start", "ms-settings:privacy-microphone"], shell=True)` which is *Windows-only* syntax — on macOS the `start` command doesn't exist, `Popen` failed, the broad `except` swallowed the exception, the toast hid, and the user saw nothing happen at all (then had to quit + relaunch). Replaced with a platform-agnostic action that brings the Waffler window to the front and navigates to the in-app Settings page where the device dropdown lives. Strictly more useful than the old Windows-only "Open OS Privacy panel" behaviour too: users can now see exactly which mic Waffler is using and switch to a different one without leaving the app.

## [3.14.34] - 2026-05-15

### Changed
- **Home-screen "Update available" banner now installs in-app instead of bouncing to the website.** The banner's `Download` button used to call `open_url('https://wafflerai.com/download/')`, sending the user out of the app to find the DMG / EXE themselves. It now opens the same in-app download-and-install modal that Settings → About already used: streams the installer with a progress bar, then runs `install_update_and_restart` on completion. The user never has to leave the app. The "Download in browser" fallback in the modal still works for cases where the in-app fetch fails.

## [3.14.33] - 2026-05-14

### Fixed
- **Fn hold-quiet timer — third attempt at fixing the OS-level Fn oscillation chatter (macOS).** v3.14.31's 40 ms leading-edge debounce caught hardware bounce but not the 60–250 ms OS-level oscillation seen on M-series Macs (Touch ID + Globe key both poke modifier state). v3.14.32 took the right approach — defer `on_release` via a `threading.Timer` that cancels if Fn re-asserts inside the hold window — but was reverted. The likely culprit there was a known `threading.Timer` race: `cancel()` only stops a timer that hasn't entered its callback, so an OS Fn=1 arriving while `_fire_delayed_release` is mid-execution would silently fire `on_release` anyway. v3.14.33 fixes that with a ticket-based invalidation pattern — every schedule/cancel bumps `_release_ticket`, and the timer callback captures its ticket at schedule time and aborts harmlessly if the ticket has moved on. `cancel()` is still called best-effort to skip the wait, but correctness no longer depends on it. Hold window dropped to 150 ms (audio post-roll already adds 150 ms of trailing capture, so a deliberate quick tap stays snappy). Tests in `tests/test_fn_handler_chatter.py` include a verbatim replay of the 18:07 chatter pattern (14 OS toggles → exactly 1 `on_press` + 1 `on_release`).

## [3.14.31] - 2026-05-14

### Fixed
- **Fn key debounce on macOS — kills phantom multi-recording fan-out.** Reproduction on v3.14.29 showed a single Fn tap producing three full `Recording started → Recording stopped` cycles in ~1 s (durations 0.17 s, 0.19 s, 1.36 s). Root cause: macOS / external keyboard occasionally emits multiple `flagsChanged` events for one physical press, with FN_FLAG flickering 1→0→1 in under 10 ms. The v3.14.13 single-CGEventTap consolidation correctly suppresses *intra-tap* duplicates but doesn't filter *OS-level* phantom events. Added a 40 ms debounce in `FnHandler` — state transitions inside that window are rejected with a `Fn press REJECTED (debounce: 3.2ms < 40ms)` log line. Real human Fn taps last 80–300 ms so the floor accepts every plausible input; phantom flicker is universally <5 ms so it gets caught.
- **Proper mic permission check via AVFoundation at startup (macOS).** The "bytes captured but RMS=0" signature in the 17:45 chaos log is *TCC-denied stream*: on macOS, an app without Microphone permission gets `sd.InputStream` opens that succeed silently and deliver zero-valued samples — no exception, no error, just permanent silence. The existing `PermissionsManager.check_microphone_permission()` was therefore lying (it returned GRANTED whenever the open succeeded). Added an `AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)` call at startup, logging the actual status (NotDetermined / Restricted / Denied / Authorized). When denied, a `[mic-tcc] WARNING: mic permission DENIED` line lands in `app.log` with the fix steps. Any future zero-RMS reproduction now self-explains via the log instead of needing a debug session to diagnose.

## [3.14.30] - 2026-05-14

### Added
- **Version stamp in the startup banner.** The `=== Waffler starting ===` line in `app.log` now includes the running `__version__` (e.g. `=== Waffler starting === (v3.14.30, PROJECT_ROOT=…)`). Was previously a recurring source of confusion when diagnosing Mac issues — figuring out which bundle a log was from required a separate `grep __version__` against `/Applications/Waffler.app/Contents/Frameworks/src/__init__.py`. Now it's one grep against the log itself.
- **Hotkey-path diagnostic logging (Mac).** `SmartHotkeyListener`, `MacEventTap`, and each handler (`FnHandler`, `GenericHotkeyHandler`) now route their lifecycle + key events through a new `src/log_util.py` helper into `app.log`, tagged with a unique per-instance id (`L01`, `tap01`, `Fn01`, `Gen02`…). The 16:22:17 dual-recording reproduction surfaced `Recording started` × 13 in 15s in `app.log`, but the `[HOTKEY] …` lines from `smart_hotkey.py` only went to stdout — so we couldn't tell whether it was one handler fanning out or multiple listeners alive at once. With the new logging the next repro will say e.g. `[HOTKEY/L01] _on_hotkey_press entered` 13 times (= one listener mis-firing) vs `[HOTKEY/L01] press fired` / `[HOTKEY/L02] press fired` interleaved (= two listeners alive). Pure observability — no behaviour change.

### Fixed
- **"Restart now" sometimes quit the app without relaunching it on macOS.** The `restart_app()` IPC called `/usr/bin/open -n /Applications/Waffler.app` while the old process was still running, then `os._exit(0)`'d 600 ms later. For signed/notarized `.app` bundles, Launch Services occasionally *collapsed* the `-n` request into a no-op "bring existing to front" when it saw an instance of the same bundle ID already alive — leaving the user with a dead app and no replacement. Switched to the Sparkle-updater pattern: spawn a detached `/bin/sh` that polls our PID, and only once we're gone calls `open -n` on the bundle. By the time the launch request is made, Launch Services has nothing to collapse, so it always honors it. Source-run path and the Windows re-exec path are unchanged.

### Changed
- **Restart prompt is now a centered modal popup instead of a top-of-page banner.** User reported the banner was easy to miss above the fold. New modal has a soft blurred backdrop, a centered card with the rotating 🔄 icon, primary "Restart now" + secondary "Later" buttons, full keyboard support (Enter triggers restart, Esc dismisses, primary button is autofocused), and click-outside-to-dismiss on the backdrop. Same `showRestartBanner()` callsite — only the rendering changed.

## [3.14.29] - 2026-05-14

### Changed
- **Wizard hotkey keycaps are now black-on-cream.** The big "Fn" / "Ctrl+Win" keycap in step 2 and the mini chips inside the "Hold to record" / "Sticky mode" instruction tiles were all white-on-cream — they faded into the wizard background. Switched both to a dark `#1A1612 → #2C2520` raised pill with `#FFFCF2` text so they read as real physical keys. Pressed state still flips to brand-gold so visual feedback during a real key press is unchanged.

## [3.14.28] - 2026-05-14

### Added
- **"Taking longer than usual" overlay toast.** If transcription takes >10 s or styling takes >15 s, a one-shot toast fires telling the user the provider may be slow and suggesting they add a fallback key in Settings → API Keys. Fires exactly once per dictation. The thresholds are tuned so they only trigger on genuine provider slowness — most dictations complete well under both.
- **"Restart now" banner after saving an API key.** When the user pastes a key into Settings (Groq / Cerebras / OpenAI) and hits Save, a sticky banner now appears at the top of the page with a clear "Restart now" CTA. The styler's client objects are constructed once at pipeline init and don't re-read keys live — so a fresh Cerebras key wasn't taking over fallback duties until the user manually quit and reopened. The new banner is loud, non-auto-dismissing, and clicking "Restart now" cleanly relaunches the app via a new `restart_app()` API endpoint (uses `open -n` on macOS to spawn a fresh `.app` instance; re-execs `sys.executable` on Windows).
- **Wizard reliability nudge in step 3.** Below the existing "Waffler tries them in order" intro, a soft amber-bordered tip card now reads: *"💡 Add more than one for reliability. If a provider hits a rate limit or has a slow day, Waffler will fall back to the next one automatically. You can always add more keys later from Settings."* Sets the right expectation without blocking advancement on a single key.

### Changed
- **Rate-limit toast wording shortened.** Was 3+ sentences (~140 chars body) which overflowed the toast and was hard to read in a flash. New version is one sentence: heading names the provider + when it resets ("Groq daily token limit hit · resets in about 3h"), body says "Pasted raw — Add a Cerebras key for fallback." Two ideas, fast to parse, still actionable.
- **In-app toasts now stay visible on hover.** Hovering over a toast pauses the auto-dismiss timer; the cursor leaving restarts a shorter 1.2 s timer. Clicking the toast dismisses it immediately. Stops users losing important messages mid-read while reaching for the mouse.

### Reverted
- **The v3.14.26 "Fn on Mac Mini" misfire**, dropped in v3.14.27.

## [3.14.27] - 2026-05-14

### Fixed
- **Wizard step 1 let users advance past macOS permissions without granting them.** The Next button on step 1 was `disabled = false` unconditionally in `wizUpdateNextButton()`. The polling enabled Next when both permissions were detected, but nothing kept it disabled in the meantime — and on initial render the button was already clickable. The visible symptom: user runs setup wizard, clicks Open System Settings, gets distracted before granting Input Monitoring, comes back, clicks Next anyway, lands on step 4 (Try It), holds the hotkey... and nothing fires. The hotkey listener can't function without Input Monitoring access. Tracked global state (`_wizardPermsAccessibility`, `_wizardPermsInputMon`), gated `case 1` on both being true, and added a belt-and-suspenders refusal inside `wizNext()` itself that shows a toast naming the missing permission(s) if the disabled-state was somehow bypassed.

### Reverted
- **v3.14.26 "Fn key on Mac Mini" changes.** That fix targeted what turned out to be a different bug — the user had simply not granted Input Monitoring (see Fixed above). Reverted the speculative keycode-63 (kVK_Function) detection in `FnHandler` and the "Fn not registering?" wizard help card so the codebase isn't carrying defensive logic for a problem that doesn't exist.

### Changed
- **Wizard buttons are now dark charcoal pills.** All cream-on-cream buttons (Open System Settings, Use a different hotkey, Back, Next) read as decorative on the cream wizard background; switched to solid `#1A1612` charcoal with `#FFFCF2` text so they pop as proper action buttons. The gold brand colour now appears as a hover glow ring instead of the base button colour. Finish-Setup button on the final step keeps its green for completion semantics.

## [3.14.26] - 2026-05-14

### Fixed
- **Fn key not detected on Mac Mini / Magic Keyboard setups.** Reported: "the Fn key won't work on my Mac Mini". Root cause is one of two things, both common on Mac Mini hardware:
  1. **macOS "Press 🌐 key to:" override.** System Settings → Keyboard → Modifier Keys lets you remap the Fn / Globe key to actions like "Change Input Source" or "Show Emoji & Symbols". When this is set to anything other than "Do Nothing", macOS intercepts the Fn key at a system level *before* our CGEventTap sees it — the `kCGEventFlagMaskSecondaryFn` flag never fires.
  2. **External Apple keyboards that send Fn as a raw keycode** (kVK_Function = 63) instead of a modifier flag. This used to surface as F13/F14/F15 (105/107/113) on older external keyboards, but on Magic Keyboard for Mac Mini it can be raw keycode 63 with no flag accompaniment.

  Two-part fix:
  - **Python `FnHandler` now catches keycode 63** in addition to the existing F13–F15 / F16 (`_EXT_FN_KEYCODES = {63, 105, 106, 107, 113}`). Fires the same press/release callbacks and suppresses the key event so the emoji picker / globe-key shortcut doesn't fire alongside.
  - **Wizard "Fn not registering?" help card** — fades in below the listening pill in step 2 after ~6 seconds if no key press has registered AND the configured hotkey is Fn AND the user is on macOS. Explains the Globe-key override issue plainly and offers two one-click actions: (a) "Open Keyboard settings →" which deep-links to `x-apple.systempreferences:com.apple.preference.keyboard`, or (b) "Choose a different hotkey →" which opens the existing alternative-hotkey picker. Hides automatically the moment a real Fn press is detected.

## [3.14.25] - 2026-05-14

### Changed
- **Wizard Step 3 — "Open the link" card is now a clickable target.** All three provider panels (Groq, Cerebras, OpenAI) had a card 1 illustration captioned "It opens straight to the API Keys page" — but the card was decorative; you had to find the small link in the help text below to actually open the page. The whole card 1 is now a tap target: cursor-pointer, hover lift, focus ring, and a "↗ Click to open" chip that fades in on hover. Clicking opens the provider's API-keys page via `pywebview.api.open_url` (or `window.open` as a fallback when previewing in a browser).
- **Wizard Step 3 — OpenAI now has the same animated three-step walkthrough as Groq and Cerebras.** Previously OpenAI was the only provider without the mock-browser walkthrough. Added the full structure with OpenAI's brand colours (dark theme, green `#10A37F` accent), `platform.openai.com/api-keys` URL bar, proper sidebar items (Playground / Assistants / Fine-tuning / **API Keys** / Usage), `+ Create new key` button matching their actual UI, and an `sk-proj-…` key prefix in the reveal modal.
- **Wizard Step 3 — added "Save this key somewhere safe" notice.** Every provider panel now has a gold-amber banner between the walkthrough and the paste input: 🔐 *"Save this key somewhere safe. Groq/Cerebras/OpenAI only shows it once — keep it in a password manager so you don't have to regenerate it later."*
- **Wizard Step 4 — "TRY SAYING" label is now a proper CTA chip.** Was a small 11px uppercase label; now it's a 14px gold-pill with a 🗣️ glyph, generous padding, soft drop-shadow, and a rounded-pill border. Stands out as an actionable prompt instead of decorative text.
- **Wizard Step 4 — send button now actually sends the dictated message.** Previously pressing the ▶ button just showed a "Pushed to app!" toast and cleared the input. Now the dictated text actually appears as a sent reply bubble (right-aligned, gold-gradient, mirror corner-radius — proper iMessage-style) inside the conversation thread with Alex. The user sees the whole loop: dictate → text in input → tap send → message lands in the chat. Reinforces what the app will do for real in their actual messaging apps once setup is done.

## [3.14.24] - 2026-05-14

### Fixed
- **Vocab "Add word" input still pre-filled for some users on upgrade.** v3.14.23 removed the legacy `loadVocab()` that was dumping every stored word into the input on startup, but some users still saw the pre-filled string after updating — turns out WebView's form-restoration cache was holding the value from the previous version's bad behaviour. Belt-and-suspenders fix:
  1. `loadVocabPage()` now unconditionally clears `#vocabInput.value` every time the page renders. Even if WebView, the browser cache, an extension, or future regression tries to put content there, it gets wiped the moment the user navigates to the Vocabulary tab.
  2. Added `autocomplete="off"`, `autocorrect="off"`, `autocapitalize="off"`, `spellcheck="false"`, and an explicit empty `value=""` on the input element so WebView's form-restoration can't repopulate it across launches.

## [3.14.23] - 2026-05-14

### Fixed
- **Vocabulary "Add word" input was pre-filled with every existing word jammed together.** A legacy `loadVocab()` function ran on every `pywebviewready` startup, joining all stored vocab words with `\n` and dumping the result into `#vocabInput`. The element used to be a multi-line `<textarea>` in an older UI, but the current UI uses a single-line `<input>` plus a separate list with delete buttons — so newlines collapsed and you'd see "WafflerAshkanGroqMLXkubernetesFargatepywebview…" stuffed into the box, forcing you to delete it before adding a new word. Removed the legacy `loadVocab()` / `onVocabSave()` functions and the startup call. Vocab list is loaded lazily by `loadVocabPage()` when you navigate to the tab; the input stays empty until you type in it.

## [3.14.22] - 2026-05-13

### Fixed
- **"Waffler" brand wordmark still didn't match the website.** v3.14.20 loaded Source Serif 4 from Google Fonts, but the actual mismatch was something subtler — the website's *header* logo uses `text-xl font-bold` with no font-family override, so it inherits Inter Variable from the body. I'd been setting the app's `.j-brand-text` to `var(--serif)` (Source Serif 4) thinking the website used serif there. Wrong font family entirely. Switched the app's brand wordmark to `Inter Variable` (bold 700, 20px, `letter-spacing: -0.01em`) so it now renders identically to the header. Journal entry body text + date dividers still use Source Serif 4 — that's where the editorial serif feel belongs.

### Changed
- **Status pill wording.** "Listening for [Fn]" was misleading — it sounded like the microphone was already hot. Changed to "Waiting for activation [Fn]" — calmer, matches the actual idle state. The Fn key chip stays so users still see exactly how to start.

## [3.14.21] - 2026-05-13

### Fixed
- **Top-nav Journal tab stayed highlighted after clicking another tab.** The HTML default put the active marker on `j-nav-active` (a sibling class to the showPage-toggled `.active`), so once the user clicked Vocabulary or Settings, BOTH "Journal" and the clicked tab looked active — confusing. Changed the default class to use `.active` (which `showPage()` actually toggles on/off), so the highlight tracks the current page correctly.

### Changed
- **Mode dropdown consolidated.** "Email — coming soon" + "Bullets — coming soon" replaced with a single disabled "More coming soon" entry. Cleaner, less repetitive — and when we ship Email / Bullets we can list them properly without retiring the placeholder line by line.

## [3.14.20] - 2026-05-13

### Fixed
- **"Waffler" brand wordmark and journal body text didn't match the website's typography.** The cream-theme CSS referenced Source Serif 4 (via `--serif`) for the brand wordmark, journal entry body text, and date dividers — but the font file wasn't actually loaded anywhere in the app, so the cascade silently fell through to Georgia. The result: subtle but visible "off-brand" feel compared to wafflerai.com. Added a Google Fonts `@import` at the top of `ui/style.css` that loads both **Source Serif 4** (serif) and **Inter** (UI sans) in the same weights the website uses. If the app is offline the cascade still falls back to Georgia / system fonts cleanly — but since the app needs internet for API calls anyway, the fonts will be there whenever the app is functional.

## [3.14.19] - 2026-05-13

### Fixed
- **Rate-limit toast was unhelpfully generic.** When Groq's daily token quota was exhausted and the user had no Cerebras / OpenAI fallback configured, the styler would fall through, try ``self.client.chat`` on a ``NoneType`` (because the OpenAI client wasn't set up), catch the resulting ``AttributeError``, and pass *that* meaningless error as the fallback reason. The toast then said: <em>"Cleanup skipped — Pasted raw. See the log for details."</em> — telling the user nothing about what actually happened or what to do.

  Two-part fix:
    1. **Styler now skips OpenAI entirely when ``self.client is None``** (instead of crashing on a NoneType call), tracks every provider's failure in an explicit ``failures`` list, and surfaces the most actionable reason — rate-limit messages beat auth/connection/other in priority. When a provider is in cooldown from a previous 429 but hasn't been called this dictation, a synthetic ``RATE_LIMIT|cooldown|<wait>s|<provider>`` reason is generated so the toast can still explain why.
    2. **Toast wording rewritten** to always be actionable. Every branch now tells the user (a) what happened, (b) what to do RIGHT NOW (add a specific fallback key in Settings → API Keys), and (c) the alternative (wait for the limit to reset, with the actual reset time). Example: <em>"Groq daily token limit hit. Pasted raw text — styling skipped because Groq's daily token limit resets in about 3h 24m. Add a Cerebras or OpenAI key in Settings → API Keys as a fallback, or wait for the limit to reset."</em>
  Headings also got more specific — ``"Cleanup skipped"`` is now ``"Groq daily token limit hit"`` / ``"Connection failed"`` / ``"Auth blocked"`` / ``"No styling provider"`` depending on what actually went wrong.

## [3.14.18] - 2026-05-13

### Fixed
- **Vocabulary / Settings pages had no way back.** The Journal top-nav was nested inside `<main id="mainArea">`. When the user clicked "Vocabulary" or "Settings", `showPage()` hid `mainArea` to reveal the relevant panel — which also hid the navigation, trapping the user on those pages with no way back to Journal. Moved the top-nav out of `mainArea` so it's a sibling of all three panels, persistently visible regardless of which page is active. Restructured `.app` as a column-flex container (top-nav on top, the active panel filling beneath) and overrode the old `height:100vh` on `.settings-panel` / `.vocabulary-panel` so they fill the remaining height instead of overflowing the viewport.

## [3.14.17] - 2026-05-13

### Fixed
- **Journal page entries didn't fill the window.** The Journal layout shipped in v3.14.16 had `max-width: 760px; margin: 0 auto` on the entry container, so the white cards looked orphaned in the middle of a wider window while the top-nav and stat strip stretched edge-to-edge. Removed the constraint on the page container — entries now stretch flush with the strip above. Body text inside each card is still capped at `75ch` (≈ classic reading width) so long paragraphs don't sprawl across an ultrawide monitor, but the card itself fills the available width.

## [3.14.16] - 2026-05-13

### Changed
- **Complete redesign — Journal layout.** Goodbye sidebar, hello editorial. The home view is now a writer's-notebook layout: top-nav across the window (Brand · Journal · Vocabulary · Settings · Listening-for-Fn pill on the right), a stat strip beneath it, then a serif "journal page" of white-card entries with italic-serif timestamps, gold-pill word-count chips, and Copy / Show original buttons.
- **🥞 Stack streak chip.** Right side of the stat strip — three little stacked waffles with a tiny syrup pat on top, "**N** stack streak" beside. Subtly wobbles every ~5s. Hidden entirely when the streak is 0 so first-time users don't see a "0 stack streak" eyesore. Powered by a new `streak_days` field in `get_stats()` that counts consecutive calendar days with at least one history entry; today's empty state preserves the streak so it doesn't snap to 0 at midnight — only breaks once a full day passes without dictating.
- **Mode selector** moved to a compact pill in the top nav (was bottom of sidebar). Mic selector moves to Settings (it's a one-time configuration, not a frequent toggle).
- **Listening indicator** is now a single amber pill with a "Listening for [Fn]" caption + animated dot; flips green when recording and amber-on-amber when processing.
- **Empty-state copy** updated: "Your journal is empty." + "Press your hotkey and speak — the first entry will appear here." 🥞

### Internal
- Existing `.transcript-card` / `.card-time` / `.card-words` / `.btn-copy` / `.text-toggle` class names preserved — restyled inside `.j-page` rather than renamed — so `app.js`'s `makeCard()` renderer keeps working without edits.
- Update-banner mount fallback added (looks for `.sidebar` first, then `.journal`) so the auto-update notification still appears in the new layout.

## [3.14.15] - 2026-05-13

### Fixed
- **Mac `SIGSEGV / EXC_BAD_ACCESS at 0x400` on wizard→pipeline handoff.** v3.14.14 fixed the CFFI closure lifetime *within* a single `AudioRecorder`, but the wizard runs its OWN `AudioRecorder` (`_wizard_recorder`) and the main `WafflerPipeline` then constructs a SECOND `AudioRecorder` immediately afterwards. The wizard cleanup path (`wizard_stop_hotkey_test`) was just doing `_wizard_recorder.stop()` (which only flips `is_recording=False` and snapshots the buffer — it does NOT close the stream) and then `_wizard_recorder = None` to drop the reference. CoreAudio's HAL I/O thread could fire one last callback into the freed wizard CFFI closure while the pipeline's brand-new `InputStream` was being constructed milliseconds later. Crash trace: `pythonify_c_value` → `method_stub` → `ffi_closure_SYSV` → `AdaptingInputOnlyProcess` → `HALC_ProxyIOContext::IOWorkLoop`, exactly as predicted.

  Two-part fix:
    1. **Process-wide `_STREAM_LOCK` in `audio.py`** serialises `sd.InputStream` creation and teardown across *all* `AudioRecorder` instances. `_create_stream()` and `_teardown_stream(stream)` both hold the lock for their full duration — so any concurrent stream creation transitively waits for the outgoing stream's HAL thread to fully drain. The lock window is ~100ms; invisible in normal use, exactly where it needs to be at instance-to-instance handoffs.
    2. **`wizard_stop_hotkey_test` now calls `_wizard_recorder.shutdown()`** (full stop → drain → close sequence, bounded by an internal 2s watchdog) BEFORE setting `_wizard_recorder = None`. Combined with the lock above, the pipeline's pre-warm `start_monitoring()` call cannot begin until the wizard's stream is fully closed.

  Added `tests/test_audio_stream_lifecycle.py` — a 50-iteration tight-loop stress test plus a 20-iteration overlapping-create/teardown loop that reproduces the exact wizard→pipeline race. Without the v3.14.15 fix this script reliably segfaults on Mac within a handful of iterations; with the fix it completes cleanly.

- **macOS overlay pill unreliable on Space changes.** Users reported "I activate it and switch to another window and the pill doesn't appear there — it eventually follows but it's flaky on some Macs." `NSWindowCollectionBehaviorCanJoinAllSpaces` is supposed to make the overlay show on whichever Space is currently active, but macOS silently "forgets" that flag intermittently, especially when the overlay's owning app isn't frontmost. Added a low-frequency re-assertion to the overlay's animation tick: every ~250ms (5 ticks at 50ms) we call `orderFrontRegardless()` while the pill is visible. CoreGraphics no-ops if the window is already topmost on the current Space, so the cost is negligible; the win is that the pill snaps onto the active Space the next tick after a switch.

## [3.14.14] - 2026-05-13

### Fixed
- **macOS `EXC_CRASH/SIGABRT` from PortAudio HAL callback (separate from the v3.14.13 segfault).** Different crash signature: `Py_FatalErrorFunc` → `abort()` on the `com.apple.audio.IOThread.client` thread, with the C trace going `convert_to_object` → `general_invoke_callback` → `ffi_closure_SYSV_inner` → `AdaptingInputOnlyProcess` → CoreAudio HAL. Trigger correlated with a Groq `RateLimitError` styling failure, but the rate-limit was incidental — the real bug was that sounddevice's CFFI closure could be invoked by CoreAudio's I/O thread for a brief window after `stream.stop()` had returned. If the `InputStream` Python object was GC'd (or the bound callback method was dropped) during that window, the next HAL callback fired into freed memory and CFFI hit a fatal Python state error.

  Rewrote `src/audio.py`'s stream lifecycle with a strict teardown sequence:
    1. `_callback_active = False` — Python-level guard so even a late HAL callback returns immediately.
    2. `stream.stop()` — stop dispatching new audio.
    3. `time.sleep(0.1)` — drain window for any in-flight HAL callback to complete.
    4. `stream.close()` — release C-level resources and the CFFI closure.
    5. Drop the Python reference last — bound method survives steps 2-4.

  Also cached the bound `_callback` method as a stable `self._callback_bound` attribute in `__init__` so PortAudio's CFFI closure always points at the same Python object for the recorder's lifetime — previously every `self._callback` access created a new bound-method object, which made GC behaviour around stream replacement non-deterministic. Switched `_stream_lock` to `RLock` so the new `_teardown_stream(stream)` helper can be called while the caller already holds the stream lock. `shutdown()` watchdog timeout bumped from 1.5s → 2.0s to cover the new drain window.

- **Cerebras "page does not exist" 404 on the wizard's "Get a key" link.** The link pointed at `cloud.cerebras.ai/keys` which returns a 404 from Cerebras's developer platform. Updated to the correct path `cloud.cerebras.ai/platform/api-keys` in the wizard, the settings panel description, and the dynamic settings hint — matching the URL already used in `wafflerai.com/api-key-guide`.

### Changed
- **Wizard Step 3 — Cerebras now has its own animated walkthrough.** Same three-card story as Groq (open → sign in → copy key) but with Cerebras's dark theme and orange-accent (`#FF6B00`) branding, the correct `cloud.cerebras.ai/platform/api-keys` URL in the mock browser titlebar, and a `csk-` key prefix in the reveal modal. Shares animation CSS with the Groq walkthrough so only one provider's cards animate visibly at a time (whichever tab is active).

## [3.14.13] - 2026-05-13

### Fixed
- **macOS segfault on recording end (multi-CGEventTap race).** Reported in `~/.waffler-hosted/crash.log` — `Fatal Python error: Segmentation fault` with `<no Python frame>` on the crashing C thread while two other threads were live inside `CFRunLoopRun()`. Root cause: `SmartHotkeyListener` was composing 2–3 independent HID-level `CGEventTap`s in one process (one for the hotkey via `FnKeyMonitor` or `MacHotkeyMonitor`, one for Space, one for Esc cancel). When the C callbacks for two taps fired near-simultaneously — for example "paste finishes + key release" at the end of a recording — PyObjC's bridge state raced between the two C threads and macOS sent SIGSEGV. The Esc-cancel monitor was added during the v3.14 series, which is exactly when the regression appeared (v3.12.3 only had one tap and didn't crash).

  Refactored to a **single** `CGEventTap` per process (`MacEventTap`) that dispatches incoming events to multiple lightweight handler objects on one `CFRunLoopRun()` thread:
    - `FnHandler` — Fn flag changes + external-keyboard Fn (F13/F14/F15), always suppresses Fn flag changes to prevent the emoji picker.
    - `SpaceHandler` — Space key, fires unconditionally so `SmartHotkeyListener` can decide whether it's a sticky toggle, suppresses Space when Fn is held (preserved Fn+Space sticky-toggle semantics).
    - `GenericHotkeyHandler` — arbitrary modifier+key combos with configurable suppression. Used for non-Fn hotkeys, the Space trigger for non-Fn hotkeys, and Esc cancel.

  All existing behaviour preserved: Fn push-to-talk, Fn+Space sticky toggle, Esc as universal sticky-cancel, Space as sticky trigger for non-Fn hotkeys, surgical modifier suppression so Cmd+C / Cmd+V keep working. `fn_key_cgevent.FnKeyMonitor` kept as a thin backward-compat shim (over the same single-tap architecture) so `app.py`'s startup permission probe and `tests/test_fn_key.py` still work without edits. Wizard polling surface preserved via property aliases (`_fn_pressed`, `_hotkey_active`, `is_combo_active`).

  Windows hotkey path untouched.

## [3.14.12] - 2026-05-13

### Changed
- **Wizard Step 3 — animated three-step Groq onboarding scenes.** Step 3 (API keys) used to be the hardest hurdle for users who'd never created an API key before. Added three illustrated story cards above the Groq input that walk through the actual flow with looping CSS animations:
  1. **Open the link** — mock Groq dashboard (dark theme, real "groq" wordmark in their orange `#F55036`, sidebar with "API Keys" highlighted) with an animated cursor that glides to the "+ Create API Key" button and clicks it; the button briefly depresses and a red ripple ring expands.
  2. **Sign in (free)** — modal with the Google G logo "Continue with Google" button, an email field that **types `you@example.com` character-by-character** with a blinking caret that moves with the text, a red "Sign in" button that briefly glows red, and a green check badge that pops in at the corner.
  3. **Copy + paste it here** — dark key-reveal modal with the "shown only once" warning, a `gsk_...` key with a **light shimmer sweep** across it, a green Copy button that briefly depresses, a "✓ Copied!" toast that slides up and fades, and a gold down-arrow that pulses toward the paste field below.

  Cards are staggered ~1s apart so the row reads left → right as one continuous demonstration, and a small gold dot travels along each connector arrow between cards to reinforce the direction. Cards stack vertically on narrow widths. Respects `prefers-reduced-motion`.

## [3.14.11] - 2026-05-13

### Fixed
- **macOS Accessibility pill never ticked green even after granting permission.** `check_accessibility_permission()` called PyObjC's `AXIsProcessTrusted()`, which has a long-standing issue under PyInstaller-bundled apps: once it returns `False` early in the process lifetime it tends to keep returning `False` for the rest of the run, even after the user toggles Accessibility ON in System Settings — the result appears to be cached inside the PyObjC interop layer. Input Monitoring was unaffected because it goes through `IOHIDCheckAccess` via raw ctypes. Rewrote the Accessibility check to use ctypes against `ApplicationServices.framework` directly: it now tries `AXIsProcessTrustedWithOptions(NULL)` first (the modern API explicitly designed for repeated polling), then falls back to `AXIsProcessTrusted()` via ctypes, and only uses the PyObjC binding as a last resort. Each call re-queries TCC live, so granting Accessibility in System Settings now ticks the wizard pill green within the next 1-second poll tick.

### Changed
- **Wizard Step 1 — proper Apple-style permission icons.** The Accessibility tile now shows the real macOS Accessibility glyph (blue gradient square with the white universal-access figure) and the Input Monitoring tile shows the dark slab with a detailed keyboard glyph, matching what users actually see in System Settings. Gold tint removed for these icons since they're now real system icons.
- **Wizard Step 1 — Accessibility now has its own animated walkthrough.** Previously only Input Monitoring had a mock-System-Settings animation showing how to add Waffler. Accessibility now has a parallel walkthrough labelled "Privacy & Security → Accessibility", offset ~3 seconds from the Input Monitoring one so the two windows feel alive independently.
- **Wizard animation apps swapped to recognisable real apps.** Replaced Karabiner-Elements / Terminal / AltTab with apps the average user actually has installed: Accessibility window shows Google Chrome, 1Password, then Waffler being added; Input Monitoring window shows Spotify, Discord, then Waffler being added. The application picker shows Zoom / Spotify / Waffler / WhatsApp / Discord with their proper brand-coloured SVG logos instead of emoji placeholders. The Waffler icon throughout uses the real bundled `logo-icon.png` instead of the 🧇 emoji.

## [3.14.10] - 2026-05-13

### Fixed
- **Post-setup crash, take 3 — different crash, same wizard.** v3.14.9 fixed the SSL access-violation by monkey-patching `httpx.create_ssl_context` and running `_initialize_pipeline()` synchronously on the IPC thread for defence-in-depth. The synchronous version turned out to introduce a DIFFERENT crash: the IPC call blocked for 2-3 seconds while OpenAI/Cerebras clients were constructed, and EdgeChromium WebView2's GUI thread crashed in C code during that block (confirmed via crash dump — no Python frame in the crashed thread, only `evaluate_js` threads stuck on `threading.acquire`). Reverted `complete_setup()` to spawn `_initialize_pipeline()` in a background thread again. The SSL monkey-patch from v3.14.9 keeps that path safe because every httpx client now reuses the same pre-built main-thread SSL context regardless of which thread constructs it. So: SSL crash gone (v3.14.9 fix), WebView2 crash gone (v3.14.10 fix), and the user gets a snappy wizard close instead of a 3-second freeze.
- **Wizard Step 1 hotkey detection on Windows.** `initFnKeyFeedback()` had `if (!document.getElementById('wizHotkeyBadge')) return;` at the top, left over from the pre-v3.14.5 wizard. After the wizard redesign, the Windows keycaps were renamed (Ctrl has no id, Win is `wizHotkeyBadgeWin`) so `wizHotkeyBadge` no longer existed → early return → `startFnKeyPolling()` never ran → `get_fn_key_state()` was never called even though the Python hook was happily firing PUSH_TO_TALK on every press (visible in `hotkey.log`). Removed the early return; polling now always starts when entering Step 2 of the wizard.
- **Wizard "Next" button hidden below the fold.** The wizard's Back/Next nav was a normal flow element and got pushed below the viewport on shorter windows. Made it `position: sticky; bottom: -4px;` with a cream gradient fade behind it so it's always pinned visible regardless of scroll position. Also tightened the hotkey-stage padding and instruction-tile heights to reduce total wizard height.

## [3.14.9] - 2026-05-13

### Fixed
- **Post-setup crash, take 2 — the real fix.** v3.14.5 added a main-thread `ssl.create_default_context()` pre-warm, on the theory that warming the SSL stack once would make later background-thread calls safe. That theory was wrong — `httpx` creates a fresh SSL context **per client**, so every `OpenAI(...)` constructor in a worker thread re-triggered the same crashing `ssl.create_default_context()` call. (Confirmed via the latest crash log: SSL context creation still showed up in the access-violation thread even though "SSL stack pre-warmed on main thread" appeared in `app.log`.) The real fix has two parts: (1) build ONE `ssl.SSLContext` on the main thread using `certifi.where()` as the cert source — this avoids the Windows cert-store call that's actually crashing — and (2) monkey-patch `httpx._config.create_ssl_context` so every httpx client reuses that same pre-built context regardless of which thread the client is constructed on. (3) As defence-in-depth, `complete_setup()` no longer spawns a fresh background thread for `_initialize_pipeline()`; it runs synchronously on the IPC thread instead, so we don't go three levels deep from main thread when constructing OpenAI clients.

## [3.14.8] - 2026-05-13

### Fixed
- **Waffle overlay finally appears during the wizard Try-It step.** The `wizard_start_hotkey_test()` path was explicitly setting `_wizard_overlay = None` with a comment about "preventing threading crashes" from an older codebase. The overlay actually runs as a separate subprocess (GIL-independent), so this defensive skip wasn't needed any more. Now it instantiates `RecordingOverlay()` and calls `prestart()` inside a try/except — if the spawn fails for any reason, recording still works but without the pill, so the wizard can't be bricked by an overlay glitch. This was the long-standing "overlay doesn't appear in Step 3" complaint.

### Changed
- **Step 1 hotkey instructions redesigned as three clean tiles.** Replaced the prose paragraph under the listening pill with three centred tiles: `Hold to record` (keys on top, title under, no description), `Release to stop` (no keycap chip at all, just the title centred and slightly larger), and `Sticky mode` (Space + hotkey on top, title, short caption: "Press Space to lock recording. Press the hotkey again to disable.").
- **Hotkey order swapped to `Ctrl + Win`** in the wizard (Windows) on Step 1, Step 4 try-it badge, and the mock chat placeholder — user preference.
- **Listening pill flips green** when keys are pressed (was previously only the body text changing). Keycaps also depress visually.
- **"TRY SAYING" label** in the Try-It step is now a bold gold-tinted pill with letter-spacing instead of small italic text.
- **The waffle in the Try-It explainer animates** with the same speech-wave cell-darkening as the homepage instead of being a static pattern.

## [3.14.7] - 2026-05-13

### Fixed
- **Auto-updater stuck at 0% on every platform (real root cause, finally).** Traced the bug by writing a standalone test script and running the updater module directly: `start_download()` was self-deadlocking on `_state_lock`. The function acquires the lock with `with _state_lock:` and then, while holding it, calls `_reset_state()` which *also* does `with _state_lock:`. `threading.Lock` is not reentrant, so the second acquire blocks forever on the same thread. The worker thread spawn at the end of `start_download()` was never reached, the request was never made, and `get_progress()` returned `total_bytes=0, downloaded=0` indefinitely — which the UI rendered as "0%". This has been the cause of every "stuck at 0%" report since v3.13.0. Both the `curl`-on-Mac path (v3.14.3) and the `requests`-on-Windows path (original) worked perfectly when invoked directly; they just never ran. Fixed by switching `_state_lock` to `threading.RLock` (reentrant). Verified end-to-end: 32 MB v3.14.6 installer downloads in ~2.5 seconds on Windows.

### Added
- **Usage section breakdown by provider.** Settings → Usage now shows four time buckets (Today / This Week / This Month / All Time) instead of just two, and a per-provider breakdown card listing Groq / Cerebras / OpenAI side-by-side with their accent colour, count of API calls, total cost, and a horizontal bar showing each one's share of overall spend. New `by_provider` field in `get_usage_stats()` aggregates the existing per-entry `provider` field that was already being recorded on every call but never surfaced anywhere.

## [3.14.6] - 2026-05-13

### Fixed
- **Email mode no longer selectable.** The sidebar Mode dropdown still listed Email as a clickable option even after we'd reframed it as "coming soon" on the website. Reverted to one active mode (Normal) plus two disabled "coming soon" placeholders (Email, Bullets). `get_modes()` IPC now returns only the active set, and `get_current_mode()` falls back to "normal" if a previously-persisted Email selection is found, so old installs migrate cleanly.
- **Fn preset shown on Windows.** The wizard's "Use a different hotkey" config panel rendered the Mac preset list (Fn / Cmd+Shift / Option+Shift) regardless of platform. Windows users could "select" a Fn key that doesn't exist on their hardware. Now `showWizardHotkeyConfig()` renders Mac-only presets on Mac and Windows-only presets (Win+Ctrl / Ctrl+Shift / Ctrl+Alt+Space) on Windows.

## [3.14.5] - 2026-05-13

### Fixed
- **Wizard hotkey step never auto-advanced on Windows.** `get_fn_key_state()` was looking for `_wizard_step2_monitor._monitor._fn_pressed`, but on Windows the wizard monitor *is* a `WindowsHotkeyListener` which exposes `is_combo_active` as a property directly — there's no inner `_monitor` wrapper. So on Windows every poll returned `pressed=False` no matter what the user pressed, and the "press your hotkey" step could only progress if the user noticed the Next button was already enabled (the manual `case 2: btn.disabled = false` override). Now uses `is_combo_active` on Windows, falls back to the inner-monitor lookup on macOS. The step now visibly registers the press and auto-advances after 1s.
- **App crashed on the very first dictation after finishing setup.** The OpenAI Python client (via httpx) calls `ssl.create_default_context()` the first time it constructs a client. When that first call happens on a background thread in a PyInstaller-bundled app on Windows, it can segfault the whole process — and the pipeline is initialized from `_initialize_pipeline()` running in a thread spawned by `complete_setup()`. Fixed by pre-warming the SSL stack with a no-op `ssl.create_default_context()` on the main thread at app startup, before any worker threads exist.

### Added
- **Waffle overlay explainer in the Try-It step.** The floating waffle pill that appears on screen during dictation was previously a mystery to first-time users — they'd press the hotkey, see a small gold square pop up somewhere, and not know what it was. The Try-It step now includes a labelled preview: a bobbing waffle with a pulse ring and a short caption ("A floating pill shows on screen while you're recording. The squares darken with the volume of your voice…"). Sits below the "Waiting for you to dictate" status so it's visible before they press the hotkey.

## [3.14.4] - 2026-05-13

### Fixed
- **Windows wizard was rendering the (Mac-only) permission step.** v3.14.3 introduced a CSS rule `#wizContent1 { display: grid !important }` to lay the Accessibility + Input Monitoring cards out side-by-side on wide screens. The `!important` overrode the JS-applied `style="display:none"`, so on Windows the permission cards leaked onto Step 2 of 3 (the hotkey step) and then onto Step 3 (API keys). Scoped the rule to `body[data-wiz-step="1"]` so it can only apply when Step 1 is genuinely active — and on Windows the wizard never reaches Step 1, so the rule never fires.
- **Wizard now fills the full app window.** Container max-width bumped from a fixed 1080px to `min(1400px, calc(100vw - 64px))` (1500px on the wide Try-It step). On a typical desktop window the wizard now occupies the whole canvas instead of floating as a small modal in a sea of cream.

### Added
- **SaaS-style polish on the wizard:** smooth fade + slide transition between steps (no more hard cuts), an idle "shine" gradient sweep across active progress segments, hover-lift on provider tabs, and a continuous ripple ring behind the "Listening for hotkey…" pill so it visibly listens instead of just sitting still. A subtle warm radial gradient at the bottom of each step adds visual weight without being noisy.
- **Inline "Need help?" guide link on the API-keys step** that opens `wafflerai.com/api-key-guide` in the user's default browser. Makes the recommended Groq → Cerebras → OpenAI ordering self-serve to learn about, without leaving the wizard hanging if a user has never used any of the three.

## [3.14.3] - 2026-05-13

### Added
- **Cream theme for the main app.** Replaces the previous black-on-grey dark UI with the same cream/gold/warm-dark palette as the new wizard and the website. New installs default to Cream so the wizard-to-app handoff feels seamless. **Settings → Appearance** now has a three-option picker: Cream (default) / Dark (the original look) / System (follows the OS via `prefers-color-scheme`). Choice persisted to `localStorage` so it survives restarts. The entire app uses CSS variables, so the theme is swapped instantly without a reload — sidebar, history list, settings, vocabulary, hotkey badges, even the toast all repaint correctly.
- **Bigger wizard window.** Wizard container max-width bumped from 800px to 1080px (and 1200px for the wide Try-It step) so the Accessibility and Input Monitoring permission cards (with the embedded animated walkthrough) fit on a single screen without scrolling. On screens ≥980px wide, Step 1 now lays the two permission cards out side-by-side as a 2-column grid instead of stacking them.

### Fixed
- **Mac auto-updater stuck at 0% — for real this time.** v3.14.1 redirected Mac users to the website as a workaround because the PyInstaller-bundled `requests`/SSL stack on macOS has a long-standing issue where the HTTPS connection establishes and `content-length` arrives but body chunks never reach `iter_content()`. The new `src/updater.py` shells out to `/usr/bin/curl` on Mac instead — curl is shipped with macOS, uses the system's SSL trust store, and works on signed-redirect URLs from GitHub Releases. Progress is reported by polling the partial file size every 400ms. The in-app **Download & Install** button is now back on Mac (no more "Open Download Page" workaround); the install + relaunch still uses the existing `hdiutil` + `cp -R` shell script.

## [3.14.2] - 2026-05-12

### Changed
- **Setup wizard fully redesigned** to match the website's branded look. The previous dark-grey/black wizard with stacked permission cards, scrollable triple-stacked API key fields and an under-stated mock chat has been replaced with a cream-background, gold-accented flow:
  - Header now has the real Waffler logo + brand mark on the left and a discrete step-counter pill on the right.
  - Progress bar uses a gold gradient on active segments instead of flat green.
  - Step 1 (Permissions) uses two compact white cards with status pills that auto-tick green via background polling. The Input Monitoring card embeds an always-visible animated mini-screencast showing exactly how to click `+` → pick Waffler → click Open → flip the toggle, with proper Karabiner-Elements + Terminal icons in the list.
  - Step 2 (Hotkey) shows a single floating plastic keycap (white/silver with black `fn` label on Mac, `Win + Ctrl` combo on Windows) that gently bobs up and down. A gold "Listening for hotkey press…" pill replaces the previous static grey hint.
  - Step 3 (API Keys) replaces three permanently-visible stacked input fields with three pill-style provider tabs (Groq · Cerebras · OpenAI). Only one input is on screen at a time, ticked green automatically on valid paste. Groq is the recommended default to match the website's chain order. Step height shrunk by ~60%.
  - Step 4 (Try It) gets a polished cream card with mini keycap hint, a gold dashed "Try saying" suggestion, and a fully redesigned mock Messages app with proper traffic-light dots and rounded message bubble. The Finish-Setup button is now green so the win at the end feels like a win.

### Fixed
- **Input Monitoring permission detection is now reliable.** The previous `CGEventTapCreate`-based check returned non-null even when Input Monitoring was denied (as long as Accessibility was granted), which meant the wizard always reported "granted" once Accessibility was on. Replaced with Apple's canonical `IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)` via `ctypes`. Returns 0 (granted) / 1 (denied) / 2 (unknown), and we treat only 0 as granted. The wizard now polls this every second while step 1 is open and the status pills flip green the moment macOS reports the permission as granted — no need to click "Recheck".

## [3.14.1] - 2026-05-12

### Fixed
- **Mac auto-update stuck at 0%.** The in-app download worker on macOS would establish the HTTPS connection, read the `content-length` header, then never receive body chunks — leaving the progress bar frozen at 0% indefinitely. Root cause is a PyInstaller-bundled `requests`/`urllib3` quirk on macOS that mishandles streamed response chunks from GitHub's signed-redirect CDN. Rather than fight platform-specific bundling bugs, the Mac flow now opens the website's `/download/` page directly when an update is detected — the standard "drag DMG into Applications" workflow that's been working reliably since v3.13.0. Windows keeps the in-app silent install via Inno Setup, which has no such bug.
- **"Download in browser" now opens the website, not the GitHub release page.** Previously the fallback button (and the sidebar update banner) sent users to a raw `github.com/.../releases/tag/v3.X.Y` page with terse asset names. They now land on `https://wafflerai.com/download/` which has the proper download UI, system requirements, and OS detection — the surface we control.

## [3.14.0] - 2026-05-12

### Changed
- **Cerebras default model upgraded to Qwen-3 235B.** Was `llama-3.1-8b` (too small to follow nuanced styling rules — collapsed long exploratory speech and hallucinated greetings like "Hi, I have power toys installed" from rambling input). Probed the user's key and found Llama 3.3 70B is no longer available on Cerebras (404 across all name variants); Qwen-3 235B is the smartest accessible model (3.4× the parameter count of Groq's 70B Llama, comparable instruction-following quality). Sub-second styling on long inputs at paid tier. `CEREBRAS_MODEL` env var still overrides.
- **Provider chain re-ordered: Groq → Cerebras → OpenAI.** Was Cerebras → Groq → OpenAI. New order spends Groq's 100K-token-per-day free tier first before any paid Cerebras tokens, then falls through to Cerebras Qwen-3 235B (paid, dedicated queue), then OpenAI gpt-4.1-mini as last-resort safety net. Most light/moderate users now pay £0/month for styling; heavy users still pay a fraction of subscription voice-to-text apps.
- **Removed the "≥200 words → gpt-4.1 (full)" routing.** The original rationale (Cerebras `llama-3.1-8b` couldn't handle long inputs reliably) no longer applies now Cerebras runs Qwen-3 235B. Benchmarked Qwen-3 235B vs gpt-4.1 full on 5 real-world long transcripts (378-732 words): quality essentially equivalent, Cerebras 6.4× faster overall (11.8s total vs 74.8s total, 14.9× faster on the fastest test). OpenAI fallback now always uses gpt-4.1-mini, dramatically cutting the rare-OpenAI-fallback cost.

### Added
- **Em-dash and en-dash stripping** as the new top-of-pipeline post-processor in `style_openai.py`. Big LLMs (Qwen-235B especially) love em-dashes (—) and en-dashes (–); they're the loudest "AI wrote this" tell in styled output. Two-layer defence: (1) hard rule added to `prompts/normal.txt` and `prompts/email.txt` forbidding em/en-dashes with worked examples, (2) `_strip_em_dashes()` regex post-processor that replaces `\s*[—–]\s*` with `, ` regardless of what the model emits. Hyphens (-) inside compound words (`voice-to-text`, `gpt-4.1-mini`, `Ctrl+Alt+S`) are explicitly preserved.

### Fixed
- **"Exploratory speech collapses into a single conclusion" bug.** Cerebras Llama 3.1 8B on multi-clause thinking-out-loud transcripts ("let's say I do X… I just want to Y… is it already Z…") would drop 95% of content and add a hallucinated "Hi" greeting. Two fixes: (a) the model upgrade to Qwen-3 235B (bigger model holds the line), (b) a new HARD RULE in `prompts/normal.txt` explicitly forbidding the over-aggressive "abandoned restarts" interpretation, with the user's actual failing transcript captured verbatim as a NEGATIVE worked example. Tighter abandoned-restart definition: requires audible mid-word abort, not topic-meandering.
- **Cerebras `queue_exceeded` 429 distinguished from RPM 429.** Free-tier Cerebras users would see "high traffic" capacity-shed and the styler would mark Cerebras as failed for a full 5 minutes, breaking the speed advantage for half the day. Now both 429 shapes are recognised and given short 20-second cooldowns so the next dictation gets a fresh attempt.

### Added
- **Cerebras as primary styling provider.** New three-tier fallback chain: Cerebras Llama → Groq Llama → OpenAI gpt-4.1-mini. Cerebras hosts Llama models on wafer-scale chips at ~3000+ tokens/sec output — significantly faster than Groq. Free tier ~1M tokens/day. Defaults to `llama-3.1-8b` (free tier); `CEREBRAS_MODEL` env var lets users upgrade to `llama-3.3-70b` if they're on a paid tier. Each provider has its own monotonic-clock skip-until deadline that pauses further attempts after a 429 — Cerebras's "high traffic" 429 gets a short 20s cooldown so the next dictation retries, while genuine quota hits get 2 minutes. Wired into the setup wizard step 3 as the recommended primary, and into the Settings panel API Keys section as the top row. `validate_cerebras_key()` IPC method handles the awkward case where Cerebras rate-limits the validation itself (saves the key provisionally so setup isn't blocked).
- **Visible progress feedback on the recording pill.** During the slow post-recording stages (transcribing and styling), the pill now shows `"Transcribing… 1s"` and `"Styling… 5s 10s…"` with a ticking elapsed counter. Eliminates the "is it frozen?" feeling on long dictations. The label clears automatically when a new recording starts so VU bars take over again.
- **Auto-routing of OpenAI model by input length.** Inputs ≥ 200 words go to `gpt-4.1` (full), which generates output faster per token. Shorter inputs stay on `gpt-4.1-mini` because mini is already fast enough and meaningfully cheaper. `OPENAI_STYLE_MODEL` env var still pins exact models for power users.
- **500ms audio pre-roll buffer.** The audio stream is now pre-warmed at pipeline init and kept alive across recordings. A continuous ring buffer captures the last 500ms of audio, spliced into every new recording. Result: first 1-2 syllables of speech are no longer clipped on every dictation (was a 50-300ms stream-creation latency on Windows).
- **150ms audio post-roll buffer.** When the user releases the hotkey, recording keeps capturing for another 150ms so the final word / syllable isn't clipped sitting in the OS audio buffer.
- **Dual-key setup wizard.** Step 3 of the wizard now shows all three provider key fields together (Cerebras + Groq + OpenAI) instead of forcing a single choice. Users can fill in any combination during initial setup. Any one validated key unlocks the Next button.
- **`OPENAI_STYLE_MODEL` and `CEREBRAS_MODEL` env vars** for advanced users who want to override the auto-routed model selection.
- **Self-correction force-LLM markers in `_is_simple()`** — short transcripts that contain self-correction markers (`"sorry I mean"`, `", no <word>"`, `"actually"`, `"I meant"`, `"hmm no"`, `"scratch that"`, `"what I'm trying to say"`, etc.) now bypass the regex-only `_basic_clean` cleaner and go to the LLM, which can actually drop the wrong half. Previously short corrections like `"Tuesday, sorry I mean Monday"` would keep both versions in the output. The corpus now covers 24 self-correction scenarios across day-of-week, name, number, place, multi-step chains, and structural (numbered list / email body / question) contexts.
- **Profanity restoration safety net.** OpenAI's gpt-4o-mini and gpt-4.1-mini have safety training that sometimes drops swear words even when the prompt explicitly forbids censoring. New `_restore_censored_profanity()` runs after every styling call: if a swear word appears in the raw transcript but not in the styled output, it gets spliced back at the correct position by anchoring on the word immediately before the swear in raw (tracking which occurrence of that word, in case it appears multiple times). Covers the common UK/US swear lexicon.
- **Greeting auto-line-break for email-style input.** When the transcript starts with `Hi <Name>,` / `Hello team,` / `Hey Rohan,` / `Dear Sam,` / `Morning all,`, Normal mode now puts the greeting on its own line followed by a blank line before the body. Mirrors the existing sign-off split. False-positive guards in place for meta-language ("Hi guys would never work as an opener").
- **Sign-off auto-split.** `Cheers, James` / `Regards, James` / `Thanks again, James` etc at end of transcript split into two lines (sign-off line + name line) for proper email shape. False-positive guard for mid-body sign-off words.

### Fixed
- **OpenAI styler was using `gpt-4o-mini` despite the upgrade to `gpt-4.1-mini`.** The OpenAIStyler constructor default was changed correctly, but the caller in `app.py` was passing `model="gpt-4o-mini"` explicitly, silently overriding every model upgrade. Removed the explicit override so the constructor default takes effect.
- **`max_tokens=512` truncation on long dictations.** The styler was hardcoded to 512 output tokens (~380 words) regardless of input length, silently truncating long dictations mid-sentence. Output budget is now sized against input length (`max(1024, min(8192, input_words * 3))`).
- **Word-level stutters in short transcripts.** `_basic_clean` now collapses literal token-repeats like "I I", "the the", "we we" before returning. Punctuation between repeats blocks the collapse on purpose ("I, I think" might be a deliberate restart).
- **Solo `"number three"` was being converted to `"1."`** when no second item was dictated. The numbered-list rule now requires at least two explicitly counted items in the input before firing. Solo count phrases (references like "step five is wrong" or "Number one priority is X") stay as prose.
- **Vocab case-correction silently no-op'd.** When Whisper transcribed a vocab word in lowercase (e.g. `cobie` instead of `COBie`), the fuzzy-matcher recognised the exact case-insensitive match but emitted no correction — the lowercase form slipped through. Fixed to emit a `(token → canonical)` correction whenever the input case differs from the vocab entry's canonical case.
- **Numbers, times, dates, units, currency, versions and acronyms** are now strictly preserved (`3pm` stays `3pm`, not `3 PM`; `230ms` stays joined; `12th May 2026` doesn't get its month lowercased). The FORMATTING rule now spells out concrete preservation examples for each form.
- **Email mode dropdown choice persists** across app restarts (was in-memory only).

### Performance
- ~35% faster OpenAI styling on short clips via gpt-4o-mini → gpt-4.1-mini swap
- Order-of-magnitude faster styling on the Cerebras-served path when capacity is available

## [3.12.7] - 2026-05-10

### Fixed
- **Vocab case-correction was silently broken.** When Whisper transcribed a vocab word in non-canonical case (e.g. `cobie` instead of `COBie`, `ashkan` instead of `Ashkan`), the fuzzy-match function recognised it as an exact case-insensitive match but then did *nothing* — no correction was emitted, so the lowercase form was preserved in the styled output. Root cause: the `if word in vocab_lower: matched_tokens.add(word); continue` branch added the token to the matched set but never appended a `(word → canonical)` correction. Fix: when the case differs from canonical, emit a correction. Tokens already in canonical case (e.g. `Ashkan` exactly as stored) still skip without an unnecessary correction. This is what the user has been hitting when they said "the vocab doesn't really work" — Whisper biasing handled most of it, but any time it produced a lowercased form, the correction layer didn't fix the case.

### Added
- **Dedicated vocab regression harness** at `scripts/test_vocab_corpus.py` — 25 cases covering: exact match preservation, lowercase case-correction, single-char typos, bigram-collapse for Whisper splits (`Nash can` → `Ashkan`), unrelated common words that must NOT be corrupted (`cost`, `high`, `ash`, `cobblestone`, `cobalt`, real names like `Coby Persin`), bigram false-positive guards (`has can`, `task on`), multi-vocab in one sentence, and vocab inside lists / email greetings. Pure local execution — no API calls — so runs in milliseconds and locks the correction layer in for any future change.

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
