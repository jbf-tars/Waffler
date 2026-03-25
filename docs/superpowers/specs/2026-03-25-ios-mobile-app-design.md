# Waffler iOS Mobile App - Design Specification

**Date:** 2026-03-25
**Status:** Draft
**Author:** Claude Sonnet 4.5 + James

## Overview

Transform Waffler into a mobile iOS app with custom keyboard extension, enabling voice-to-text input anywhere on iPhone. Similar to Wispr Flow's mobile experience but with Waffler's BYOK (bring your own key) privacy model.

## Goals

- **Primary:** Enable Waffler voice input on iOS via keyboard extension
- **Privacy:** Maintain desktop app's zero-server, BYOK architecture
- **UX:** Seamless, always-available voice input (like global hotkey on desktop)
- **Quality:** Native iOS feel with Waffler brand identity

## Non-Goals (v1)

- ❌ Android support
- ❌ Full custom keyboard (QWERTY replacement)
- ❌ Multiple processing modes (only "Normal" mode for v1)
- ❌ iCloud sync (local-only history for v1)
- ❌ Widget or Siri integration

---

## System Architecture

### Components

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│   Main App      │     │ Keyboard Extension   │     │  WafflerCore    │
│                 │     │                      │     │   (Framework)   │
│ • Settings      │────▶│ • "Start Waffling"   │────▶│                 │
│ • History       │     │   Button             │     │ • AudioEngine   │
│ • API Setup     │     │ • Recording UI       │     │ • WhisperClient │
│ • Onboarding    │     │ • Status Display     │     │ • LLMClient     │
└─────────────────┘     └──────────────────────┘     │ • Storage       │
                                                      │ • Keychain      │
                                                      └─────────────────┘
                                    │
                                    ▼
                        ┌───────────────────────────┐
                        │      App Groups           │
                        │  (Shared Storage)         │
                        │                           │
                        │ • Keychain (API keys)     │
                        │ • CoreData (History)      │
                        │ • UserDefaults (Settings) │
                        └───────────────────────────┘
```

### Tech Stack

- **Language:** Swift (iOS 15+)
- **UI Framework:** SwiftUI
- **Storage:** CoreData (history), Keychain (API keys), UserDefaults (settings)
- **Networking:** URLSession with async/await
- **Audio:** AVFoundation (AVAudioRecorder)
- **Architecture:** Shared Framework pattern (WafflerCore.framework)

### Approach: Shared Framework

**Why:** Industry standard for keyboard extensions. Avoids code duplication, enables clean separation, easy to test.

**Structure:**
```
WafflerApp/          (Main iOS app target)
WafflerKeyboard/     (Keyboard extension target)
WafflerCore/         (Shared framework)
  ├── Audio/
  ├── Transcription/
  ├── Storage/
  ├── API/
  └── Models/
