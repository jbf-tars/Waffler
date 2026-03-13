# Waffler - Project Tracker

**Last Updated:** 2026-02-16 21:22 GMT  
**Status:** 95% Complete - v5 with Automated Testing  
**Next:** James to test automated testing workflow

---

## 🎯 What We're Building

**Elevator pitch:**
"Whispr Flow for ADHD brains. Press a hotkey, ramble your scattered thoughts, get structured text in your clipboard."

**NOT building:**
- ❌ Mobile note-taking app (like Talknotes)
- ❌ Cloud storage / note organization
- ❌ Team collaboration features

**ARE building:**
- ✅ macOS hotkey dictation tool (like Whispr Flow)
- ✅ System-wide, works in any app
- ✅ LLM-powered cleanup of rambling speech
- ✅ Clipboard integration (paste anywhere)
- ✅ ADHD-friendly brain dump tool

---

## 🏗️ What We've Built So Far

### Core Pipeline (100% Complete)

```
Press Cmd+Shift+Space (hotkey)
    ↓
Record audio while key held
    ↓
Deepgram STT (transcribe) - 1074ms
    ↓
MiniMax LLM (restructure rambling) - 8562ms
    ↓
Clipboard (paste anywhere)
```

### Features Implemented

**✅ Phase 1 - Core Functionality (DONE)**
- Hotkey listener (Cmd+Shift+Space)
- Audio recording (16kHz, mono)
- Deepgram STT integration (nova-2 model)
- MiniMax LLM styling with configurable prompts
- Clipboard integration
- macOS notifications
- Error handling & retry logic

**✅ ADHD Optimization (DONE - Feb 16)**
- New prompt: `adhd_ramble.txt` 
  - Handles topic jumping, self-corrections, backtracking
  - Preserves ALL meaningful ideas (even if scattered)
  - Reorganizes chaos into structure
- Configurable prompts via `.env` (PROMPT_STYLE setting)
- Two prompt options:
  - `adhd_ramble` (default) - handles rambling
  - `two_phase` (simple) - basic cleanup

**✅ Packaging (DONE)**
- PyInstaller build → Waffler.app
- Standalone macOS app (13MB)
- No installation required (just copy to Applications)

**⏳ Phase 2 - Backend/Accounts (50% Complete)**
- PostgreSQL database schema designed
- User accounts & auth (FastAPI backend)
- Usage tracking
- NOT TESTED (blocked - needs local DB setup)

**📋 Phase 3 - Distribution (NOT STARTED)**
- Code signing
- Notarization
- DMG creation
- App Store submission (optional)

---

## 📂 Project Structure

```
/Users/tars/Desktop/waffler/
├── main.py                  # Main orchestrator
├── src/
│   ├── audio.py            # Audio recording
│   ├── hotkey.py           # Hotkey listener
│   ├── transcribe.py       # Deepgram STT
│   ├── style.py            # MiniMax LLM styling
│   ├── clipboard.py        # Clipboard manager
│   ├── notify.py           # macOS notifications
│   └── config.py           # Configuration loader
├── prompts/
│   ├── adhd_ramble.txt     # ADHD-optimized prompt (NEW)
│   ├── two_phase.txt       # Simple prompt
│   └── README.md           # Prompt documentation
├── backend/                # Phase 2 (not tested yet)
│   ├── app/
│   ├── database/
│   └── ...
├── dist/
│   ├── Waffler.app       # Built app
│   └── Waffler-ADHD-v2.zip  # Delivered to James
├── .env                    # API keys + config
├── config.yaml             # App settings
├── Waffler.spec          # PyInstaller spec
└── Documentation:
    ├── README.md           # Overview
    ├── QUICKSTART.md       # 5-min setup guide
    ├── TESTING.md          # Comprehensive testing
    ├── ARCHITECTURE.md     # Technical details
    ├── ADHD-OPTIMIZATION.md # Feb 16 changes
    └── PROJECT-TRACKER.md  # This file
```

---

## 🧪 Testing Status

### ✅ Component Tests (PASSED)
- Audio recording: ✅ Works
- Deepgram STT: ✅ 1074ms latency (excellent)
- MiniMax LLM: ✅ 8562ms latency (needs optimization)
- Clipboard: ✅ Works
- Notifications: ✅ Works

### ⏳ Integration Tests (BLOCKED)
- **Hotkey → Full pipeline:** NOT TESTED
- **Accessibility permission:** Waiting for James to grant
- **End-to-end user flow:** NOT TESTED

**Blocker:** Requires James to:
1. Install Waffler.app on his Mac
2. Grant accessibility permission (System Settings → Privacy → Accessibility)
3. Test: Press Cmd+Shift+Space → Speak → Release → Check clipboard

