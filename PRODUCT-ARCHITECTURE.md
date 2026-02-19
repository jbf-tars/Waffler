# VoiceFlow — Product Architecture Document
*Research compiled: February 2026 | Status: Pre-launch / Prototype*

---

## Executive Summary

- **Ship BYOK (Bring Your Own Key) for v1.** User supplies their OpenAI API key, you charge $29-49 one-time. Zero ongoing API cost to you, zero server infrastructure, zero compliance burden. This matches your stack perfectly and gets you to paid in weeks, not months.
- **Whispr Flow is NOT doing per-user model training.** Their "personal dictionary" is a word list injected into Whisper's `prompt` parameter — it's a prompt engineering trick, not ML. Their "Styles" feature is just per-context system prompts. You can replicate everything they do, 100% locally.
- **Your biggest v1 risks are packaging (notarization/signing) and piracy prevention, not architecture.** The API cost math is trivial. Use Lemon Squeezy for payments + license keys, PyInstaller for packaging, and $99/yr Apple Developer + $249/yr EV cert for Windows signing.

---

## Recommended Path: v1 Launch

### The One-Sentence Strategy
> Ship a beautifully packaged local app at $29. User brings their OpenAI key. You pocket nearly 100% of revenue. Ship in 4 weeks.

### Why BYOK Wins for v1
1. **No server to maintain.** No AWS, no database, no backend, no ops at midnight.
2. **No compliance liability.** You never touch user audio. Full stop.
3. **No cash flow timing problem.** SaaS means you absorb API costs 30 days before subscriptions arrive.
4. **Perfect positioning against Whispr Flow.** Privacy-first, one-time price, no subscription creep. This is a *selling point*.
5. **Transparent cost to users.** A heavy user dictating 1 hour/day pays OpenAI ~$7/month directly. That's still cheaper than Whispr Flow's $8/month subscription — and they own the key.

### v1 Feature Scope (4-week sprint)
```
Week 1: Package + sign (PyInstaller → .app/.exe, Apple notarization, Windows EV cert)
Week 2: License key activation (Lemon Squeezy API integration)  
Week 3: API key onboarding UX (setup wizard, validation, encrypted keychain storage)
Week 4: Polish + submit to first 10 beta testers
```

---

## Distribution Options — Deep Comparison

### Option A: BYOK (Recommended for v1)
User provides their own OpenAI API key. App runs fully locally.

**Pros:**
- Zero infrastructure cost to James
- No per-user margin math to worry about
- Privacy story is unbeatable: "Your audio never leaves your machine"
- No Terms of Service complications with OpenAI (user's own key = user's own account)
- Simpler UX than you think — most target users already have OpenAI accounts

**Cons:**
- Target market narrows (users must have/create OpenAI account and add billing)
- Onboarding friction: setup wizard required
- You can't offer a free tier easily (they'd burn their own credits)

**Pricing logic:**
- User cost: $0-10/month to OpenAI depending on usage (see cost breakdown below)
- Your revenue: $29 launch / $49 regular, 100% margin minus payment fees (~5%)
- Your break-even: First sale

**Key onboarding UX (critical):**
```
1. Welcome screen with 3 steps shown visually
2. "You need an OpenAI API key — here's how to get one" (link to platform.openai.com/api-keys)
3. Paste key field → validate key live (test with $0.001 API call)
4. Store key in macOS Keychain / Windows Credential Manager (never plain text)
5. Optional: Let user set a monthly spend cap (OpenAI supports this)
```

---

### Option B: SaaS Backend (Future v2+)

You absorb API costs, charge subscription. **Do not build this for v1.**

**Architecture needed:**
- FastAPI or Express backend on a VPS (Hetzner, Railway, Render)
- Auth system (Supabase or Clerk)
- Per-user API key management
- Rate limiting + abuse prevention
- Webhook handling for subscription events
- Audio → Whisper → GPT pipeline server-side
- Storage for user word lists / preferences (PostgreSQL or Redis)

**Cost breakdown per user per month (SaaS model):**

| User Type | Min/Month | Whisper Cost | GPT-4o-mini Cost | Total API Cost |
|-----------|-----------|--------------|------------------|----------------|
| Light (5 min/day, 20 days) | 100 min | $0.60 | ~$0.01 | **$0.61** |
| Moderate (20 min/day) | 400 min | $2.40 | ~$0.05 | **$2.45** |
| Heavy (1hr/day) | 1,200 min | $7.20 | ~$0.15 | **$7.35** |