```

Both app and keyboard link to `WafflerCore.framework` and share data via App Groups.

---

## User Experience

### Onboarding Flow (First Launch)

**Screen 1: Welcome**
- Waffler app icon (golden waffle)
- "Welcome to Waffler"
- "Voice-to-text anywhere. Private, powerful, yours."
- [Get Started] button

**Screen 2: Choose Provider**
- "Choose AI Provider"
- Radio buttons:
  - ⚪ OpenAI (Whisper + GPT-4o-mini)
  - 🔘 Groq (Whisper-large-v3 + Llama 3.3 70B) ← default
- [Continue] button

**Screen 3: API Key Entry**
- "Enter API Key"
- "Securely stored in your iPhone Keychain"
- Input field: [gsk_...] (password masked)
- Help box: "Don't have an API key? Get one in 2 minutes →"
  - Links to: https://wafflerai.com/api-key-guide/
- [Save & Continue] button
- [← Back] button

**Screen 4: Enable Keyboard** (iOS system instructions)
- "Enable Waffler Keyboard"
- Step-by-step guide to Settings → General → Keyboard → Keyboards → Add New Keyboard → Waffler
- [Open Settings] button

**Screen 5: Grant Permissions**
- "Allow Microphone Access"
- Explanation of why needed
- [Allow Microphone] button → triggers iOS permission prompt
- [Done] button

### Keyboard Extension UI

**Input Accessory View** (bar above iOS keyboard):
```
┌────────────────────────────────────────────────┐
│                    [Start Waffling] 🎤         │ ← Golden button, right-aligned
└────────────────────────────────────────────────┘
┌────────────────────────────────────────────────┐
│          Standard iOS Keyboard                  │
│                   (Q W E R T Y)                │
└────────────────────────────────────────────────┘
```

**Button States:**
- **Idle:** "Start Waffling" (golden gradient button)
- **Recording:** "Recording..." (red, pulsing)
- **Processing:** "Transcribing..." / "Polishing..." (gray with spinner)
- **Success:** "Done ✓" (green flash, brief)
- **Error:** "Tap to retry" (red)

**Design:**
- **Brand colors:** Golden gradient (#D4A843 → #B08530)
- **Text:** Cream (#FFFDF5)
- **Dark theme:** Background #0a0a0a (matches website)
- **iOS-native:** Rounded pill button (14px border-radius), subtle shadow

---

## Voice Input Flow

**Happy Path:**

1. User taps "Start Waffling" in keyboard
2. Keyboard requests microphone permission (if not granted)
3. `WafflerCore.AudioEngine` starts recording → shows "Recording..."
4. User stops speaking → button released or silence detected
5. `WafflerCore.WhisperClient` sends audio to OpenAI/Groq API → shows "Transcribing..."
6. `WafflerCore.LLMClient` cleans up transcript with GPT-4o-mini/Llama → shows "Polishing..."
7. Keyboard inserts cleaned text into text field → shows "Done ✓" briefly
8. `WafflerCore.Storage` saves to history (CoreData via App Group)

**Timing:**
- Target: <2 seconds end-to-end for 10-second audio clip
- Groq typically faster (~500-1000ms for transcription)
- OpenAI ~1-2 seconds for transcription

---

## API Integration

### Provider Choice

User selects **one** provider during onboarding (can change in Settings):

**Option A: OpenAI**
- Transcription: `POST /v1/audio/transcriptions` (Whisper API)
- Cleanup: `POST /v1/chat/completions` (GPT-4o-mini)
- User provides: OpenAI API key (sk-...)

**Option B: Groq**
- Transcription: `POST /openai/v1/audio/transcriptions` (Whisper-large-v3)
- Cleanup: `POST /openai/v1/chat/completions` (Llama 3.3 70B)
- User provides: Groq API key (gsk-...)

### Cleanup Prompt (Normal Mode)

Use existing desktop app prompt: `/Users/james/waffler/prompts/normal.txt`

**Key behaviors:**
- Remove filler words (um, uh, like)
- Fix grammar and punctuation
- Preserve meaning and detail
- Natural, conversational output

---

## Storage & Persistence

### API Keys
- **Storage:** iOS Keychain (kSecClassGenericPassword)
- **Access:** WafflerCore provides `KeychainManager` class
- **Sharing:** App Group enables both app and keyboard to access same Keychain
- **Security:** Encrypted at rest, backed up to iCloud Keychain (if user enables)

### History
- **Storage:** CoreData SQLite database in App Group container
- **Schema:**
  ```swift
  TranscriptionEntry {
    id: UUID
    timestamp: Date
    rawText: String          // Whisper output
    cleanedText: String      // LLM-cleaned output
    audioURL: URL?           // Optional: store audio file
    wordCount: Int
    provider: String         // "openai" or "groq"
  }
  ```
- **Access:** WafflerCore provides `HistoryManager` class
- **Sync:** Local-only for v1 (no iCloud sync)

### Settings
- **Storage:** UserDefaults in App Group container
- **Keys:**
  - `selectedProvider`: "openai" | "groq"
  - `autoPaste`: Bool (reserved for future)
  - `dialect`: "auto" | "en-GB" | "en-US"
- **Access:** WafflerCore provides `SettingsManager` class

### App Group Setup
- **Identifier:** `group.com.waffler.app`
- **Purpose:** Share data between main app and keyboard extension
- **Contents:**
  - Keychain (API keys)
  - CoreData (history DB)
  - UserDefaults (settings)

---

## Error Handling

### No API Key
- **When:** User tries to record without setting up API key
- **Behavior:** Show alert: "API Key Required. Tap to set up →"
- **Action:** Deep link to Settings in main app

### Microphone Permission Denied
- **When:** User denies mic access
- **Behavior:** Show alert: "Microphone Required. Go to Settings → Waffler → Microphone"
- **Action:** [Open Settings] button

### Network Error
- **When:** API request fails (no internet, timeout, 500 error)
- **Behavior:** Show "Network Error. Tap to retry"
- **Action:** Retry button (up to 2 retries, then fail gracefully)

### Invalid API Key
- **When:** API returns 401 Unauthorized
- **Behavior:** Show "Invalid API Key. Tap to update →"
- **Action:** Deep link to Settings

### Audio Too Short
- **When:** Recording < 0.5 seconds
- **Behavior:** Show "Recording too short. Try again"
- **Action:** Reset to idle state

### Audio Too Long
- **When:** Recording > 30 seconds (Whisper limit)
- **Behavior:** Auto-stop at 30s, process normally
- **Feedback:** Show warning: "Max 30 seconds"

### API Rate Limit
- **When:** 429 Too Many Requests
- **Behavior:** Show "API Rate Limit. Wait 30s" with countdown
- **Action:** Auto-retry after countdown

---

## Settings Screen (Main App)

```
Settings
├─ API Provider
│  ├─ Selected: [Groq ▼]
│  └─ Tap to change → Provider picker
├─ API Key
│  ├─ gsk_•••••••••••• (masked)
│  └─ Tap to update → Key entry
├─ How to Get API Key
│  └─ Links to wafflerai.com/api-key-guide/ ↗
├─ Preferences
│  └─ Spelling: [Auto (match speaker) ▼]
└─ About
   ├─ Version 1.0.0
   ├─ Privacy Policy
   └─ GitHub (open source)