---

## 🎨 Current Configuration

**Audio:**
- Sample rate: 16000 Hz
- Channels: Mono
- Format: WAV

**STT (Deepgram):**
- Model: nova-2
- Language: en-US
- Latency: ~1.1s (excellent)

**LLM (MiniMax):**
- Model: MiniMax-M2.5
- Max tokens: 1024
- Prompt: adhd_ramble (configurable)
- Latency: ~8.6s (needs optimization)

**Hotkey:**
- Default: Cmd+Shift+Space
- Configurable in config.yaml

**API Keys:**
- Deepgram: ✅ Configured
- MiniMax: ✅ Configured

---

## 🚀 Differentiation vs Competitors

### Direct Competitors (Hotkey Dictation)
1. **Whispr Flow** - Hotkey dictation, no LLM cleanup
2. **Pipit** - Hotkey dictation, basic formatting
3. **Sotto** - Local AI dictation, no restructuring
4. **SpeakMac** - Hotkey dictation, auto-punctuation

**Our advantage:**
- ✅ LLM-powered restructuring of rambling (UNIQUE)
- ✅ ADHD-focused positioning (UNIQUE)
- ✅ Configurable prompts (UNIQUE)

### Indirect Competitors (Note-taking apps)
1. **Talknotes** - Mobile app, cloud storage, 100+ templates
2. **VoiceToNotes.ai** - Web/mobile, real-time transcription
3. **Otter.ai** - Team collaboration, meeting notes

**Why we're different:**
- ✅ macOS-native hotkey (not mobile-first)
- ✅ Clipboard workflow (not app lock-in)
- ✅ System-wide (works in any app)
- ✅ No cloud storage (privacy-first)

---

## 📊 Performance Metrics

**Current latency:**
- Audio capture: ~0ms (real-time)
- Deepgram STT: 1074ms ✅
- MiniMax LLM: 8562ms ⚠️ (over 3s target)
- **Total:** ~9.6s ⚠️

**Target latency:**
- Goal: <3s total
- Current: 9.6s (3x over target)

**Issue:** MiniMax latency too high (8.5s)

**Potential fixes:**
1. Switch to faster LLM (Claude, GPT-4o-mini)
2. Optimize MiniMax prompt (reduce tokens)
3. Stream LLM output (partial results)
4. Local LLM (Ollama + llama3)

---

## 💰 Pricing Strategy (Proposed)

**Model:** One-time purchase (NOT subscription)

**Reasoning:**
- Competitors all subscription ($10-15/mo)
- Subscription fatigue is real
- ADHD users value simplicity
- One-time = lower barrier

**Pricing tiers:**
1. **Launch price:** $29 (limited time)
2. **Regular price:** $49
3. **Optional:** API credits subscription ($4.99/mo if needed)

**Why this works:**
- Whispr Flow is ~$10/mo subscription
- One-time $49 = 5 months of Whispr → better value
- ADHD market willing to pay for quality tools
- No ongoing billing = less friction

---

## 📈 Roadmap

### Week 1: MVP Testing (Current - 95% done)
- [x] Core pipeline built
- [x] ADHD prompt optimization
- [x] App packaged and delivered
- [ ] **BLOCKED:** James to test on his Mac
- [ ] Gather feedback on latency, accuracy, UX

### Week 2: Backend + Accounts (50% done, paused)
- [ ] Set up local PostgreSQL
- [ ] Test user accounts & auth
- [ ] Test usage tracking
- [ ] Frontend auth integration
- [ ] E2E testing with database

### Week 3: Distribution (Not started)
- [ ] Code signing certificate ($99/yr Apple Developer)
- [ ] Notarization (security approval)
- [ ] DMG creation (installer)
- [ ] Landing page / marketing site
- [ ] App Store submission (optional)

### Future (Post-launch)
- [ ] Local Whisper.cpp (offline STT)
- [ ] Local LLM (Ollama, privacy-first)
- [ ] Custom hotkey configuration UI
- [ ] Multiple output formats (Markdown, JSON, etc.)
- [ ] Integrations (Notion, Obsidian, etc.)

---

## ⚠️ Known Issues

### Critical
1. **Latency:** 9.6s total (target <3s)
   - MiniMax taking 8.5s
   - Need to optimize or switch LLM

2. **Untested:** End-to-end user flow
   - Waiting for James to grant accessibility permission
   - Can't verify hotkey works in production

### Minor
1. **API credits:** MiniMax minimum top-up $25 (expensive)
2. **Deepgram free tier:** Limited credits (may need paid plan)
3. **PyInstaller warnings:** Deprecated onefile mode (migrate to onedir)