*GPT-4o-mini is negligible: 400 min/month ≈ 200 sessions × 600 tokens in / 300 tokens out = $0.05 total*

**Break-even at $8/month subscription (like Whispr Flow):**
- Per moderate user: $8 revenue − $2.45 API − $0.30 server share = **$5.25 margin (66%)**
- But: you still need $50-200/month in server overhead before user #1

**Scale economics:**

| Users | Monthly Revenue | API Costs | Server ($) | Net Margin |
|-------|----------------|-----------|------------|------------|
| 100 | $800 | $245 | $100 | $455 (57%) |
| 1,000 | $8,000 | $2,450 | $300 | $5,250 (66%) |
| 10,000 | $80,000 | $24,500 | $1,500 | $54,000 (68%) |

**Why not v1:** Subscription revenue requires churn management, compliance, uptime SLAs, billing disputes, support volume. This is a company, not a product.

---

### Option C: Credit Bundle (Embedded Key Pool)

One-time purchase includes X minutes of dictation via your pooled OpenAI key.

**How Cursor-style credit systems work:**
- User buys the app once ($29) → get 500 minutes of dictation "included"
- After that, option to buy refill packs or switch to BYOK
- Your pooled API key is obfuscated in the binary (not safe, just deterrent)

**Risks:**
- Your API key is in the app binary. Even obfuscated, it WILL be extracted.
- One viral Reddit post = your $500/day bill
- OpenAI TOS technically restricts sharing keys
- Rate limiting per-user is complex to implement without a server
- Cursor uses subscriptions + a backend to meter usage — you'd need the same

**Verdict: Skip for v1.** The obfuscation game is not winnable for a solo developer. BYOK is cleaner.

---

## Does Whispr Flow Learn From Users?

### Verdict: No. It's prompt engineering, not model training.

**What they're actually doing:**

1. **Personal Dictionary** = A JSON list of custom words stored server-side, injected as the `prompt` parameter in Whisper API calls. Whisper uses the prompt as a vocabulary hint for transcription. This is a documented Whisper feature:
   ```python
   openai.audio.transcriptions.create(
     model="whisper-1",
     file=audio_file,
     prompt="TARS, OpenClaw, pywebview"  # <-- custom vocabulary
   )
   ```
   When you correct a misspelling in their app, they add it to this list. That's it.

2. **Styles (tone per app)** = Different system prompts per detected application context. "Slack message" → casual cleanup prompt. "Email" → formal cleanup prompt. Stored server-side, synced across devices.

3. **Context-aware spelling** ("spells names right") = Uses the surrounding text buffer as additional context in the STT prompt. Not ML.

**Why per-user fine-tuning is not viable:**
- OpenAI fine-tuning costs: $3/hour training + $0.30/1M tokens inference (fine-tuned model)
- Minimum useful dataset: ~1000 user corrections per user to see improvement
- That's months of use before fine-tuning is even warranted
- Each user would need their own fine-tuned model endpoint
- At 1000 users: 1000 separate model deployments — operationally insane
- Whisper open-source fine-tuning requires GPU infrastructure ($276-350/month per GPU)

**What "learning" looks like architecturally if you DID do it:**
```
1. Collect anonymized (correction → target) pairs per user
2. Batch fine-tune whisper-small locally per user every N months
3. Serve per-user model from your own inference endpoint
4. Cost: ~$50/user/year in compute — unsustainable at $8/month
```

**What competitors actually do:**
- Whispr Flow: Word list + prompt injection (confirmed from feature descriptions)
- Dragon Naturally Speaking: Client-side acoustic model adaptation (the old way, pre-LLM)
- Apple Dictation: On-device Whisper variant, no per-user training
- Google Docs voice: Server-side STT, no per-user fine-tuning

**You can match Whispr Flow's "learning" in 1 day:** Local `user_dictionary.json` → injected into every Whisper call. Auto-populated when user corrects text. Done.

---

## API Cost Breakdown (BYOK Model — For User Transparency)

Since the user pays OpenAI directly, this is what you should communicate clearly in marketing:

