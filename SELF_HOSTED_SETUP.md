# Self-Hosted Waffler Setup Guide

## What We Built

A self-hosted backend for Waffler that eliminates the need for users to bring their own API keys (BYOK). Instead of users paying for Groq/OpenAI directly, your backend provides the service.

### Architecture

```
Client (Mac/Windows Desktop App)
  ↓ Local whisper transcription (FREE, offline)
  ↓ Send text for cleanup
  ↓ HTTPS
Your Backend (VPS ~$10-20/mo)
  ↓ FastAPI + PostgreSQL
  ↓ User auth & quota management
  ↓ Forward text to LLM
  ↓
Serverless GPU (Modal/Replicate - pay per use ~$0.002/transcription)
  ↓ Llama 3.1 70B or similar
  ↓ Text cleanup/styling
  ↓
Return to client → paste to active app
```

### Cost Structure

**Current (BYOK model):**
- Users pay: $2-5/month each for Groq/OpenAI
- You pay: $0
- Total ecosystem cost (100 users): $200-500/month

**Self-hosted:**
- You pay: $20-50/month total (infrastructure + LLM usage)
- Users pay: $0 (included in subscription)
- **Savings: 75-90% reduction**

**Revenue model:**
- Free tier: 20 transcriptions/month (local-only)
- Plus: $5/mo (100 transcriptions) - **96% margin**
- Pro: $12/mo (500 transcriptions) - **92% margin**

---

## What's Been Done

### ✅ Phase 1: Local Backend Setup (Completed)

**Branch:** `self-hosted` (separate from main - your production app is untouched)

**Changes:**
1. **Database Models** ([backend/database/models.py](backend/database/models.py))
   - Added `LLMUsage` table for cost tracking
   - Added `GUID` type decorator for SQLite/PostgreSQL compatibility
   - Fixed Python 3.9 compatibility issues

2. **Auth Endpoints** ([backend/app/auth/router.py](backend/app/auth/router.py))
   - Fixed Python 3.9 type hints (`Optional[str]` instead of `str | None`)
   - Ready endpoints: `/auth/signup`, `/auth/signin`, `/auth/refresh`

3. **Local Development Environment**
   - SQLite database initialized at `backend/waffler_local.db`
   - Backend running on `localhost:8000`
   - Environment config: `backend/.env` (see `.env.example` for template)

**Test it:**
```bash
# Check backend is running
curl http://localhost:8000/health
# Response: {"status":"healthy"}

# Test signup endpoint
curl -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password123"}'
```

---

## Next Steps

### Phase 2: Add LLM Styling Endpoint

**Goal:** Replace Groq with self-hosted serverless LLM

