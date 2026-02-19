# Week 2: Account System & Backend Design

## 🎯 Objectives

1. ✅ Core app working with voice input (Week 1 - pending test)
2. 📋 Account system for tracking usage
3. 💳 Subscription tiers (Free/Pro)
4. 🔑 API key management for frontend
5. 📊 Usage tracking & enforcement

---

## 🏗️ Architecture Overview

```
Frontend (macOS .app)
├─ UI: Exists (hotkey + clipboard)
├─ Auth: Login screen (new)
├─ Storage: SQLite cache + Keychain (new)
└─ API: HTTPS calls to backend

Backend (FastAPI)
├─ Auth: Email/password + Google OAuth
├─ Database: PostgreSQL (user accounts, usage logs)
├─ API: REST endpoints for signup, login, usage tracking
├─ Billing: Stripe integration for payments
└─ Monitoring: Usage limits enforcement

Cloud (Railway/Fly.io)
├─ Hostname: api.voiceflow.app
├─ SSL: Auto (provided by platforms)
└─ Cost: ~$10-15/month

```

---

## 📊 Data Models

### Users Table
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR UNIQUE NOT NULL,
    name VARCHAR,
    password_hash VARCHAR,
    tier VARCHAR DEFAULT 'free',  -- 'free' | 'pro'
    stripe_customer_id VARCHAR,
    api_key VARCHAR UNIQUE,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

### Usage Log Table
```sql
CREATE TABLE usage_logs (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users,
    timestamp TIMESTAMP,
    words_used INT,
    characters_used INT,
    transcript VARCHAR,
    success BOOL
);
```

### Subscription Table
```sql
CREATE TABLE subscriptions (
    id UUID PRIMARY KEY,
    user_id UUID UNIQUE REFERENCES users,
    stripe_subscription_id VARCHAR,
    status VARCHAR,  -- 'active' | 'cancelled'
    current_period_start TIMESTAMP,
    current_period_end TIMESTAMP,
    canceled_at TIMESTAMP
);
```

---

## 🔌 API Endpoints

### Authentication
```
POST /auth/signup
  Body: { email, password, name }
  Response: { user_id, api_key, tier }

POST /auth/signin
  Body: { email, password }
  Response: { user_id, api_key, tier }

POST /auth/oauth
  Body: { provider, token }
  Response: { user_id, api_key, tier }

POST /auth/refresh
  Headers: { Authorization: Bearer api_key }
  Response: { new_api_key }
```

### Usage Tracking
```
POST /usage/log
  Headers: { Authorization: Bearer api_key }
  Body: { words_used, characters_used }
  Response: { remaining_quota, reset_date }

GET /usage/quota
  Headers: { Authorization: Bearer api_key }
  Response: { words_used, words_limit, reset_date }
```

### Subscription Management
```
GET /subscription
  Headers: { Authorization: Bearer api_key }
  Response: { tier, status, period_end, manage_url }

POST /subscription/upgrade
  Headers: { Authorization: Bearer api_key }
  Response: { stripe_session_id, checkout_url }

POST /subscription/cancel
  Headers: { Authorization: Bearer api_key }
  Response: { confirmation }
```

---

## 💾 Frontend Changes (macOS App)

### New Files
- `src/auth.py` - Login/logout logic
- `src/storage.py` - SQLite cache management
- `src/keychain.py` - macOS Keychain integration (secure token storage)
- `ui/login.py` - Login UI (tkinter or custom)

### Modified Files
- `config.py` - Add backend URL config
- `main.py` - Wrap with auth check before hotkey
- `style.py` - Include API key in requests

### Flow Changes
```
1. App starts
2. Check if logged in (token in Keychain)
3. If not: Show login dialog
4. User enters email/password
5. Get API key from backend
6. Store in Keychain
7. Start hotkey listener
8. On recording: Send to backend with API key
9. Backend logs usage
10. Return result
```

---

## 🔐 Security Considerations

### Credentials Management
- **API Keys:** Stored in macOS Keychain (encrypted)
- **Passwords:** Never stored locally, only on backend (hashed)
- **HTTPS:** All backend communication encrypted
- **Refresh tokens:** 24-hour expiry with refresh endpoint