| Use Pattern | Minutes/Month | Whisper Cost | GPT-4o-mini | **Total to User** |
|-------------|--------------|--------------|-------------|-------------------|
| Casual (5 min/day) | ~100 min | $0.60 | $0.01 | **~$0.61/mo** |
| Daily user (20 min/day) | ~400 min | $2.40 | $0.05 | **~$2.45/mo** |
| Power user (1hr/day) | ~1,200 min | $7.20 | $0.15 | **~$7.35/mo** |
| Extreme (2hr/day) | ~2,400 min | $14.40 | $0.30 | **~$14.70/mo** |

**Marketing angle:** "Even a power user pays ~$7/month to OpenAI. Whispr Flow charges $8/month *plus* sends your audio to their servers. VoiceFlow is a one-time $29 purchase and you own your data."

OpenAI new pricing note: GPT-4o-mini-transcribe is now $0.003/min (half of whisper-1). Worth switching to for cost reduction, though whisper-1 is battle-tested.

---

## Packaging & Distribution Plan

### macOS

**Toolchain:**
- **PyInstaller** → creates `.app` bundle (most mature, best community support for pywebview)
- Alternatively: **Briefcase** (BeeWare) — better macOS citizen, worth investigating
- **NOT Electron** — too heavy for a simple app

**Code signing + notarization (mandatory for distribution):**
```bash
# 1. Enroll in Apple Developer Program ($99/year)
#    → Get "Developer ID Application" certificate

# 2. Build with PyInstaller
pyinstaller --windowed --name VoiceFlow --icon icon.icns main.py

# 3. Sign the .app (sign everything inside recursively)
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: James Smith (XXXXXXXXXX)" \
  --entitlements entitlements.plist \
  VoiceFlow.app

# 4. Create .dmg
hdiutil create -volname VoiceFlow -srcfolder VoiceFlow.app -ov -format UDZO VoiceFlow.dmg

# 5. Notarize
xcrun notarytool submit VoiceFlow.dmg \
  --apple-id your@email.com \
  --team-id XXXXXXXXXX \
  --password "app-specific-password" \
  --wait

# 6. Staple the ticket
xcrun stapler staple VoiceFlow.dmg
```

**Entitlements needed** (for pywebview + microphone):
```xml
<key>com.apple.security.device.audio-input</key><true/>
<key>com.apple.security.network.client</key><true/>
```

**Without notarization:** Gatekeeper blocks the app. Users see "unidentified developer" and need to right-click → Open. Death for a paid product.

**Cost:** $99/year Apple Developer Program. Notarization itself is free.

**PyInstaller gotcha:** All `.dylib` and `.so` files must also be signed individually before signing the bundle. Use a script to sign recursively. The Nuitka route is faster startup but notarization is more complex.

---

### Windows

**Code signing:**
- **OV (Organization Validated) cert:** ~$100-200/year. SmartScreen warning appears until your app builds "reputation" (thousands of downloads). Unusable for v1.
- **EV (Extended Validation) cert:** ~$249/year from SSL.com. **Immediately bypasses SmartScreen.** Required for v1.

**Note (March 2024 change):** Microsoft changed how SmartScreen interacts with EV certs. EV still gets faster trust but no longer 100% guaranteed immediate bypass. However, it's still dramatically better than OV.

**Recommended:** SSL.com EV Code Signing cert ($249/year) via their eSigner cloud signing service (no hardware token required since June 2023 rule change).

**Windows packaging:**
```bash
pyinstaller --onefile --windowed --name VoiceFlow --icon icon.ico main.py
# Then sign with signtool.exe or SSL.com eSigner
```

**Distribution format:** `.exe` installer via **Inno Setup** (free) or `.msix` (Microsoft Store compatible).

**Cost summary:**
| Platform | Cost | Renewal |
|----------|------|---------|
| Apple Developer Program | $99 | Annual |
| Windows EV cert (SSL.com) | $249 | Annual |
| **Total signing overhead** | **$348/year** | — |

---

### Self-Hosted vs Stores

| Channel | Fee | Pros | Cons |
|---------|-----|------|------|
| **Lemon Squeezy** | 5% + $0.50 | License key API, global tax handling, webhooks | US-based MoR |
| **Gumroad** | 10% (free tier) or flat 3% | Marketplace discovery, simple | Weak license API, higher fees |
| **Paddle** | ~5% + $0.50 | Enterprise-grade MoR, global | More complex setup |
| **Mac App Store** | 30% | Discovery, trusted, no signing cost | Sandboxing kills microphone access complexity, 30% cut |
| **Microsoft Store** | 12-15% | Discovery | Sandboxing, MSIX required |
| **Direct (Stripe)** | 2.9% + $0.30 | Lowest fees | Must implement license system yourself, handle VAT |

