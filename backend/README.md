# VoiceFlow Backend API

FastAPI backend for account management, usage tracking, and billing.

## 🎯 Features

- ✅ **Authentication:** Signup, signin, API key management
- ✅ **Usage Tracking:** Log words/characters used, check quota
- ✅ **Subscription Management:** Free/Pro tiers (Stripe integration TODO)
- ✅ **PostgreSQL:** Relational database with SQLAlchemy ORM
- ✅ **JWT:** Secure token-based authentication

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Setup PostgreSQL

**Option A: Local PostgreSQL**
```bash
# Install PostgreSQL (macOS)
brew install postgresql
brew services start postgresql

# Create database
createdb voiceflow

# Create user
psql -c "CREATE USER voiceflow WITH PASSWORD 'voiceflow';"
psql -c "GRANT ALL PRIVILEGES ON DATABASE voiceflow TO voiceflow;"
```

**Option B: Docker**
```bash
docker run -d \
  --name voiceflow-postgres \
  -e POSTGRES_USER=voiceflow \
  -e POSTGRES_PASSWORD=voiceflow \
  -e POSTGRES_DB=voiceflow \
  -p 5432:5432 \
  postgres:15
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 4. Initialize Database

```bash
python init_db.py
```

### 5. Run Server

```bash
uvicorn app.main:app --reload
```

Server runs at: http://localhost:8000

---

## 📚 API Documentation

### Interactive Docs
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

### Endpoints

#### Authentication

**POST /auth/signup**
```json
{
  "email": "user@example.com",
  "password": "secure_password",
  "name": "John Doe"
}
```
Response:
```json
{
  "user_id": "uuid",
  "api_key": "vf_...",
  "tier": "free",
  "email": "user@example.com",
  "name": "John Doe"
}
```

**POST /auth/signin**
```json
{
  "email": "user@example.com",
  "password": "secure_password"
}
```

**POST /auth/refresh**
```json
{
  "current_api_key": "vf_..."
}
```

---

#### Usage Tracking

**POST /usage/log**
Headers:
```
Authorization: Bearer vf_your_api_key
```
Body:
```json
{
  "words_used": 150,
  "characters_used": 750,
  "transcript": "optional transcript"
}
```

Response:
```json
{
  "words_used": 150,
  "words_limit": 2000,
  "reset_date": "2026-02-17T00:00:00",
  "percentage_used": 7.5
}
```

**GET /usage/quota**
Headers:
```
Authorization: Bearer vf_your_api_key
```

---

#### Subscription

**GET /subscription/**
Headers:
```
Authorization: Bearer vf_your_api_key
```

Response:
```json
{
  "tier": "free",
  "status": null,
  "period_end": null
}
```

**POST /subscription/upgrade**
**POST /subscription/cancel**

---

## 🗄️ Database Schema

### Users Table
```sql
- id (UUID, primary key)
- email (unique, indexed)
- name
- password_hash
- tier (free/pro)
- stripe_customer_id
- api_key (unique, indexed)
- created_at
- updated_at
```

### Usage Logs Table
```sql
- id (UUID, primary key)
- user_id (foreign key, indexed)
- timestamp (indexed)
- words_used
- characters_used
- transcript
- success
```

### Subscriptions Table
```sql
- id (UUID, primary key)
- user_id (foreign key, unique)
- stripe_subscription_id
- status
- current_period_start
- current_period_end
- canceled_at
- created_at
- updated_at
```

---

## 🧪 Testing

### Manual Testing with curl

**Signup:**
```bash
curl -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"test123","name":"Test User"}'
```

**Log Usage:**
```bash
curl -X POST http://localhost:8000/usage/log \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer vf_YOUR_API_KEY" \
  -d '{"words_used":100,"characters_used":500}'
```

**Check Quota:**
```bash
curl http://localhost:8000/usage/quota \
  -H "Authorization: Bearer vf_YOUR_API_KEY"
```

---

## 🚀 Deployment

### Railway (Recommended)

1. **Create account:** https://railway.app
2. **Create new project** → Deploy from GitHub
3. **Add PostgreSQL** → Add service → PostgreSQL
4. **Set environment variables:**
   - `DATABASE_URL` (auto-set by Railway)
   - `JWT_SECRET_KEY`
5. **Deploy!**

Cost: ~$10-15/month

### Fly.io (Alternative)

Similar process, slightly different UI.

---

## 📊 Tier Limits

| Tier | Weekly Word Limit | Price |
|------|------------------|-------|
| Free | 2,000 words | $0 |
| Pro  | Unlimited | $9/month |

---

## 🔐 Security

- ✅ Passwords hashed with bcrypt
- ✅ API keys stored securely
- ✅ JWT tokens with expiry
- ✅ Rate limiting (TODO)
- ✅ Input validation (Pydantic)

---

## 📝 TODO

- [ ] Stripe integration for payments
- [ ] Email verification
- [ ] Password reset
- [ ] OAuth (Google Sign-In)
- [ ] Rate limiting
- [ ] Admin dashboard
- [ ] Usage analytics

---

## 🛠️ Development

**Run with auto-reload:**
```bash
uvicorn app.main:app --reload --port 8000
```

**Check database:**
```bash
psql -U voiceflow -d voiceflow
\dt  # List tables
\d users  # Describe users table
```

**Reset database:**
```bash
DROP DATABASE voiceflow;
CREATE DATABASE voiceflow;
python init_db.py
```

---

**Status:** Week 2 backend foundation complete! 🎉