---

## 🎯 Next Steps (Priority Order)

### Immediate (This Week)
1. **James to test Waffler.app**
   - Install on his Mac
   - Grant accessibility permission
   - Test: Press hotkey → Speak rambling → Check clipboard
   - Report: Accuracy? Latency? UX issues?

2. **Fix MiniMax latency** (if issue confirmed)
   - Option A: Switch to Claude/GPT-4o-mini (faster)
   - Option B: Optimize prompt (reduce tokens)
   - Option C: Local LLM (Ollama)

3. **Gather ADHD user feedback**
   - Test with 5-10 ADHD users
   - Validate: Does the cleanup actually help?
   - Iterate on prompt based on feedback

### Short-term (Next 2 weeks)
4. **Decide on backend** (needed for accounts?)
   - If yes: Set up local PostgreSQL, test Phase 2
   - If no: Skip to distribution (simpler)

5. **Finalize pricing & positioning**
   - One-time $29-49 confirmed?
   - Marketing messaging: "ADHD brain dump tool"
   - Landing page mockup

6. **Code signing & distribution**
   - Apple Developer account ($99/yr)
   - Notarization process
   - DMG creation

### Long-term (Post-launch)
7. **Marketing & launch**
   - Reddit (r/ADHD, r/productivity)
   - Product Hunt
   - Indie Hackers
   - Twitter/X

8. **Privacy features**
   - Local Whisper.cpp (offline STT)
   - Local LLM (no API calls)
   - Market as "privacy-first ADHD tool"

---

## 📝 Decision Log

### 2026-02-14: Voice App - Week 1 MVP
- **Decision:** Build hotkey dictation tool (like Whispr Flow)
- **Stack:** Deepgram STT + MiniMax LLM + PyInstaller
- **Status:** 95% complete, waiting for testing

### 2026-02-16: ADHD Optimization
- **Decision:** Optimize for rambling/brain dump use case
- **Implementation:** New `adhd_ramble.txt` prompt
- **Result:** Handles topic jumping, preserves all ideas, restructures chaos

### 2026-02-16: Competitor Research
- **Finding:** Talknotes exists but is mobile note-taking app
- **Clarification:** We're building hotkey dictation tool (different category)
- **Direct competitors:** Whispr Flow, Pipit, Sotto, SpeakMac
- **Unique advantage:** LLM restructuring of rambling (nobody else does this)

### 2026-02-16: Project Tracking
- **Decision:** Create comprehensive tracker (this doc)
- **Reason:** James wants to ensure we're on the same page
- **Status:** PROJECT-TRACKER.md created

### 2026-02-16 20:59: Fixed Packaging Bug (v3)
- **Issue:** v2 had ModuleNotFoundError - PyInstaller didn't include src/ and prompts/
- **Fix:** Updated Waffler.spec to explicitly include src/ and prompts/ directories
- **Change:** Switched from onefile to onedir mode (better for macOS security)
- **Result:** Waffler-v3-fixed.zip (30MB) delivered to James
- **Status:** Waiting for testing

### 2026-02-16 21:22: Automated Testing Solution (v5)
- **Problem:** Manual testing tedious, mic permission issues, empty transcripts
- **James's ask:** "Can you inject voice files for testing? Can you spawn agents to test?"
- **Solution:** Created automated testing framework with pre-recorded audio
- **New files:**
  - `test_with_audio.py` - Run full pipeline with audio file (no mic needed)
  - `record_test_audio.py` - Record test audio once, reuse forever
  - `AUTOMATED-TESTING.md` - Complete documentation
- **Workflow:** Record once → Test unlimited times
- **Benefits:** 
  - No manual hotkey pressing
  - No mic permission for tests
  - Consistent results
  - Fast iteration (seconds not minutes)
- **Result:** Waffler-v5-Automated.zip (69KB) delivered
- **Status:** Waiting for James to test automated workflow

---

## 🤝 Alignment Check

**James, confirm:**
- ✅ We're building a hotkey dictation tool (like Whispr Flow)?
- ✅ NOT building a mobile note-taking app (like Talknotes)?
- ✅ Target users: ADHD / verbal processors / brain dumpers?
- ✅ Clipboard workflow (paste anywhere)?
- ✅ One-time purchase pricing model?

**Next action:** You test Waffler-ADHD-v2.zip on your Mac and report back.

**Questions to answer after testing:**
1. Does the hotkey work? (Cmd+Shift+Space)
2. Is the transcription accurate?
3. Does the ADHD cleanup actually help?
4. Is 9.6s latency acceptable or too slow?
5. Any UX issues or bugs?

---

**End of tracker. Updates will be logged here as we progress.**