**Option A: Replicate (Recommended - simpler)**
1. Sign up at [replicate.com](https://replicate.com)
2. Get API token
3. Add to `backend/.env`: `REPLICATE_API_TOKEN=r8_...`
4. Create `backend/app/style/router.py`
5. Add endpoint to forward text to Llama 3.1 70B

**Option B: Modal (More control)**
1. Sign up at [modal.com](https://modal.com)
2. Install Modal CLI: `pip install modal`
3. Create `backend/modal_functions/style_text.py`
4. Deploy function: `modal deploy modal_functions/style_text.py`

**Code to add:**
```python
# backend/app/style/router.py (create this file)
from fastapi import APIRouter, Depends, Header
import replicate

router = APIRouter()

@router.post("/style")
async def style_text(
    transcript: str,
    authorization: str = Header(...)
):
    # Verify user has quota
    # Call Replicate API
    # Log usage to LLMUsage table
    # Return styled text
```

**Estimated time:** 30-45 minutes

---

### Phase 3: Connect Desktop App to Backend

**Goal:** Update Waffler desktop app to use your backend instead of direct API calls

**Changes needed:**
1. Create `src/waffler_auth_backend.py` (new file - don't modify existing `waffler_auth.py`)
2. Update `src/style_openai.py` to try backend first, fallback to Groq
3. Add `BACKEND_URL` environment variable support

**Detection logic:**
```python
# App auto-detects mode
if os.getenv("BACKEND_URL"):
    # Self-hosted mode - use your backend
    backend_url = os.getenv("BACKEND_URL")
else:
    # BYOK mode - user's own API keys (existing behavior)
    use_groq_api_key()
```

**Estimated time:** 1-2 hours

---

### Phase 4: Deploy to VPS

**Goal:** Make backend accessible from anywhere

**Recommended provider:** Hetzner CPX21 ($10/mo) or Railway ($20/mo)

**Steps:**
1. Provision VPS
2. Install Docker & Docker Compose
3. Deploy backend + PostgreSQL
4. Set up SSL with Let's Encrypt
5. Update desktop app to use `https://api.waffler.yourdomain.com`

**Docker Compose template:**
```yaml
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: waffler
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data

  backend:
    build: ./backend
    environment:
      DATABASE_URL: postgresql://waffler:${DB_PASSWORD}@postgres:5432/waffler
      JWT_SECRET_KEY: ${JWT_SECRET}
      REPLICATE_API_TOKEN: ${REPLICATE_API_TOKEN}
    ports:
      - "8000:8000"
```

**Estimated time:** 2-3 hours

---

## Testing the Full Flow

### 1. Test Auth Endpoints

```bash
# Sign up a test user
curl -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "securepassword123",
    "name": "Test User"
  }'

# Response: {"user_id":"...","api_key":"vf_...","tier":"free","email":"test@example.com","name":"Test User"}

# Sign in
curl -X POST http://localhost:8000/auth/signin \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"securepassword123"}'
```

### 2. Test LLM Styling (once Phase 2 is complete)

```bash
# Style text via backend
curl -X POST http://localhost:8000/style/style \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "transcript": "um so like I was thinking we should maybe do the thing you know",
    "prompt_style": "smart"
  }'

# Expected: {"styled_text":"I was thinking we should do the thing","usage":{...}}
```

### 3. Test Desktop App Integration (once Phase 3 is complete)

```bash
# Set backend URL
export BACKEND_URL=http://localhost:8000

# Run desktop app
python app.py

# App should:
# - Show "Self-hosted mode" in setup wizard
# - Ask for email/password instead of API keys
# - Use local whisper for transcription
# - Send styled text requests to your backend
```

---

## Files Modified

### Self-Hosted Branch Changes
- `backend/app/auth/router.py` - Python 3.9 compatibility fixes
- `backend/database/models.py` - Added LLMUsage table, GUID type
- `backend/.env.example` - Updated with SQLite and Modal/Replicate config

### Files to Create (Next Steps)
- `backend/app/style/router.py` - LLM styling endpoint
- `backend/modal_functions/style_text.py` - Modal serverless function (if using Modal)
- `src/waffler_auth_backend.py` - Backend auth client
- `docker-compose.yml` - VPS deployment config

---

## Running the Backend Locally

### Start Backend
```bash
cd /Users/james/waffler-app/backend
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Check Status
```bash
curl http://localhost:8000/health
```

### View Database
```bash
sqlite3 backend/waffler_local.db
> .tables
> SELECT * FROM users;
> .quit
```

---

## Repository Structure

```
waffler-app/  (or "waffler" repo)
├── backend/
│   ├── app/
│   │   ├── auth/        # ✅ Auth endpoints (signup, signin)
│   │   ├── usage/       # ✅ Usage tracking
│   │   ├── subscription/# ✅ Subscription management
│   │   └── style/       # ⏳ TODO: LLM styling endpoint
│   ├── database/
│   │   ├── models.py    # ✅ User, UsageLog, Subscription, LLMUsage
│   │   └── config.py    # ✅ Database connection
│   ├── .env.example     # ✅ Environment template
│   ├── init_db.py       # ✅ Database initialization
│   └── requirements.txt # ✅ Python dependencies
├── src/
│   ├── waffler_auth.py   # Current Supabase auth (keep for main branch)
│   └── waffler_auth_backend.py  # ⏳ TODO: Self-hosted auth client
└── app.py               # Desktop app entry point
```

---

## Troubleshooting

### Backend won't start
```bash
# Check if .env exists
ls -la backend/.env

# If not, copy from example
cp backend/.env.example backend/.env

# Make sure DATABASE_URL is set
cat backend/.env | grep DATABASE_URL
```

### Database errors
```bash
# Reinitialize database
cd backend
rm waffler_local.db  # Delete old database
python3 init_db.py  # Create fresh database
```

### Python 3.9 compatibility
The code has been fixed for Python 3.9, but if you see errors about `|` operator:
- Find: `str | None`
- Replace: `Optional[str]`
- Add: `from typing import Optional`

---

## Resources

- **Plan Document:** [/Users/james/.claude/plans/drifting-tinkering-galaxy.md](/Users/james/.claude/plans/drifting-tinkering-galaxy.md)
- **FastAPI Docs:** https://fastapi.tiangolo.com
- **Replicate Docs:** https://replicate.com/docs
- **Modal Docs:** https://modal.com/docs

---

## Cost Breakdown (100 users)

| Component | Cost/Month | Notes |
|-----------|------------|-------|
| VPS (Hetzner CPX21) | $10 | Backend + PostgreSQL |
| OR Railway Hobby | $20 | Easier deployment |
| Replicate LLM usage | $10-30 | ~5,000 transcriptions @ $0.002 each |
| **Total** | **$20-50** | vs $200-500 with BYOK |

**Per-user profit** (at $12/mo Pro tier):
- Revenue: $12/mo
- Cost: ~$0.50/mo (infrastructure + LLM)
- **Margin: $11.50 (96%)**

---

## Support

Questions? Check the implementation plan at `/Users/james/.claude/plans/drifting-tinkering-galaxy.md` or review this conversation.

**Last updated:** 2026-02-23
**Status:** Phase 1 complete, Phase 2-4 pending
