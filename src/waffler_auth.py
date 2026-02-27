"""
Waffler — Supabase Authentication Module
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
SESSION_FILE = Path.home() / ".waffler-hosted" / "session.json"

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
    print(f"OAuth: get_oauth_url() called for provider={provider}")
    try:
        # Start local server to capture the OAuth callback with tokens
        _start_oauth_server()
        redirect_url = f"http://localhost:{OAUTH_CALLBACK_PORT}/callback"
        print(f"OAuth: Redirect URL set to {redirect_url}")

        client = _get_client()
        print(f"OAuth: Calling Supabase sign_in_with_oauth...")
        res = client.auth.sign_in_with_oauth({
            "provider": provider,
            "options": {
                "redirect_to": redirect_url,
            }
        })
        if hasattr(res, 'url') and res.url:
            print(f"OAuth: ✓ Got OAuth URL from Supabase: {res.url[:80]}...")
            return {"ok": True, "url": res.url}
        print(f"OAuth: ✗ No URL returned from Supabase")
        _stop_oauth_server()
        return {"ok": False, "error": f"{provider.title()} sign-in is not configured yet"}
    except Exception as e:
        print(f"OAuth: ✗ Exception in get_oauth_url: {e}")
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
    <title>Waffler - Sign In</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif;
            background: #FFFDF5;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #1A1A1A;
            -webkit-font-smoothing: antialiased;
        }
        .container {
            text-align: center;
            padding: 56px 48px;
            max-width: 460px;
            background: #1A1A1A;
            border-radius: 16px;
            box-shadow: 0 4px 24px rgba(0, 0, 0, 0.12), 0 0 0 1px rgba(255, 255, 255, 0.05);
        }
        .logo {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            margin-bottom: 40px;
            opacity: 0.9;
        }
        .logo-icon {
            font-size: 32px;
        }
        .logo-text {
            font-size: 28px;
            font-weight: 650;
            letter-spacing: -0.03em;
            color: #F5F5F0;
        }
        .logo-text span {
            color: #6D3BF5;
        }
        .status-icon {
            margin-bottom: 28px;
            opacity: 0;
            animation: fadeInScale 0.6s cubic-bezier(0.22, 1, 0.36, 1) forwards;
        }
        .success-check {
            filter: drop-shadow(0 4px 16px rgba(109, 59, 245, 0.2));
        }
        .check-circle {
            stroke-dasharray: 240;
            stroke-dashoffset: 240;
            animation: drawCircle 0.6s cubic-bezier(0.22, 1, 0.36, 1) 0.2s forwards;
        }
        .check-path {
            stroke-dasharray: 60;
            stroke-dashoffset: 60;
            animation: drawCheck 0.4s cubic-bezier(0.22, 1, 0.36, 1) 0.6s forwards;
        }
        .status-title {
            font-size: 24px;
            font-weight: 600;
            margin-bottom: 12px;
            color: #F5F5F0;
            letter-spacing: -0.02em;
        }
        .status-message {
            font-size: 14px;
            color: #A09890;
            line-height: 1.6;
            margin-bottom: 20px;
            font-weight: 400;
        }
        .spinner {
            width: 44px;
            height: 44px;
            margin: 24px auto;
            border: 3px solid rgba(109, 59, 245, 0.15);
            border-top: 3px solid #6D3BF5;
            border-radius: 50%;
            animation: spin 1s cubic-bezier(0.68, -0.55, 0.27, 1.55) infinite;
        }
        .btn {
            display: inline-block;
            padding: 12px 28px;
            background: #6D3BF5;
            color: white;
            text-decoration: none;
            border-radius: 10px;
            font-weight: 600;
            font-size: 15px;
            transition: all 0.3s cubic-bezier(0.22, 1, 0.36, 1);
            border: none;
            cursor: pointer;
        }
        .btn:hover {
            background: #5B2ED4;
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(109, 59, 245, 0.35);
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(8px) scale(0.96); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes fadeInScale {
            from { opacity: 0; transform: scale(0.9); }
            to { opacity: 1; transform: scale(1); }
        }
        @keyframes drawCircle {
            to { stroke-dashoffset: 0; }
        }
        @keyframes drawCheck {
            to { stroke-dashoffset: 0; }
        }
        .error-details {
            margin-top: 24px;
            padding: 18px;
            background: rgba(240, 192, 80, 0.08);
            border-radius: 12px;
            border: 1px solid rgba(240, 192, 80, 0.25);
            font-size: 14px;
            text-align: left;
            color: #F0EDE6;
        }
        .error-details strong {
            color: #F0C050;
            display: block;
            margin-bottom: 10px;
            font-weight: 600;
        }
        .error-details ul {
            list-style: none;
            padding-left: 0;
        }
        .error-details li {
            padding: 5px 0;
            padding-left: 20px;
            position: relative;
            color: #A09890;
            line-height: 1.5;
        }
        .error-details li:before {
            content: "•";
            position: absolute;
            left: 4px;
            color: #F0C050;
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
        const search = window.location.search.substring(1);
        const content = document.getElementById('content');

        // Check for query parameters first (authorization code flow)
        const queryParams = new URLSearchParams(search);
        const authCode = queryParams.get('code');
        const error = queryParams.get('error');
        const errorDesc = queryParams.get('error_description');

        if (error) {
            // OAuth error in query params
            showError(
                '⚠️',
                'Authorization Error',
                errorDesc || error,
                'The sign-in was cancelled or an error occurred.'
            );
        } else if (authCode) {
            // Authorization code flow - send code to backend to exchange for tokens
            fetch('/token', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code: authCode })
            }).then(() => {
                showSuccess();
            }).catch(err => {
                showError(
                    '❌',
                    'Connection Error',
                    'Could not connect to Waffler',
                    'Make sure Waffler is running and try again.'
                );
            });
        } else if (hash) {
            // Implicit flow - has tokens in hash - process them
            const params = new URLSearchParams(hash);
            const data = {};
            for (const [k, v] of params) data[k] = v;

            // Check for error in the hash
            if (data.error) {
                showError(
                    '⚠️',
                    'Authorization Error',
                    data.error_description || data.error,
                    'The sign-in was cancelled or an error occurred.'
                );
            } else {
                // Send tokens to Python backend
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
                        'Could not connect to Waffler',
                        'Make sure Waffler is running and try again.'
                    );
                });
            }
        } else {
            // No parameters at all = OAuth flow failed before redirect
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
                <svg class="status-icon success-check" width="80" height="80" viewBox="0 0 80 80">
                    <circle class="check-circle" cx="40" cy="40" r="38" fill="none" stroke="#6D3BF5" stroke-width="3"/>
                    <path class="check-path" d="M 25 40 L 35 50 L 55 30" fill="none" stroke="#6D3BF5" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                <div class="status-title">Authentication complete</div>
                <div class="status-message">
                    You're all set. Return to Waffler to continue.
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
            print(f"OAuth: /token POST received, body length={length}")
            try:
                data = json.loads(body)
                print(f"OAuth: Data received: {list(data.keys())}")

                # Check if this is an authorization code that needs to be exchanged
                if 'code' in data and 'access_token' not in data:
                    print(f"OAuth: Got authorization code, exchanging for tokens...")
                    try:
                        client = _get_client()
                        # Exchange the code for a session with proper params
                        auth_code = data['code']
                        res = client.auth.exchange_code_for_session({"auth_code": auth_code})
                        print(f"OAuth: Exchange response type: {type(res)}")
                        if hasattr(res, 'session') and res.session:
                            print(f"OAuth: ✓ Code exchange successful!")
                            _oauth_tokens = {
                                'access_token': res.session.access_token,
                                'refresh_token': res.session.refresh_token,
                            }
                        elif hasattr(res, 'user') and res.user:
                            # Some Supabase versions return differently
                            print(f"OAuth: ✓ Got user, checking for session...")
                            session = client.auth.get_session()
                            if session:
                                _oauth_tokens = {
                                    'access_token': session.access_token,
                                    'refresh_token': session.refresh_token,
                                }
                        else:
                            print(f"OAuth: ✗ Code exchange failed - unexpected response: {res}")
                    except Exception as e:
                        print(f"OAuth: ✗ Code exchange error: {type(e).__name__}: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    # Direct tokens from implicit flow
                    _oauth_tokens = data
                    print(f"OAuth: Tokens received directly (implicit flow)")

                print(f"OAuth: Final token keys: {list(_oauth_tokens.keys()) if _oauth_tokens else 'None'}")
            except Exception as e:
                print(f"OAuth: Failed to process: {e}")
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'ok')

    def log_message(self, format, *args):
        # Log all HTTP requests to help with debugging
        msg = format % args
        print(f"OAuth: HTTP Request: {msg}")


def _start_oauth_server():
    """Start the local HTTP server that captures the OAuth callback."""
    global _oauth_server, _oauth_tokens
    _oauth_tokens = None
    _stop_oauth_server()
    print(f"OAuth: Starting callback server on port {OAUTH_CALLBACK_PORT}...")
    try:
        server = HTTPServer(('127.0.0.1', OAUTH_CALLBACK_PORT), _OAuthCallbackHandler)
        _oauth_server = server
        t = threading.Thread(target=server.serve_forever, daemon=True, name="OAuthServer")
        t.start()
        print(f"OAuth: ✓ Server started successfully and listening on port {OAUTH_CALLBACK_PORT}")
    except Exception as e:
        print(f"OAuth: ✗ FAILED to start server on port {OAUTH_CALLBACK_PORT}: {e}")
        raise


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

    print("OAuth: poll_oauth_result() called")

    if not _oauth_tokens:
        print("OAuth: No tokens yet, still pending")
        return {"ok": False, "pending": True}

    print(f"OAuth: Tokens found! Processing...")
    access_token = _oauth_tokens.get('access_token', '')
    refresh_token = _oauth_tokens.get('refresh_token', '')
    _oauth_tokens = None

    if not access_token:
        print("OAuth: Error - No access token in received data")
        _stop_oauth_server()
        return {"ok": False, "error": "No access token received"}

    try:
        print("OAuth: Setting Supabase session with tokens...")
        client = _get_client()
        res = client.auth.set_session(access_token, refresh_token)
        if res.user:
            print(f"OAuth: Success! User {res.user.email} authenticated")
            _user = {"id": str(res.user.id), "email": res.user.email}
            _session = {
                "access_token": res.session.access_token if res.session else access_token,
                "refresh_token": res.session.refresh_token if res.session else refresh_token,
            }
            _save_session(_session)
            _stop_oauth_server()
            return {"ok": True, "user": _user}
    except Exception as e:
        print(f"OAuth: Session error: {e}")
        _stop_oauth_server()
        return {"ok": False, "error": f"Session error: {e}"}

    print("OAuth: Failed - no user returned from set_session")
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
