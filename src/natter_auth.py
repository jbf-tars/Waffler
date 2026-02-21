"""
Natter — Supabase Authentication Module
Handles signup, login, session persistence, and usage tracking.
"""
from __future__ import annotations

import json
from pathlib import Path
from supabase import create_client, Client

# ── Supabase config ───────────────────────────────────────────────────
SUPABASE_URL = "https://hbgjxqxbdrhoxtmbvqeu.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_Fb2idee5aJVSDM98BmJ1Jg_86NGh5V1"

# Local session file
SESSION_FILE = Path.home() / ".natter" / "session.json"

# ── Module state ──────────────────────────────────────────────────────
_client: Client = None
_user: dict = None
_session: dict = None


def _get_client() -> Client:
    """Lazy-init the Supabase client."""
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _client


def _save_session(session_data: dict):
    """Persist session to disk for auto-login."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(session_data, f)


def _load_session() -> dict | None:
    """Load persisted session from disk."""
    if not SESSION_FILE.exists():
        return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _clear_session():
    """Delete persisted session."""
    try:
        SESSION_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────

def sign_up(email: str, password: str) -> dict:
    """Create a new account. Returns {ok, user, error}."""
    try:
        client = _get_client()
        res = client.auth.sign_up({"email": email, "password": password})

        if res.user:
            global _user, _session
            _user = {"id": str(res.user.id), "email": res.user.email}

            if res.session:
                _session = {
                    "access_token": res.session.access_token,
                    "refresh_token": res.session.refresh_token,
                }
                _save_session(_session)
                return {"ok": True, "user": _user, "needs_confirm": False}
            else:
                # Email confirmation required
                return {"ok": True, "user": _user, "needs_confirm": True}

        return {"ok": False, "error": "Signup failed — please try again"}
    except Exception as e:
        err = str(e)
        if "already registered" in err.lower() or "already been registered" in err.lower():
            return {"ok": False, "error": "This email is already registered. Try logging in."}
        return {"ok": False, "error": err}


def sign_in(email: str, password: str) -> dict:
    """Log in with email + password. Returns {ok, user, error}."""
    try:
        client = _get_client()
        res = client.auth.sign_in_with_password({"email": email, "password": password})

        if res.user and res.session:
            global _user, _session
            _user = {"id": str(res.user.id), "email": res.user.email}
            _session = {
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
            }
            _save_session(_session)
            return {"ok": True, "user": _user}

        return {"ok": False, "error": "Login failed — check your credentials"}
    except Exception as e:
        err = str(e)
        if "invalid" in err.lower() or "credentials" in err.lower():
            return {"ok": False, "error": "Invalid email or password"}
        if "not confirmed" in err.lower():
            return {"ok": False, "error": "Check your email to confirm your account first"}
        return {"ok": False, "error": err}


def sign_out() -> dict:
    """Sign out and clear session."""
    global _user, _session
    try:
        client = _get_client()
        client.auth.sign_out()
    except Exception:
        pass
    _user = None
    _session = None
    _clear_session()
    return {"ok": True}


def restore_session() -> dict:
    """Try to restore a saved session (auto-login). Returns {ok, user}."""
    global _user, _session
    saved = _load_session()
    if not saved or "refresh_token" not in saved:
        return {"ok": False}

    try:
        client = _get_client()
        res = client.auth.refresh_session(saved["refresh_token"])

        if res.user and res.session:
            _user = {"id": str(res.user.id), "email": res.user.email}
            _session = {
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
            }
            _save_session(_session)
            return {"ok": True, "user": _user}
    except Exception:
        pass

    _clear_session()
    return {"ok": False}


def get_current_user() -> dict | None:
    """Return the currently logged-in user, or None."""
    return _user


def is_logged_in() -> bool:
    """Check if a user is currently authenticated."""
    return _user is not None


def get_user_id() -> str | None:
    """Return the current user's UUID."""
    return _user["id"] if _user else None


def get_oauth_url(provider: str) -> dict:
    """Get the OAuth sign-in URL for a provider (google, apple).
    Returns {ok, url} or {ok: False, error}."""
    try:
        client = _get_client()
        # Use PKCE flow — returns a URL to open in the browser
        res = client.auth.sign_in_with_oauth({
            "provider": provider,
            "options": {
                "redirect_to": f"{SUPABASE_URL}/auth/v1/callback",
            }
        })
        if hasattr(res, 'url') and res.url:
            return {"ok": True, "url": res.url}
        return {"ok": False, "error": f"{provider.title()} sign-in is not configured yet"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def increment_usage(audio_seconds: float):
    """Record a transcription in the usage table."""
    if not _session or not _user:
        return
    try:
        client = _get_client()
        client.rpc("increment_usage", {
            "p_user_id": _user["id"],
            "p_audio_seconds": audio_seconds,
        }).execute()
    except Exception as e:
        print(f"[supabase] Usage tracking error: {e}")


def get_profile() -> dict | None:
    """Fetch the current user's profile."""
    if not _user:
        return None
    try:
        client = _get_client()
        res = client.table("profiles").select("*").eq("id", _user["id"]).single().execute()
        return res.data
    except Exception:
        return None


def get_usage_today() -> dict:
    """Get today's usage for the current user."""
    if not _user:
        return {"transcription_count": 0, "total_audio_seconds": 0}
    try:
        from datetime import date
        client = _get_client()
        res = (client.table("usage")
               .select("*")
               .eq("user_id", _user["id"])
               .eq("date", date.today().isoformat())
               .single()
               .execute())
        return res.data or {"transcription_count": 0, "total_audio_seconds": 0}
    except Exception:
        return {"transcription_count": 0, "total_audio_seconds": 0}
