# Waffler Self-Hosted Backend - PC Setup Guide

## Quick Setup on Windows PC

### Prerequisites
- Git installed
- Python 3.9+ installed
- Waffler app source code

---

## Step 1: Pull Latest Code

```bash
# Navigate to your Waffler directory
cd C:\path\to\waffler

# Fetch latest from GitHub
git fetch origin

# Switch to self-hosted branch
git checkout self-hosted

# Pull latest changes
git pull origin self-hosted
```

---

## Step 2: Set Up Backend

### Create .env file

Create `backend\.env` with this content:

```bash
# Local Development Environment
DATABASE_URL=sqlite:///./waffler_pc.db
JWT_SECRET_KEY=dev-secret-change-in-production-12345

# Replicate API token (get from replicate.com/account)
REPLICATE_API_TOKEN=your_replicate_token_here
```

### Install Python Dependencies

```bash
cd backend
pip install -r requirements.txt
```

**If you get errors**, try:
```bash
pip install --upgrade pip
pip install -r requirements.txt --no-cache-dir
```

### Initialize Database

```bash
python init_db.py
```

You should see:
```
✓ Database initialized successfully
✓ Tables created: users, usage_log, subscriptions, llm_usage
```

---

## Step 3: Start Backend Server

```bash
# From backend/ directory
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
```

**Leave this terminal open!** The backend needs to keep running.

---

## Step 4: Test Backend (New Terminal)

Open a new terminal/command prompt:

```bash
# From project root
python test_backend_integration.py
```

Expected output:
```
✓ Backend is healthy
✓ Authenticated as test@example.com
✓ Quota: 0/20 used, 20 remaining
✓ Backend styling activated
✓ Styled text: "I was thinking we should do the thing"
✅ All tests passed!
```

If you get "Insufficient credit" error, wait 5-10 minutes for Replicate billing to activate.

---

## Step 5: Configure Desktop App

### Create .env file in project root

Create `.env` in the root directory (same level as `app.py`):

```bash
# Self-Hosted Backend
BACKEND_URL=http://localhost:8000

# Local Whisper transcription
LOCAL_WHISPER=1

# Prompt style
PROMPT_STYLE=smart
```

---

## Step 6: Launch Waffler

```bash
# Run the app (from project root)
python app.py
```

The app should:
1. Detect the backend at localhost:8000
2. Show signup/signin screen (backend mode)
3. No API key prompts!

---

## Testing the Full Flow

1. **Sign up** with email/password in the app
2. **Complete setup** (permissions, hotkey, etc.)
3. **Press hotkey** to record
4. **Say:** "um like I was thinking we should maybe do the thing you know"
5. **Expected result:** "I was thinking we should do the thing" (cleaned up!)

---

## Troubleshooting

### Backend won't start

**Error: Port 8000 already in use**
```bash
# Find process using port 8000
netstat -ano | findstr :8000

# Kill it (use PID from above)
taskkill /F /PID <process_id>
```

**Error: Module not found**
```bash
# Make sure you're in backend/ directory
cd backend
pip install -r requirements.txt
```

### App doesn't detect backend

Check:
1. Backend is running (http://localhost:8000/health should respond)
2. `.env` file exists in root with `BACKEND_URL=http://localhost:8000`
3. Restart the app

### Replicate "Insufficient credit"

Wait 5-10 minutes after adding billing. Replicate's system needs time to process.

To check if it's working:
```bash
python test_backend_integration.py
```

---

## Architecture

```
Windows PC
├── Backend (Python FastAPI)
│   ├── Port: 8000
│   ├── Database: SQLite (waffler_pc.db)
│   └── LLM: Replicate API
│
└── Desktop App
    ├── Whisper: Local (faster-whisper)
    ├── Auth: Backend (email/password)
    └── Styling: Backend → Replicate
```

---

## What's Different from BYOK?

**BYOK (main branch):**
- Users provide Groq/OpenAI API keys
- Direct API calls from desktop app
- Unlimited usage (if you have keys)

**Self-Hosted (self-hosted branch):**
- No API keys needed
- Backend handles everything
- Quota system (free=20, plus=100, pro=500)
- You pay for LLM usage (~$0.002/transcription)

---

## Cost Breakdown

**Per transcription:** ~$0.002
**100 users × 50 transcriptions/month:** ~$10/month LLM costs
**VPS (when deployed):** ~$10/month
**Total:** ~$20/month for 100 users

**Revenue (if charging $5/month):** $500/month
**Profit:** ~$480/month (96% margin)

---

## Next Steps

Once everything works locally:

1. **Deploy to VPS** (Hetzner, Railway, DigitalOcean)
2. **Update BACKEND_URL** to production URL
3. **Set up SSL** with Let's Encrypt
4. **Build Windows app** with production backend
5. **Release!**

---

## Support

If you run into issues:
1. Check backend logs (terminal where uvicorn is running)
2. Test endpoint: `curl http://localhost:8000/health`
3. Verify .env files exist and have correct values
4. Make sure both backend and app are on `self-hosted` branch

---

**Last updated:** 2026-02-28