### Usage Enforcement
```python
# Backend check before transcription
if user.tier == 'free':
    weekly_words = get_weekly_usage(user_id)
    if weekly_words >= 2000:
        return 429, "Weekly quota exceeded"

# Log usage
log_usage(user_id, word_count=len(transcript.split()))
```

### Rate Limiting
```
Free tier: 
  - 2,000 words/week
  - 5 requests/minute

Pro tier:
  - Unlimited
  - 100 requests/minute
```

---

## 💳 Billing (Stripe Integration)

### Pricing
```
Free: $0/month
  - 2,000 words/week
  - 7-day Pro trial included

Pro: $9/month
  - Unlimited usage
  - Same-day support (future)
```

### Stripe Setup
1. Create Stripe account
2. Define price: $9/month
3. Set up webhook for payment events
4. Handle: customer.subscription.created, updated, deleted

---

## 🚀 Deployment Plan

### Backend (FastAPI)
```
1. Create new repo: voiceflow-backend
2. Structure:
   ├─ app/
   │  ├─ main.py (FastAPI app)
   │  ├─ auth/
   │  ├─ usage/
   │  ├─ subscription/
   │  └─ models.py
   ├─ database/
   │  └─ postgresql.py
   ├─ services/
   │  ├─ stripe.py
   │  └─ auth.py
   └─ requirements.txt

3. Deploy to Railway:
   - Connect GitHub repo
   - Set environment variables
   - Deploy on commit
   - Auto SSL certificate
```

### Database (PostgreSQL)
```
Option 1: Railway's built-in PostgreSQL
  - 5GB free, then $5/month
  - Easy backups
  - Auto SSL

Option 2: Render PostgreSQL
  - Similar pricing
  - Different UI
```

### Frontend (macOS App)
```
1. Add auth.py module
2. Create login UI (simple tkinter dialog)
3. Store tokens in Keychain
4. Test with TestFlight distribution (if needed)
5. Build .dmg installer
```

---

## 📅 Implementation Timeline

### Week 2, Day 1-2: Backend Setup
- [ ] Create FastAPI project structure
- [ ] Set up PostgreSQL database (local first)
- [ ] Implement auth endpoints (signup, signin, refresh)
- [ ] Add usage tracking endpoints
- [ ] Test endpoints locally with Postman

### Week 2, Day 3-4: Stripe Integration
- [ ] Set up Stripe account
- [ ] Add subscription management endpoints
- [ ] Implement webhook handling
- [ ] Test payment flow

### Week 2, Day 5: Frontend Auth
- [ ] Create login UI
- [ ] Integrate Keychain storage
- [ ] Test login flow
- [ ] Test usage quota enforcement

### Week 2, Day 6-7: Testing & Deployment
- [ ] E2E testing (signup → record → usage tracking)
- [ ] Deploy backend to Railway
- [ ] Update frontend to use production API
- [ ] Test full flow with live backend

---

## 🎯 Success Criteria

✅ Backend runs locally (FastAPI)
✅ Database migrations work
✅ Auth endpoints functional (email/password)
✅ Usage tracking works
✅ Stripe payments process
✅ Frontend can login and save token
✅ Usage quota enforced
✅ Full E2E test passes

---

## 📚 Technologies

**Backend:**
- FastAPI (Python web framework)
- PostgreSQL (relational database)
- SQLAlchemy (ORM)
- Alembic (database migrations)
- PyJWT (JWT tokens)
- Stripe Python SDK

**Deployment:**
- Railway.app or Fly.io
- GitHub Actions (CI/CD)
- Docker (containerization)

**Frontend Updates:**
- tkinter (simple login dialog)
- keyring (macOS Keychain)
- requests (HTTP client)

---

## 💡 Notes

- **OAuth:** Consider adding Google Sign-In for faster onboarding
- **Password reset:** Implement email-based password reset
- **Free trial:** All users get 7-day Pro trial
- **Analytics:** Track which commands are most used
- **Future:** Add voice model training on user data (with consent)

---

**Ready to start Week 2 after voice testing passes!** 🚀
