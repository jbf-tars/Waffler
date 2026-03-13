# Waffler Competitor Research
*Researched: 2026-02-17 | Subagent deep dive*

---

## Executive Summary

The voice dictation market has exploded in 2024-2025. The space has bifurcated into two camps:
1. **"Mystery Box" / Cloud-first**: Wispr Flow, Willow — simple UX, cloud processing, AI cleanup happens automatically
2. **"Transparent & Empowering" / Local-first**: Superwhisper, VoiceInk, MacWhisper, Spokenly — local models, user controls prompts/models, BYOK

Waffler currently sits in category 2 but without the configurability or polish of Superwhisper. The primary threat is Wispr Flow dominating category 1 with a massive funding advantage ($56M raised).

**Key gap**: Waffler is missing custom vocabulary, snippets, context-awareness from selected text/clipboard, pause/resume, and a command mode for editing text by voice. These are table-stakes features in 2025.

---

## Competitor 1: Wispr Flow (wisprflow.ai)

> *Note: The request specified "Whispr Flow" at whisprflow.app — this domain resolves to nothing. The actual primary competitor is **Wispr Flow** at wisprflow.ai. Both names appear in user discussions; they're the same product.*

### Technical Architecture

- **Processing**: 100% cloud-based. Audio sent to Wispr's servers, processed via proprietary STT (believed to use Whisper under the hood) + OpenAI/Meta LLMs for cleanup
- **Pipeline**: Hold hotkey → audio captured → cloud STT → LLM cleanup → inserted into active field
- **AI Cleanup**: Uses LLM post-processing with context awareness (what app you're in, surrounding text). Evidence is prompt-based rather than fine-tuned model — they describe it as "adaptive formatting"
- **Personalisation**: Per-user dictionary stored server-side, fed as vocabulary hints. No evidence of fine-tuning transcription model per user
- **Latency**: Sub-second claimed; real-world testing shows 1-2 seconds after you stop speaking
- **Context Awareness**: Reads app name to adjust tone (formal in email, casual in iMessage). **Does not read screen content via accessibility APIs** — works at the OS input injection level
- **Context Correction**: Smart enough to handle self-corrections: "Let's meet at 2pm, no wait, 4pm" → outputs only "4pm"

### Hotkey Design

- Default: `fn` key (hold to talk, release to transcribe) 
- Also supports double-tap fn for "long talk mode"
- Custom hotkey configurable (e.g., Ctrl+Option or Ctrl+Windows)
- **Push-to-talk** (hold) AND **toggle** modes available

### Overlay/Recording UI

- Bottom of screen: animated waveform/recording indicator
- Minimal — just a small pill showing recording state
- Disappears immediately after transcription
- No mode selector visible during recording

### Features

- **Whisper Mode**: Low-volume dictation for quiet environments
- **Command Mode** (Pro): Select text + speak commands like "Make this more formal", "Summarise this", "Turn into bullets"
- **Personal Dictionary**: Custom words, names, jargon — synced across devices
- **Snippets**: Voice shortcuts that expand to full phrases (say "my email" → inserts email address)
- **Flow Notes**: Voice capture to sync-able notes across devices
- **100+ languages**: German, French, Spanish etc. now at English-quality parity
- **Multi-device sync**: Mac + Windows + iOS with shared dictionary

### Pricing

| Plan | Price | Notes |
|------|-------|-------|
| Free | $0 | 2,000 words/week limit |
| Pro | $15/month (or ~$12/mo annual, $144/yr) | Unlimited dictation, Command Mode |
| Pro Student | $7.49/month | With verification |
| Teams | $10-12/user/month | Shared dictionary, admin controls |
| Enterprise | Custom | SOC2 Type II, HIPAA, SSO |

- 14-day free Pro trial on signup
- **Distribution**: Direct download (not Mac App Store for desktop). iOS via App Store.
- iOS app: 4.8/5 stars, 5.1K ratings

### User Complaints (direct quotes from Reddit/reviews)

- *"The biggest problem is that it often freezes, and when it does, it can also freeze whatever app I'm working in."* — Reddit, Wispr Flow Windows
- *"Mic not working, support unresponsive"* — r/WisprFlow
- *"800MB of RAM usage even during idle periods"* — multiple sources
- *"Slow startup times, taking 8-10 seconds to initialize"* — willowvoice.com review
- *"6 minute time limit per transcription"* — zackproser.com review
- *"Occasionally (not often) requires retries"*
- *"Cloud-only processing means [it] cannot function without internet"*
- Privacy concerns: data processed via OpenAI/Meta servers; vague privacy policy
- *"The app automatically adding itself to startup processes without clear user consent"*

### What They're Good At
- Extremely simple onboarding (works within 60 seconds of install)
- Most polished UI in the space
- Best cross-platform (Mac + Windows + iOS)
- Voice self-correction handling is genuinely impressive
- Accessibility focus (users with Parkinson's, RSI cite it as transformative)

---

## Competitor 2: Superwhisper (superwhisper.com)

### Technical Architecture

- **Processing**: Local-first with optional cloud. Runs Whisper models locally on macOS + Nvidia Parakeet (newer, faster)
- **STT Models available**:
  - Local: Whisper Tiny/Base/Small/Large/Large-V3 Turbo, Parakeet (NVIDIA, very fast, real-time)
  - Cloud: OpenAI, Anthropic, Deepgram, Groq, Gemini, Grok
- **AI Cleanup**: Custom prompts per mode (user-defined system prompt). This is explicitly prompt-based — users write their own instructions. Evidence: full custom prompt editor in UI, user community shares prompts
- **BYOK**: Yes — bring your own OpenAI/Anthropic/Groq/etc API keys (Pro plan)
- **Context Awareness** (most sophisticated in market):
  - **Selected Text Context**: Captured via accessibility API when recording starts
  - **Clipboard Context**: Captured within 3 seconds before/during recording  
  - **Application Context**: Active input field full text (even scrolled text beyond visible) — captured AFTER transcription, between STT and LLM processing steps
  - Super Mode: Has all three context types enabled
- **Pipeline**: Hotkey → audio → local Whisper/Parakeet STT → [context captured] → LLM with custom prompt → paste to active field
- **Speaker Diarization**: Yes, major improvements in v2.6.0 (multi-speaker recordings)
- **File Transcription**: Upload audio/video files for batch processing

### Hotkey Design

- Default: `⌥ + Space` (configurable)
- Mouse shortcuts: scroll wheel click, mouse button
- Deep link integration for shell command triggers
- Mode selector: keyboard shortcuts to switch modes (just tap number key)
- "Activate when using" — mode auto-switches based on active app

### Overlay/Recording UI

- **Recording window**: Floating window, resizable, waveforms
- Classic design: horizontal waveform animation during recording
- Shows current mode name
- Context indicators (lights up when clipboard/selected text captured)
- Stats: words per minute typing test, usage stats (v2.7.0)
- Mini mode selector (compact design option)

### Changelog Highlights (recent features showing roadmap)

- v2.9.0 (Jan 2026): **Parakeet Realtime** (true real-time offline transcription), Gemini/Grok cloud models
- v2.8.0 (Jan 2026): New history UI with full-text search, segmented playback, model library browser
- v2.7.0 (Nov 2025): Stats/WPM test, push-to-talk style selector, GPT 5.1 support
- v2.6.0 (Oct 2025): Selected text context in all apps, Claude Haiku 4.5, speaker separation
- v2.5.x: Parakeet vocabulary support

### Pricing

| Plan | Price |
|------|-------|
| Free | Unlimited small AI models, 15 min Pro feature trial |
| Pro | $84.99/year OR $249 lifetime |
| Student discount | 40% off |
| Enterprise | Custom, SOC2 Type II |

- **Distribution**: Direct download (DMG from superwhisper.com). Also on Setapp (but reportedly different/limited version).
- iOS app on App Store

### User Complaints

- *"Superwhisper has some interesting features, in terms of the on-the-fly processing that it's capable of, but I never got it to perform reliably enough"*
- *"I think the only thing that's missing (for someone who uses transcription multiple times a day) is the absence of a pause button"* — Reddit
- *"Long transcripts need hands-on editing, the setup is intimidating for casual users"*
- *"An hour-long dictation turns into another 10 minutes of manual cleanup"* — getvoibe.com
- *"older devices may be slower or lack support for advanced models"*
- Complex configuration overwhelms non-power users
- Expensive for lifetime ($249) vs competitors

### What They're Good At

- Most customisable prompting system — power users love it
- Local privacy-first (nothing leaves device unless using cloud models)
- BYOK flexibility
- Context awareness is genuinely best-in-class (accessibility API access)
- Continuous active development (active changelog)
- The go-to for developers who want to control AI coding agents via voice

---

## Competitor 3: MacWhisper (goodsnooze.gumroad.com/l/macwhisper)

*By indie developer Jordi Bruin (Good Snooze)*

### Technical Architecture

- **Processing**: 100% local using OpenAI Whisper + Nvidia Parakeet
- Models: Tiny → Large-V3 Turbo (user selects)
- Performance: ~15x real-time speed on Apple Silicon; 1hr recording = ~5min transcription
- **No cloud dependency** for core transcription
- Optional: OpenAI/Claude API keys for AI enhancement (user-provided)
- **Meeting auto-recording**: Auto-captures Zoom, Teams, Webex, Skype, Discord, Chime

### Hotkey Design

- Global hotkey for system-wide dictation mode
- Menu bar icon for quick access
- Drag-and-drop audio files directly onto app

### Pricing

| Tier | Price | Notes |
|------|-------|-------|
| Free | Free | Basic transcription, limited models |
| Pro | ~$59 one-time (Gumroad) | Full feature set including dictation, meeting recording |
| Pro (App Store) | ~$79.99 AUD lifetime via in-app | Missing dictation + meeting recording (App Store restriction) |

- **Two distributions**: Gumroad (recommended, more features) vs Mac App Store (limited)
- Gumroad version gets features App Store can't have due to Apple restrictions

### Pro Features Locked Behind Paywall

- Correct spelling automatically
- Enable punctuation (comma, period, new line)
- Speaker detection / diarization
- Ignore unwanted segments
- Dictation AI prompts
- Batch transcription
- Record app audio
- Large V3 Turbo model

### User Complaints

- *"I often get 'network not available' after talking for a while (a couple of sentences). This is really frustrating as I need to repeat myself and often forget what I just said."* — Reddit (Gumroad payment→licensing issues)
- Support responsiveness issues
- Sometimes stalls on very long files (2-3hr MP4)
- App Store version severely limited vs Gumroad

### What They're Good At

- Best-in-class for bulk/batch file transcription
- Meeting recording is genuinely useful (auto-capture from Zoom/Teams)
- Simple, privacy-preserving
- SRT/VTT export for subtitles
- Speaker separation

---

## Competitor 4: Windows 11 Built-in Dictation / Fluid Dictation

### Technical Architecture

- Classic voice typing: `Win + H` hotkey, online speech recognition (cloud-based)
- **Fluid Dictation** (new, 2025): Local SLMs (Small Language Models) running on-device
  - Auto-removes filler words ("um", "uh")
  - Auto-punctuation and grammar cleanup
  - Runs locally — no data leaves the PC
  - **Limitation**: Only on Copilot+ PCs (NPU required), not available on all Windows 11 machines
  - Available in builds like 26120.5790+

### What Makes It Notable

- Microsoft's move to on-device AI processing is significant — validates the local approach
- No subscription cost — bundled with OS
- Secure fields (passwords, PINs) have dictation disabled by default

### Key Limitations vs Third-Party Apps

- No custom dictionary (beyond basic)
- No AI editing mode
- No context awareness
- No custom modes/prompts
- Limited to Copilot+ PCs for advanced features

---

## Competitor 5: VoiceInk (tryvoiceink.com)

*Open source (GPL-3), indie developer*

### Technical Architecture

- **Processing**: 100% local via Whisper models
- Uses Apple's MLX/CoreML for inference on Apple Silicon
- Context awareness: Screenshot → OCR to detect text (limited vs Superwhisper's accessibility API access)
- Open source — buildable from source, also available as paid prebuilt
- **Power Mode**: Detects active app/URL, auto-applies pre-configured settings

### Pricing

| Option | Price |
|--------|-------|
| Build from source | Free |
| Prebuilt binary | $39 one-time |
| Also: brew install --cask voiceink | Free |

### Features

- Personal dictionary: custom words, industry terms, text replacements
- Smart modes with AI enhancement
- Global shortcuts, push-to-talk
- AI Assistant mode (ChatGPT-like conversational)

### Why It Matters (for Waffler)

VoiceInk is open source and shows exactly how to implement:
- Local Whisper inference with Apple Silicon optimisation
- App-detection-based mode switching
- Screenshot-based context extraction
- Personal dictionary integration with Whisper vocabulary hints

---

## Competitor 6: Other Notable Tools

### Spokenly (spokenly.app)
- Free local mode (unlimited on-device), $7.99/mo for cloud
- Whisper + modern STT models
- Generous — iOS keyboard included
- Context: sends screenshot to AI (limited)
- Available on App Store (limiting for advanced features)

### BetterDictation (betterdictation.com)  
- Push-to-talk, fully offline on Apple Silicon
- $2/month or $24 lifetime (cheapest paid option)
- Optional Pro layer removes fillers, tidies grammar
- No Intel support

### Willow Voice (willowvoice.com)
- Cloud-first, privacy-preserving
- Claims sub-1 second latency, 40% higher accuracy than built-in
- Sub-200ms processing claimed
- Personalises to writing style over time
- SOC2 compliance, zero voice data storage
- Mac only (Windows in progress)
- Subscription pricing

### AquaVoice (withaqua.com)
- Browser extension + Mac app
- Context-aware formatting
- Praised for browser integration
- Criticism: periodic lag
- 49 languages; Teams plan English-only

### Nuance Dragon Professional (Windows)
- Legacy leader, $399-599 one-time
- Deep command-and-control macros
- Domain-specific vocabulary training (legal/medical)
- Still best for full PC voice control
- Too complex/expensive for casual use

---

## Feature Gap Analysis

### Waffler Current Features
- Whisper STT (cloud via OpenAI)
- GPT-4o-mini cleanup
- 3 modes: Normal / Ramble / Agentic
- History panel
- Mode selector overlay
- Live VU bars
- Floating overlay
- Right Option hotkey

### Missing vs Competitors (Priority Ordered)

| Feature | Has It | Notes |
|---------|--------|-------|
| **Custom vocabulary/dictionary** | ❌ | All major competitors have this |
| **Snippets (voice shortcuts to text)** | ❌ | Wispr Flow killer feature |
| **Context from selected text** | ❌ | Superwhisper's top feature |
| **Context from clipboard** | ❌ | Easy to add, high value |
| **Pause/resume recording** | ❌ | Requested repeatedly in Superwhisper reviews |
| **Push-to-talk mode** | ❌ | Many users prefer hold-to-talk vs toggle |
| **Context-aware tone** | Partial | Waffler modes are manual; competitors do it automatically |
| **Command/edit mode** | ❌ | "Make this formal" etc. |
| **Whisper/quiet mode** | ❌ | Wispr Flow differentiator |
| **Usage stats / WPM counter** | ❌ | Superwhisper added this (engagement feature) |
| **File transcription** | ❌ | MacWhisper's specialty |
| **Keyboard shortcut modes** | Partial | Waffler uses Right Option only; competitors allow any key |
| **Local model option** | ❌ | Privacy segment is large; local Whisper = no API cost |
| **BYOK (Bring Your Own Key)** | ❌ | Reduces Waffler's API costs, attracts power users |
| **App-specific mode auto-switching** | ❌ | "Activate when using Telegram" — Superwhisper feature |
| **Cancel mid-recording** | Unknown | Competitors have Esc to cancel |

---

## Technical Deep Dive: How Competitors Handle Key Problems

### 1. AI Cleanup: Prompt vs Fine-tuned Model

**Evidence: All major competitors use prompt-based LLM cleanup, not fine-tuned models.**

- Superwhisper: Explicit custom prompt editor in UI; user writes system prompt
- VoiceInk: Custom AI enhancement prompts visible in settings
- Wispr Flow: Black box but contextual — adapts by app type. Almost certainly prompt-based
- MacWhisper: Uses user-provided API keys with their own prompts

**What this means for Waffler**: GPT-4o-mini with a system prompt IS the industry standard. The differentiation is in WHAT you send with the prompt (context) and whether users can customise it.

### 2. Context Awareness: How Superwhisper Reads the Screen

Superwhisper uses macOS Accessibility APIs to capture:
- **Selected Text**: Grabbed via accessibility tree when recording starts
- **Application Context**: Active input field text (via accessibility), app name, window title — grabbed AFTER transcription completes but before LLM processing
- **Clipboard**: Whatever was copied in last 3 seconds

This is more powerful than screenshot approaches because it gets actual text (including scrolled text), not just what's visible. Requires Accessibility permissions.

In comparison:
- VoiceInk: Screenshot + OCR (weaker — only visible text)
- Spokenly: Screenshot to LLM (weakest)
- Wispr Flow: App-type detection only (no screen reading)

### 3. Hotkey Approaches

| App | Default | Hold vs Toggle | Cancel |
|-----|---------|----------------|--------|
| Wispr Flow | `fn` key | Hold to talk (push-to-talk) | Release = stop |
| Superwhisper | `⌥ + Space` | Toggle (start/stop) OR push-to-talk | Esc to cancel |
| MacWhisper | Configurable | Toggle | Esc |
| VoiceInk | Configurable | Both modes | Esc |
| Waffler | Right Option | Toggle | Unknown |

**Key insight**: Users want BOTH modes — hold for short bursts, toggle for long dictation. Superwhisper added mouse button trigger in v2.6.0.

### 4. Pipeline Latency

- **Wispr Flow**: Sub-second after releasing key (cloud, fast inference)
- **Superwhisper** (Parakeet Realtime): Near real-time local transcription now
- **Superwhisper** (Whisper Large cloud): ~2-3 seconds
- **MacWhisper** (local large model): Fast but initial model load is slow
- **Waffler**: Depends on OpenAI Whisper API latency + GPT-4o-mini

### 5. Personalisation Approaches

No competitor appears to fine-tune the STT model per user. Personalisation is implemented via:
1. **Vocabulary hints**: Words passed as prompt hints to Whisper (`initial_prompt` parameter)
2. **Post-processing dictionary**: Whisper → text → regex/LLM replacement of common transcription errors
3. **Writing style adaptation**: LLM cleanup prompt includes user's past corrections (Wispr Flow claims this)

### 6. Open Source Resources Worth Studying

- **WhisperKit** (github.com/argmaxinc/WhisperKit): MIT license, Swift package for on-device Whisper on Apple Silicon. Used by multiple macOS apps.
- **VoiceInk** (github.com/Beingpax/VoiceInk): Full macOS voice dictation app, GPL-3, shows complete implementation
- **whisper.cpp** (github.com/ggerganov/whisper.cpp): C++ Whisper inference, powers many local apps

---

## User Review Goldmine: What People Actually Complain About

*Direct quotes from Reddit threads, App Store, and review sites:*

**Accuracy failures**:
- *"Apple's voice-to-text on the Mac might be fast, but any brief pause to think or redo a sentence would throw it off entirely"* — Cult of Mac
- *"They're still full of typos, they lack context, and generally you sound kind of like an imbecile - especially with ChatGPT"* — App Store review

**Reliability**:
- *"The biggest problem is that it often freezes, and when it does, it can also freeze whatever app I'm working in"* — r/ProductivityApps on Wispr Flow Windows
- *"Mic not working, support unresponsive... I spent my pomodoro kicking off my voice dictation planning session, being blocked by this bug, and now to gsd I'm downloading superwhisper so I can unblock my day"* — r/WisprFlow
- *"I often get 'network not available' after talking for a while"* — r/MacWhisper

**Missing features**:
- *"I think the only thing that's missing is the absence of a pause button"* — Superwhisper user (Reddit)
- *"6 minute time limit per transcription"* — Wispr Flow (Zack Proser review)

**Privacy**:
- *"The privacy policy lacks clarity around data handling practices"* — Multiple Wispr Flow critics
- *"Superwhisper: 'the data never leaves my computer, so I know my data is safe'"* — User testimonial

**Resource usage**:
- *"Wispr Flow consumes about 800MB and constantly uses around 8% CPU"* — Willowvoice review
- *"Slow startup times, taking 8-10 seconds to initialize"* — Willowvoice review

**Superwhisper cleanup quality**:
- *"Superwhisper users... complain about the need to manually clean up transcripts"* — Wispr Flow comparison page
- *"An hour-long dictation turns into another 10 minutes of manual cleanup"* — getvoibe.com

---

## Pricing Landscape Summary

| App | Free Tier | Paid | Model |
|-----|-----------|------|-------|
| Wispr Flow | 2,000 words/week | $15/mo or $144/yr | Subscription |
| Superwhisper | Yes (small models) | $84.99/yr or $249 lifetime | Subscription OR Lifetime |
| MacWhisper | Yes (limited) | ~$59 one-time | One-time |
| VoiceInk | Open source | $39 one-time | One-time |
| BetterDictation | No | $2/mo or $24 lifetime | Both |
| Spokenly | Yes (local) | $7.99/mo cloud | Freemium |
| Windows 11 | Built-in (basic) | Free (Fluid needs Copilot+ PC) | OS bundled |

**Market insight**: Users strongly prefer one-time pricing when available. *"I have SuperWhisper lifetime, but mostly just use Alter"* — the lifetime purchase was the tipping point. Subscription fatigue is real.

---

## Distribution Insights

| App | Mac App Store | Direct Download | Notes |
|-----|--------------|-----------------|-------|
| Wispr Flow | No (desktop) | Yes | iOS on App Store |
| Superwhisper | No | Yes (DMG) | Also on Setapp (limited) |
| MacWhisper | Limited | Gumroad | App Store version missing key features |
| VoiceInk | No | Direct + Homebrew | Open source, build yourself |
| BetterDictation | Yes | Yes | Both |

**Key insight**: Distributing outside the Mac App Store enables features Apple doesn't allow:
- Accessibility API access (screen reading for context)
- Auto-launch at login without user prompts
- Global hotkeys via CGEventTap (system level)
- Meeting audio recording

MacWhisper explicitly says dictation and meeting recording are NOT available in the App Store version.

---

## Top 10 Features/Improvements for Waffler

*Priority ordered. Effort: S = days, M = 1-2 weeks, L = 2+ weeks*

### 🔴 Critical (Parity Features)

**1. Custom Vocabulary / Personal Dictionary**
- **What**: Users add words (names, jargon, acronyms) that reliably transcribe correctly
- **How**: Pass words as Whisper `initial_prompt` parameter and/or as system prompt context for LLM cleanup; also do regex post-processing replacements
- **Evidence**: All major competitors have this. Superwhisper, VoiceInk pass vocabulary to Whisper models directly. Wispr Flow stores and syncs personal dictionary.
- **User quote**: *"The app learns your unique words and adds them to your personal dictionary automatically"* — cited as core value by Wispr Flow users
- **Effort**: M
- **Impact**: High — directly reduces transcription errors for proper nouns

**2. Snippets / Voice Text Expansion**
- **What**: Say a trigger phrase → expand to full text. E.g. "my email" → inserts full email address
- **How**: Post-processing step after LLM cleanup, detect trigger phrases, replace with stored snippets
- **Evidence**: Both Wispr Flow and Superwhisper have this. Multiple reviewers cite it as a power-user killer feature
- **User quote**: *"Snippets let users assign a shortcut word to expand into a full paragraph or link (perfect for repeatedly sharing Calendly links, code templates or customer-support replies)"*
- **Effort**: S-M
- **Impact**: High — high stickiness feature, especially for developers

**3. Hold-to-Talk / Push-to-Talk Mode**
- **What**: Hold hotkey = recording, release = transcribe. Alternative to Waffler's current toggle
- **How**: Track key-down vs key-up events; short hold = push-to-talk, long hold (>300ms) = push-to-talk, quick tap = toggle
- **Evidence**: Wispr Flow uses fn hold as default. Superwhisper added this in update. Users strongly prefer it for short bursts
- **User quote**: *"I love hitting the fn key twice to start long talk/dictation mode"* — and separately hold for quick replies
- **Effort**: S
- **Impact**: High — fundamentally changes UX feel, reduces friction

**4. Pause/Resume Recording**
- **What**: Pause mid-dictation without losing the session (to think, to sneeze, to check notes)
- **How**: Buffer audio, track pause state, resume recording to same buffer
- **Evidence**: Requested repeatedly for Superwhisper (listed as #1 missing feature by power users). VoiceInk and MacWhisper have it
- **User quote**: *"I think the only thing that's missing is the absence of a pause button"*
- **Effort**: M
- **Impact**: High — removes major friction for longer dictation sessions

**5. Context from Selected Text / Clipboard**
- **What**: Before recording, selected text is sent to LLM along with dictation for smarter responses
- **How**: On recording start, capture selected text via macOS Accessibility APIs (`AXSelectedText`); capture clipboard content; include in LLM prompt as context
- **Evidence**: Superwhisper's "Super Mode" does this — full text of active input field, selected text, clipboard
- **Example use case**: Select a paragraph, dictate "rewrite this more formally" → LLM gets context + instruction
- **Effort**: M (requires Accessibility permission request)
- **Impact**: High — enables command/edit mode, makes Agentic mode much more powerful

### 🟡 Important (Differentiation Features)

**6. App-Specific Mode Auto-Switching**
- **What**: Define which mode activates based on which app is in focus. E.g., "Slack → Casual mode", "Email → Formal mode"
- **How**: Watch `NSWorkspace.shared.frontmostApplication`, switch mode when app changes
- **Evidence**: Superwhisper's "Activate when using" feature. Multiple power users cite this as reason they pay for Superwhisper
- **User quote**: *"I use a more casual tone for personal messages in WhatsApp, Telegram and the Messages app. For work conversations in Slack and emails, it switches to a more formal style automatically"*
- **Effort**: M
- **Impact**: Medium — reduces manual mode switching, increases perceived intelligence

**7. Configurable Cleanup Prompts (Power User Mode)**
- **What**: Let users write their own system prompt for the LLM cleanup step
- **How**: Settings panel with textarea for custom instructions, override default prompt
- **Evidence**: Superwhisper's custom modes. Users share prompts on Reddit/Notion. Major reason power users choose Superwhisper over Wispr Flow
- **User quote**: *"In SuperWhisper you can configure specific prompts for certain behaviors... Both are great. Superwhisper is better if you need to transform your output using a custom prompt."*
- **Effort**: S (UI work mainly; backend already supports prompts)
- **Impact**: Medium — small segment but high-value power users

**8. Usage Statistics / WPM Counter**
- **What**: Show words dictated, time saved vs typing, WPM comparison
- **How**: Track word counts per session, store locally, calculate typing equivalent
- **Evidence**: Superwhisper added WPM typing test + stats in v2.7.0. Wispr Flow shows "220 WPM" claims prominently. Strong engagement/retention driver
- **User quote**: *"I consistently hit 175 WPM [with Wispr Flow]"* — people love sharing these numbers
- **Effort**: S
- **Impact**: Medium — retention/engagement, marketing material, users share on Twitter

**9. Whisper/Quiet Dictation Mode**
- **What**: Optimise for whispering — lower volume detection sensitivity, indicate in UI
- **How**: Adjust VAD (Voice Activity Detection) threshold, show visual indicator of whisper mode active
- **Evidence**: Wispr Flow explicitly markets "Whisper Mode" as a differentiator. Cult of Mac reviewer: *"Even when I'm whispering in a cafe or a shared workspace, Wispr Flow has no trouble"*
- **Effort**: S
- **Impact**: Medium — niche but memorable feature, good for marketing

**10. BYOK (Bring Your Own API Keys)**
- **What**: Users provide their own OpenAI/Anthropic API keys; Waffler passes costs through
- **How**: API key storage (Keychain), route requests through user's key
- **Evidence**: Superwhisper Pro has BYOK. MacWhisper optional. Attracts privacy-conscious and cost-sensitive users
- **User quote**: *"FridayGPT with local model or sometimes with the Groq API for voice transcription"* — users actively BYOK when apps don't provide it
- **Effort**: M
- **Impact**: Medium-Low — reduces Waffler's API costs, attracts power users, but adds support complexity

---

## Strategic Positioning Recommendation

Based on this research, Waffler should position as:

**"The privacy-forward, developer-grade voice dictation for Mac — with the polish of Wispr Flow and the control of Superwhisper, without the $249 price tag."**

Key differentiators to build:
1. **Smarter cleanup** (context from screen/clipboard — better than Wispr Flow)
2. **Power user control** (custom prompts, BYOK — competitive with Superwhisper)
3. **Simple daily UX** (push-to-talk, snippets, vocabulary — parity with Wispr Flow)
4. **Honest pricing** (one-time option OR lower subscription — vs Wispr Flow's $15/mo)

The Agentic mode is genuinely unique. No competitor has this — lean into it.

---

*Research compiled from: wisprflow.ai, superwhisper.com, goodsnooze.gumroad.com, afadingthought.substack.com, reddit.com/r/macapps, reddit.com/r/ProductivityApps, reddit.com/r/WisprFlow, reddit.com/r/MacWhisper, clickup.com, cultofmac.com, zapier.com, willowvoice.com, getvoibe.com, implicator.ai, github.com/Beingpax/VoiceInk, github.com/argmaxinc/WhisperKit, superwhisper.com/docs, apps.apple.com*
