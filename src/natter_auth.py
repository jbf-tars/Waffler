"""
Natter — Supabase Authentication Module
Handles signup, login, session persistence, and usage tracking.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
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
    Starts a local callback server to capture tokens after OAuth completes.
    Returns {ok, url} or {ok: False, error}.

    IMPORTANT: Add http://localhost:17834/callback to your Supabase project's
    Redirect URLs (Authentication > URL Configuration) for this to work.
    """
    try:
        # Start local server to capture the OAuth callback with tokens
        _start_oauth_server()
        redirect_url = f"http://localhost:{OAUTH_CALLBACK_PORT}/callback"

        client = _get_client()
        res = client.auth.sign_in_with_oauth({
            "provider": provider,
            "options": {
                "redirect_to": redirect_url,
            }
        })
        if hasattr(res, 'url') and res.url:
            return {"ok": True, "url": res.url}
        _stop_oauth_server()
        return {"ok": False, "error": f"{provider.title()} sign-in is not configured yet"}
    except Exception as e:
        _stop_oauth_server()
        return {"ok": False, "error": str(e)}


# ── OAuth local callback server ──────────────────────────────────────
OAUTH_CALLBACK_PORT = 17834
_oauth_tokens: dict = None
_oauth_server: HTTPServer = None


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect from Supabase after Google/Apple sign-in.
    Supabase sends tokens in the URL hash (#access_token=...&refresh_token=...).
    Since hash fragments aren't sent to the server, we serve a small page that
    extracts them with JS and POSTs them back to us.
    """

    def do_GET(self):
        if '/callback' in self.path:
            html = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Natter - Sign In</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #e0e0e0;
        }
        .container {
            text-align: center;
            padding: 40px;
            max-width: 500px;
            background: rgba(255, 255, 255, 0.03);
            border-radius: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
        }
        .logo {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            margin-bottom: 32px;
        }
        .logo-icon {
            font-size: 42px;
        }
        .logo-text {
            font-size: 32px;
            font-weight: 700;
            color: #fff;
        }
        .logo-text span {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .status-icon {
            font-size: 64px;
            margin-bottom: 24px;
            animation: fadeIn 0.5s ease;
        }
        .status-title {
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 12px;
            color: #fff;
        }
        .status-message {
            font-size: 16px;
            color: #a0a0a0;
            line-height: 1.6;
            margin-bottom: 24px;
        }
        .spinner {
            width: 40px;
            height: 40px;
            margin: 24px auto;
            border: 3px solid rgba(102, 126, 234, 0.2);
            border-top: 3px solid #667eea;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        .btn {
            display: inline-block;
            padding: 12px 28px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4);
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: scale(0.8); }
            to { opacity: 1; transform: scale(1); }
        }
        .error-details {
            margin-top: 20px;
            padding: 16px;
            background: rgba(255, 69, 58, 0.1);
            border-radius: 8px;
            border: 1px solid rgba(255, 69, 58, 0.3);
            font-size: 14px;
            text-align: left;
        }
        .error-details strong {
            color: #ff453a;
            display: block;
            margin-bottom: 8px;
        }
        .error-details ul {
            list-style: none;
            padding-left: 0;
        }
        .error-details li {
            padding: 4px 0;
            padding-left: 20px;
            position: relative;
        }
        .error-details li:before {
            content: "•";
            position: absolute;
            left: 4px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <div class="logo-icon">🎙️</div>
            <div class="logo-text">Nat<span>ter</span></div>
        </div>
        <div id="content">
            <div class="spinner"></div>
            <div class="status-title">Completing sign-in...</div>
            <div class="status-message">Please wait while we verify your credentials</div>
        </div>
    </div>

    <script>
        const hash = window.location.hash.substring(1);
        const queryParams = new URLSearchParams(window.location.search);
        const authCode = queryParams.get('code');
        const content = document.getElementById('content');

        if (authCode) {
            // PKCE flow: Supabase sent an auth code in the query string
            fetch('/token', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code: authCode })
            }).then(r => r.json()).then(data => {
                if (data.ok) {
                    showSuccess();
                } else {
                    showError(
                        '❌',
                        'Sign-in Failed',
                        data.error || 'Could not exchange auth code',
                        'Try signing in again from Natter.'
                    );
                }
            }).catch(err => {
                showError(
                    '❌',
                    'Connection Error',
                    'Could not connect to Natter',
                    'Make sure Natter is running and try again.'
                );
            });
        } else if (hash) {
            // Implicit flow: tokens in the URL hash fragment
            const params = new URLSearchParams(hash);
            const data = {};
            for (const [k, v] of params) data[k] = v;

            if (data.error) {
                showError(
                    '⚠️',
                    'Authorization Error',
                    data.error_description || data.error,
                    'The sign-in was cancelled or an error occurred.'
                );
            } else {
                fetch('/token', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                }).then(() => {
                    showSuccess();
                }).catch(err => {
                    showError(
                        '❌',
                        'Connection Error',
                        'Could not connect to Natter',
                        'Make sure Natter is running and try again.'
                    );
                });
            }
        } else {
            showError(
                '❌',
                'Sign-in Failed',
                'No authentication data received',
                `This usually happens when:
                • The sign-in was cancelled
                • The redirect URL is not configured in Supabase
                • Network connectivity issues occurred

                <strong>To fix:</strong>
                Go to Supabase Dashboard → Authentication → URL Configuration
                and add <code>http://localhost:17834/callback</code> to Redirect URLs`
            );
        }

        function showSuccess() {
            content.innerHTML = `
                <div class="status-icon">✅</div>
                <div class="status-title">Signed in successfully!</div>
                <div class="status-message">
                    You can close this tab and return to Natter.
                </div>
            `;
        }

        function showError(icon, title, message, details) {
            content.innerHTML = `
                <div class="status-icon">${icon}</div>
                <div class="status-title">${title}</div>
                <div class="status-message">${message}</div>
                <div class="error-details">
                    <strong>Troubleshooting:</strong>
                    ${details}
                </div>
                <div style="margin-top: 24px;">
                    <a href="#" class="btn" onclick="window.close(); return false;">Close Tab</a>
                </div>
            `;
        }
    </script>
</body>
</html>
            '''
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(html.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global _oauth_tokens
        if self.path == '/token':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except Exception:
                data = {}

            # PKCE flow: exchange the auth code for a session
            if 'code' in data and 'access_token' not in data:
                try:
                    client = _get_client()
                    res = client.auth.exchange_code_for_session({"auth_code": data["code"]})
                    if res.user and res.session:
                        _oauth_tokens = {
                            "access_token": res.session.access_token,
                            "refresh_token": res.session.refresh_token,
                        }
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": True}).encode())
                        return
                    else:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"ok": False, "error": "Code exchange failed"}).encode())
                        return
                except Exception as e:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
                    return

            # Implicit flow: tokens already provided
            _oauth_tokens = data
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'ok')

    def log_message(self, format, *args):
        pass  # Suppress HTTP log noise


def _start_oauth_server():
    """Start the local HTTP server that captures the OAuth callback."""
    global _oauth_server, _oauth_tokens
    _oauth_tokens = None
    _stop_oauth_server()
    try:
        server = HTTPServer(('127.0.0.1', OAUTH_CALLBACK_PORT), _OAuthCallbackHandler)
        _oauth_server = server
        t = threading.Thread(target=server.serve_forever, daemon=True, name="OAuthServer")
        t.start()
    except Exception:
        pass


def _stop_oauth_server():
    """Stop the local OAuth callback server."""
    global _oauth_server
    if _oauth_server:
        try:
            _oauth_server.shutdown()
        except Exception:
            pass
        _oauth_server = None


def poll_oauth_result() -> dict:
    """Check if the OAuth callback has been received.
    If tokens were captured, set the Supabase session and return the user.
    Returns {ok: True, user: {...}} on success,
            {ok: False, pending: True} if still waiting,
            {ok: False, error: "..."} on failure.
    """
    global _oauth_tokens, _user, _session

    if not _oauth_tokens:
        return {"ok": False, "pending": True}

    access_token = _oauth_tokens.get('access_token', '')
    refresh_token = _oauth_tokens.get('refresh_token', '')
    _oauth_tokens = None

    if not access_token:
        _stop_oauth_server()
        return {"ok": False, "error": "No access token received"}

    try:
        client = _get_client()
        res = client.auth.set_session(access_token, refresh_token)
        if res.user:
            _user = {"id": str(res.user.id), "email": res.user.email}
            _session = {
                "access_token": res.session.access_token if res.session else access_token,
                "refresh_token": res.session.refresh_token if res.session else refresh_token,
            }
            _save_session(_session)
            _stop_oauth_server()
            return {"ok": True, "user": _user}
    except Exception as e:
        _stop_oauth_server()
        return {"ok": False, "error": f"Session error: {e}"}

    _stop_oauth_server()
    return {"ok": False, "error": "Failed to complete sign-in"}


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