**Recommendation: Lemon Squeezy.** Best license key API in the indie space. Handles VAT/GST globally (Merchant of Record). 5% + $0.50 on $29 = ~$1.95 fee, leaving you $27.05 per sale. License key validation API is well-documented and free to use.

---

## Licensing / Activation

### Recommended: Lemon Squeezy License Keys

**How it works:**
1. User buys on your Lemon Squeezy store page
2. They receive a license key by email automatically
3. App prompts for key on first launch
4. App calls Lemon Squeezy's license API to validate:
   ```python
   import requests
   
   def activate_license(key: str, machine_id: str) -> bool:
       response = requests.post(
           "https://api.lemonsqueezy.com/v1/licenses/activate",
           json={
               "license_key": key,
               "instance_name": machine_id  # UUID per machine
           }
       )
       data = response.json()
       return data.get("activated", False)
   
   def validate_license(key: str, instance_id: str) -> bool:
       response = requests.post(
           "https://api.lemonsqueezy.com/v1/licenses/validate",
           json={"license_key": key, "instance_id": instance_id}
       )
       return response.json().get("valid", False)
   ```
5. Store `instance_id` + `license_key` in macOS Keychain / Windows Credential Manager
6. Re-validate on every launch (requires internet, but graceful offline fallback)

**Activation limits:** Set to 2 activations per license (one Mac, one Windows). Configurable in Lemon Squeezy dashboard.

**Offline grace period:** Cache validation result with timestamp. If offline, allow 7-day grace period before nagging. This handles travel/no-internet scenarios.

**Piracy deterrence is enough, not piracy prevention:**
> Spending $200 on DRM to stop a $29 pirate is not worth it. Ship fast, iterate fast. The pirates weren't going to buy anyway.

---

### Alternative: Paddle

Good if you want more control or expect enterprise customers. Slightly more complex integration. Similar pricing.

### DIY Licensing (Not Recommended)

You could generate your own license keys using RSA signatures, store a public key in the binary, validate locally. Pro: no external dependency. Con: eventual crack, support burden. Not worth it for $29.

---

## Telemetry & Improvement (Without a Server)

### Can you improve prompts without seeing user data? Yes.

**Strategy 1: Opt-in feedback ping**
```python
# After each dictation, show subtle 👍/👎 buttons
# If thumbs down: ask "What went wrong?" (optional, anonymous)
# Aggregate: {"rating": -1, "issue_type": "wrong_words", "app_context": "slack"}
# Send anonymous ping to your endpoint (no audio, no text)
```

**Strategy 2: A/B prompt testing in releases**
- Ship v1.1 with System Prompt A, v1.2 with System Prompt B
- Track which version users upgrade to / stay on (via Lemon Squeezy webhook update stats)
- Iterate prompt based on aggregate feedback issues

**Strategy 3: Public beta program**
- 20 trusted beta users who opt-in to sharing anonymized correction pairs
- Manual review → improve prompt
- No PII, no audio, just correction patterns

**Strategy 4: Prompt iteration from your own usage**
- Eat your own dog food. Use VoiceFlow daily.
- Keep a `PROMPT_CHANGELOG.md` logging what changed and why
- This is how most indie tools improve early

**Privacy-preserving improvement pipeline:**
```
User (opt-in) → local processing → only metadata sent → 
your dashboard → prompt iteration → release update
```

Never log audio. Never log transcribed text. Metadata only: session length, thumbs up/down, detected app context, language detected.

---

## Server vs Local Comparison Table

