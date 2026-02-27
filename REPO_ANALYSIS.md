# Repository Analysis - Mac vs PC vs Remote

## What Claude Code on PC Discovered

Your PC Claude analyzed the GitHub repos and found some important insights about your multi-repo setup.

---

## Repository Structure

### 1. **jbf-tars/voiceflow-app** (Main Development Repo)
- **What:** Desktop application (what we've been working on)
- **Status:** Mac has pushed **13 commits ahead** of remote
- **Location:** `/Users/james/voiceflow-app` (Mac)
- **Branch:** Currently on `self-hosted` (local only, not pushed yet)

### 2. **jbf-tars/waffler** (Same Content + Backend)
- **What:** Same repo content as voiceflow-app
- **Has:** `backend/` folder with FastAPI
  - Auth endpoints (signup, signin)
  - Usage tracking
  - Subscriptions
  - Stripe-ready models
- **Purpose:** Appears to be the "production" or "canonical" repo name

### 3. **jbf-tars/waffler-website** (Marketing Site)
- **What:** Marketing/landing page website
- **Status:** Exists, didn't need changes
- **Purpose:** Public-facing marketing site for Waffler

---

## Key Findings

### ✅ Good News
1. **Backend already exists** in the waffler repo with production-ready FastAPI code
2. **OAuth is fully working** on Windows build (Google OAuth with local callback)
3. **Windows build is ready** at `C:\Users\james\Downloads\Telegram Desktop\VoiceFlow-v37\dist\Waffler\Waffler.exe`

### ⚠️ Important Notes
1. **self-hosted branch NOT pushed** - It's only on your Mac locally
2. **Mac is 13 commits ahead** - Changes on Mac haven't been pushed to remote
3. **Multiple repo names** - `voiceflow-app` vs `waffler` (same content, different names)

---

## What This Means for Self-Hosted Work

### Our Self-Hosted Branch
```bash
# Current state
- Branch: self-hosted (local on Mac only)
- Commits: 2 new commits (backend setup + documentation)
- Status: NOT pushed to remote

# To push it:
git push -u origin self-hosted
```

### Repo Name Confusion
It seems you have two repos with the same content:
- **voiceflow-app** - Development name (what we're working in)
- **waffler** - Production name (what users see)

**Recommendation:** Decide on one canonical repo and archive the other to avoid confusion.

---

## Mac vs Windows Build Differences

### Mac (What We're Working On)
- ✅ Self-hosted backend setup (local branch)
- ✅ Google OAuth with local callback server
- ✅ Tray icon disabled (by design)
- ✅ 13 commits ahead with latest features

### Windows (Current Build)
- ✅ Google OAuth fully working with `http://localhost:17834/callback`
- ✅ Tray icon working on Windows
- ✅ Normal (Wispr) mode prompt style added
- ✅ `poll_oauth_result` for reliable token capture
- ✅ Ready to use at: `C:\Users\james\Downloads\Telegram Desktop\VoiceFlow-v37\dist\Waffler\Waffler.exe`

### Sync Status
```
Mac:     [=============] 13 commits ahead
Remote:  [====] Behind Mac
Windows: [========] Synced with remote (missing Mac's 13 commits)
```

---

## OAuth Setup (Important!)

Claude Code on PC noted this critical configuration:

### Google OAuth Callback URL
**Must be added to Supabase:**
```
http://localhost:17834/callback
```

**Where to add:**
1. Go to Supabase project dashboard
2. Navigate to: **Authentication > URL Configuration > Redirect URLs**
3. Add: `http://localhost:17834/callback`
4. Save

**Why:** The app uses a local callback server on port 17834 to capture OAuth tokens after browser authentication. Without this redirect URL configured, Google OAuth will fail.

---

## How Self-Hosted Fits In

### Current OAuth Flow (BYOK)
```
User clicks "Sign in with Google"
  ↓
Opens browser → Google OAuth
  ↓
Redirects to http://localhost:17834/callback
  ↓
Waffler app captures token
  ↓
Stores in ~/.waffler/session.json
  ↓
User authenticated with Supabase
```

### Self-Hosted OAuth Flow (What We're Building)
```
User enters email/password in Waffler
  ↓
POST to http://localhost:8000/auth/signup
  ↓
Backend creates user in PostgreSQL
  ↓
Returns JWT token + API key
  ↓
Stores in ~/.waffler/session.json
  ↓
User authenticated with YOUR backend (no Supabase)
```

**Key difference:** Self-hosted doesn't need Google OAuth - just email/password to your backend.

---

## Next Steps to Sync Everything

### 1. Push Mac Changes to Remote
```bash
# Push main branch changes (13 commits)
git checkout main
git push origin main

# Push self-hosted branch
git checkout self-hosted
git push -u origin self-hosted
```

### 2. Update Windows Build
Once Mac changes are pushed:
```bash
# On Windows PC
git pull origin main
# Rebuild: python build.py or similar
```

### 3. Configure OAuth Redirect
Add `http://localhost:17834/callback` to Supabase redirect URLs (if not already done).

### 4. Deploy Self-Hosted Backend to VPS
Once Phase 2-3 are complete (LLM endpoint + desktop app integration):
```bash
# Deploy to VPS
# Update desktop app to use https://api.waffler.yourdomain.com
# Users can choose: Self-hosted OR BYOK mode
```

---

## Repository Recommendations

### Option A: Consolidate (Recommended)
1. Keep **waffler** as the canonical repo
2. Archive **voiceflow-app**
3. Update all local references to point to `waffler`

### Option B: Keep Separate
1. **waffler** = Production releases
2. **voiceflow-app** = Active development
3. Merge from voiceflow-app → waffler when ready for release

### Option C: Current State
Keep both, but clearly document which is which:
- Add README to each explaining the relationship
- Use branch protection on `waffler` main branch
- Only push stable releases to `waffler`

---

## Self-Hosted Branch Visibility

### Current State
```
Local only:
  self-hosted branch (2 commits)
    - Backend setup
    - Documentation

Not visible to:
  - Remote GitHub
  - Windows PC
  - Other collaborators
```

### To Make It Visible
```bash
# Push to remote
git push -u origin self-hosted

# Now visible on GitHub and can be pulled by Windows
```

### Integration Strategy
1. **Finish self-hosted implementation** (Phases 2-4)
2. **Test thoroughly** on Mac
3. **Push self-hosted branch** to remote
4. **Create pull request** to merge into main
5. **Test on Windows** before merging
6. **Deploy backend** to VPS
7. **Release** with both BYOK and self-hosted modes

---

## Summary

**What PC Claude Found:**
- ✅ Multiple repos exist (voiceflow-app, waffler, waffler-website)
- ✅ Backend code already exists in FastAPI
- ✅ Windows build is ready with Google OAuth
- ⚠️ Mac is 13 commits ahead of remote
- ⚠️ self-hosted branch is local-only

**What This Means:**
- Your self-hosted work is progressing well but needs to be pushed
- Windows build is slightly behind Mac (missing latest 13 commits)
- OAuth callback URL must be configured in Supabase
- Backend infrastructure already exists, just needs LLM integration

**Immediate Actions:**
1. ✅ Push Mac changes to remote (both main and self-hosted branches)
2. ✅ Add OAuth callback URL to Supabase
3. ⏳ Complete Phase 2-3 of self-hosted setup
4. ⏳ Sync Windows build with latest Mac changes

---

## Questions to Resolve

1. **Which repo is canonical?** `voiceflow-app` or `waffler`?
2. **Should we consolidate?** Merge them into one repo?
3. **What about waffler-website?** Keep separate or monorepo?
4. **Branch strategy?** How to manage main vs self-hosted vs development?

**Last updated:** 2026-02-23