```

---

## Testing Strategy

### Unit Tests
- `WafflerCore` framework (business logic)
  - API client mocks
  - Storage operations
  - Audio encoding

### Integration Tests
- Keyboard → WafflerCore → Storage flow
- App → WafflerCore → API flow

### Manual Testing
- Onboarding flow (fresh install)
- Keyboard extension in Messages, Notes, Email, Safari
- Error scenarios (no internet, invalid key, etc.)
- Dark mode / Light mode
- iPhone SE (small screen) and iPhone Pro Max (large screen)

### Beta Testing
- TestFlight distribution
- 10-20 users
- Collect feedback on:
  - Onboarding clarity
  - Keyboard reliability
  - Transcription accuracy
  - Performance

---

## Implementation Phases

### Phase 1: Foundation
- Project setup (Xcode, Swift, SwiftUI)
- WafflerCore framework skeleton
- App Groups + Keychain + CoreData setup
- Basic UI (onboarding screens)

### Phase 2: Core Logic
- Audio recording (AVFoundation)
- API clients (OpenAI + Groq)
- LLM cleanup integration
- Storage (history, settings)

### Phase 3: Keyboard Extension
- Input accessory view
- "Start Waffling" button + states
- Recording flow
- Text insertion

### Phase 4: Polish & Testing
- Error handling
- Settings screen
- History browser
- UI refinement (brand colors, animations)

### Phase 5: App Store Submission
- App icon (all sizes)
- Screenshots
- App Store listing
- Privacy policy
- Submit for review

---

## Success Metrics (Post-Launch)

- **Adoption:** 100+ downloads in first month
- **Engagement:** 50% of users record >10 transcriptions/week
- **Quality:** <5% error/retry rate
- **Performance:** 95% of transcriptions complete <2 seconds
- **Retention:** 60% of users active after 30 days

---

## Open Questions

1. **Audio storage:** Should we save audio files locally or just text?
   - **Recommendation:** Text-only for v1 (saves space)

2. **History sync:** iCloud sync in v2?
   - **Recommendation:** Yes, but not v1 (scope control)

3. **Vocabulary hints:** Desktop has custom vocab. Include in v1?
   - **Recommendation:** No, too complex for v1. Add in v2.

4. **Silence detection:** Auto-stop recording after N seconds of silence?
   - **Recommendation:** Yes, 2 seconds of silence = auto-stop

---

## References

- **Desktop App:** `/Users/james/waffler` (Python, pywebview, pynput)
- **Website:** wafflerai.com (color scheme, branding)
- **Wispr Flow Research:** `/Users/james/waffler/WISPR_FLOW_IMPROVEMENTS.md`
- **API Guide:** https://wafflerai.com/api-key-guide/
- **GitHub Repo:** https://github.com/jbf-tars/waffler

---

## Appendix: File Structure

```
WafflerIOS/
├── WafflerApp/                   # Main iOS app
│   ├── WafflerApp.swift          # App entry point
│   ├── Views/
│   │   ├── OnboardingView.swift
│   │   ├── SettingsView.swift
│   │   └── HistoryView.swift
│   └── Assets.xcassets/          # App icon, images
│
├── WafflerKeyboard/              # Keyboard extension
│   ├── KeyboardViewController.swift
│   ├── Views/
│   │   └── WafflerInputAccessory.swift
│   └── Info.plist
│
├── WafflerCore/                  # Shared framework
│   ├── Audio/
│   │   └── AudioEngine.swift
│   ├── API/
│   │   ├── WhisperClient.swift
│   │   ├── LLMClient.swift
│   │   └── APIProvider.swift
│   ├── Storage/
│   │   ├── KeychainManager.swift
│   │   ├── HistoryManager.swift
│   │   └── SettingsManager.swift
│   └── Models/
│       ├── TranscriptionEntry.swift
│       └── APIProvider.swift
│
└── WafflerTests/                 # Unit tests
    ├── AudioEngineTests.swift
    ├── APIClientTests.swift
    └── StorageTests.swift
```

---

**End of Design Specification**