| Dimension | Local + BYOK (v1) | SaaS Backend (v2+) |
|-----------|------------------|---------------------|
| **Time to ship** | 4-6 weeks | 3-6 months |
| **Infrastructure cost** | $0/month | $50-300/month |
| **API cost to you** | $0 (user pays) | $0.61-7.35/user/month |
| **Revenue model** | One-time $29-49 | $8/month subscription |
| **Per-sale margin** | ~95% (minus payment fees) | 55-70% (minus API + server) |
| **Compliance burden** | None | SOC2, GDPR, HIPAA concerns |
| **Support complexity** | Low (app bugs only) | High (billing, outages, accounts) |
| **Privacy story** | ★★★★★ Unbeatable | ★★★ Sends audio to server |
| **Feature ceiling** | Lower (no cross-device sync) | Higher (sync, teams, web) |
| **Competitive moat** | Price + privacy | Features + stickiness |
| **Recommended for** | v1 launch now | v3+ if traction exists |

---

## Roadmap: Prototype → Paid Product

### Phase 0: Now (Week 1-2)
- [ ] Clean up Python script → proper app structure
- [ ] Add keychain storage for API key (never store in plaintext)
- [ ] pywebview UI: onboarding wizard (key setup → validation → success)
- [ ] Test packaging with PyInstaller on both platforms

### Phase 1: v1 Launch (Week 3-6)
- [ ] Apple Developer enrollment ($99)
- [ ] EV cert purchase for Windows ($249, SSL.com)
- [ ] Lemon Squeezy store setup (product page, license keys enabled)
- [ ] License activation screen in app
- [ ] macOS: notarize .dmg → upload to GitHub Releases or self-hosted
- [ ] Windows: sign .exe installer via eSigner → upload
- [ ] Landing page (simple, fast — Framer, Carrd, or static HTML)
- [ ] **Soft launch: Tweet/Post to target audience with 10 beta invite codes**

### Phase 2: Polish (Month 2-3)
- [ ] Auto-updater (Sparkle on macOS, WinSparkle on Windows)
- [ ] Local user dictionary (custom words → Whisper prompt injection)
- [ ] Opt-in feedback (thumbs up/down → anonymous ping)
- [ ] App context detection (Slack/email/code → tone adjustment)
- [ ] Trial mode: 10 minutes free before license required

### Phase 3: Growth (Month 3-6)
- [ ] Affiliate program via Lemon Squeezy
- [ ] Team license tier ($79, 3 seats)
- [ ] Evaluate: add audio processing quality upgrades
- [ ] Potential: local Whisper model option (offline, no API needed — big privacy win)

### Phase 4: Consider SaaS (Month 6+, if >500 paying users)
- [ ] Only if users are requesting it
- [ ] Start with opt-in cloud sync for word lists (minimal server)
- [ ] Layer subscription on top of existing BYOK base
- [ ] Don't abandon one-time purchase — offer both

---

## Local Whisper: The Nuclear Option

Worth noting: `whisper.cpp` runs on Apple Silicon M1+ in real-time with `whisper-small` or `whisper-medium` models.

- **Cost to user: $0/month forever**
- **Privacy: 100% offline**
- **Quality: 90% of Whisper API for English**
- **Setup: bundled model (150MB - 800MB)**

If you bundle a local Whisper model, you eliminate the OpenAI dependency entirely. The GPT-4o-mini cleanup step still requires an API key, but you could:
1. Skip cleanup (simpler, just raw transcription)
2. Offer cleanup as an optional premium step with their key
3. Build local cleanup with a tiny LLM (Phi-3, Llama 3.2 — possible on Apple Silicon)

**This is a roadmap feature, not v1.** But it's a massive differentiator against Whispr Flow's server-dependent architecture.

---

## Key Decisions Summary

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| Business model | BYOK + one-time purchase | Zero ongoing cost, ship fast |
| v1 price | $29 launch / $49 regular | Impulse buy range, undercuts Whispr Flow's $96/yr |
| Payment platform | Lemon Squeezy | Best license key API for indie devs |
| macOS signing | Apple Developer ($99/yr) | Required for Gatekeeper bypass |
| Windows signing | EV cert, SSL.com ($249/yr) | SmartScreen bypass from day 1 |
| Packaging | PyInstaller | Most mature for pywebview stack |
| License validation | Lemon Squeezy API | No backend needed, 2-device limit |
| Offline grace | 7-day local cache | UX sanity for travelers |
| "Learning" | Local word list → Whisper prompt | Matches Whispr Flow, no server needed |
| Telemetry | Opt-in anonymous thumbs up/down only | Privacy-first brand |
| Windows Store / Mac App Store | Skip for v1 | 30% cut + sandboxing restrictions |

---

*Last updated: February 2026 | Next review: After v1 launch*
