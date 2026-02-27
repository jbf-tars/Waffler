"""
Natter — Self-Hosted Backend Authentication Module
Handles signup, login, and session persistence with your own backend.
"""
from __future__ import annotations

import json
import os
import requests
from pathlib import Path
from typing import Optional

# Backend URL from environment or default to localhost
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# Local session file
SESSION_FILE = Path.home() / ".natter" / "session_backend.json"

# Module state
_user: Optional[dict] = None
_api_key: Optional[str] = None


def _save_session(user: dict, api_key: str):
    """Persist session to disk for auto-login."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump({"user": user, "api_key": api_key}, f)


def _load_session() -> Optional[dict]:
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

def sign_up(email: str, password: str, name: str = "") -> dict:
    """Create a new account on the backend. Returns {ok, user, api_key, error}."""
    try:
        response = requests.post(
            f"{BACKEND_URL}/auth/signup",
            json={"email": email, "password": password, "name": name or None},
            timeout=10
        )

        if response.status_code == 201:
            data = response.json()
            global _user, _api_key
            _user = {
                "id": data["user_id"],
                "email": data["email"],
                "name": data.get("name"),
                "tier": data.get("tier", "free")
            }
            _api_key = data["api_key"]
            _save_session(_user, _api_key)
            return {"ok": True, "user": _user, "api_key": _api_key}
        elif response.status_code == 400:
            error = response.json().get("detail", "Signup failed")
            if "already registered" in error.lower():
                return {"ok": False, "error": "This email is already registered. Try logging in."}
            return {"ok": False, "error": error}
        else:
            return {"ok": False, "error": f"Server error: {response.status_code}"}

    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "Cannot connect to backend. Make sure the backend is running."}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "Backend request timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def sign_in(email: str, password: str) -> dict:
    """Log in with email + password. Returns {ok, user, api_key, error}."""
    try:
        response = requests.post(
            f"{BACKEND_URL}/auth/signin",
            json={"email": email, "password": password},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            global _user, _api_key
            _user = {
                "id": data["user_id"],
                "email": data["email"],
                "name": data.get("name"),
                "tier": data.get("tier", "free")
            }
            _api_key = data["api_key"]
            _save_session(_user, _api_key)
            return {"ok": True, "user": _user, "api_key": _api_key}
        elif response.status_code == 401:
            return {"ok": False, "error": "Invalid email or password"}
        else:
            error = response.json().get("detail", "Login failed")
            return {"ok": False, "error": error}

    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "Cannot connect to backend. Make sure the backend is running."}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "Backend request timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def sign_out() -> dict:
    """Sign out and clear session."""
    global _user, _api_key
    _user = None
    _api_key = None
    _clear_session()
    return {"ok": True}


def restore_session() -> dict:
    """Try to restore a saved session (auto-login). Returns {ok, user, api_key}."""
    global _user, _api_key
    saved = _load_session()
    if not saved or "api_key" not in saved:
        return {"ok": False}

    # Validate the API key by checking quota
    try:
        response = requests.get(
            f"{BACKEND_URL}/style/quota",
            params={"api_key": saved["api_key"]},
            timeout=5
        )

        if response.status_code == 200:
            _user = saved["user"]
            _api_key = saved["api_key"]
            return {"ok": True, "user": _user, "api_key": _api_key}
    except Exception:
        pass

    _clear_session()
    return {"ok": False}


def get_current_user() -> Optional[dict]:
    """Return the currently logged-in user, or None."""
    return _user


def is_logged_in() -> bool:
    """Check if a user is currently authenticated."""
    return _user is not None and _api_key is not None


def get_user_id() -> Optional[str]:
    """Return the current user's UUID."""
    return _user["id"] if _user else None


def get_api_key() -> Optional[str]:
    """Return the current user's API key for backend requests."""
    return _api_key


def get_quota_status() -> dict:
    """Get user's current quota status. Returns {tier, quota, used, remaining, error}."""
    if not _api_key:
        return {"error": "Not authenticated"}

    try:
        response = requests.get(
            f"{BACKEND_URL}/style/quota",
            params={"api_key": _api_key},
            timeout=5
        )

        if response.status_code == 200:
            return response.json()
        else:
            error = response.json().get("detail", "Failed to get quota")
            return {"error": error}

    except requests.exceptions.ConnectionError:
        return {"error": "Cannot connect to backend"}
    except Exception as e:
        return {"error": str(e)}


def check_backend_health() -> bool:
    """Check if the backend is reachable and healthy."""
    try:
        response = requests.get(f"{BACKEND_URL}/health", timeout=3)
        return response.status_code == 200
    except Exception:
        return False
