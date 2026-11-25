"""Import from Granola: a person's Granola meetings become Waffler meetings.

No model is involved and nothing is paid for. Granola's own file on the
computer is encrypted, so the way in is the door Granola opens for other
programs: its MCP endpoint (RESOURCE), which this file talks to as an
ordinary client, in JSON-RPC over HTTP, with the Python standard library.

Four parts:

* signing in (`connect`): Granola says where its sign-in service is (two
  small documents read at run time, never written down here), Waffler
  registers itself there as a public client, the person approves in their
  browser, and the answer comes back to a listener on 127.0.0.1 that takes
  exactly one call. Authorisation code with PKCE (S256) and a random state.
  Every address used must be https;
* the client (`Client`): initialize, then tools/call, answered as JSON or as
  an event stream. A 401 refreshes the token once; a rate limit waits and
  tries again;
* reading (`parse_listing`, `parse_meetings`, `parse_transcript`): Granola
  answers in text that looks like XML but is not promised to be, so it is
  read with patterns, one meeting at a time;
* the run (`run`, and `Job` for the window): list everything, skip what was
  imported before and has not changed since (STATE_FILE), store the rest with
  Store.import_external, one meeting at a time. Stopped part way, what was
  imported stays and the next run carries on.

The tokens are secrets. They are kept as the account's session is
(src/account.py): TOKEN_FILE in the data folder, the owner's alone, never in
settings.json, never in a log, never handed to the page, and only ever sent
to the addresses Granola named.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import html
import http.server
import importlib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

RESOURCE = "https://mcp.granola.ai/mcp"
SOURCE = "Granola"                    # Store.import_external's `source`: part of every imported id
TOKEN_FILE = "granola.json"           # in the data folder, mode 600
STATE_FILE = "granola_import.json"    # which meetings are in, and Granola's date for each
CLIENT_NAME = "Waffler"
PROTOCOL = "2025-06-18"
BROWSER_WAIT_S = 300.0                # how long the person has to approve in the browser
TIMEOUT_S = 30.0
MAX_ANSWER = 16 * 1024 * 1024         # bytes in one answer: far above any real one
TRIES = 3                             # per meeting, before it is left for another day
WAIT_S = (2.0, 5.0, 15.0)             # between tries, when Granola names no wait of its own
MAX_WAIT_S = 60.0
BATCH = 10                            # meetings in one get_meetings call (Granola's limit)
PAGE_CHARS = 40_000                   # one page of a transcript
MAX_PAGES = 500                       # of a listing or of one transcript: a loop is stopped here

NO_INTERNET = "Waffler couldn't reach Granola. Check your internet connection and try again."
NO_BROWSER = "Waffler couldn't open your browser. Try again."
TIMED_OUT = "Granola wasn't approved in the browser. Try again."
REFUSED = "Granola didn't let Waffler in. Try again."
NOT_CONNECTED = "Connect Granola first."
FAILED = "That didn't work. Try again."
BUSY = "An import is already running."
STOPPED = "stopped"                   # a reason, never shown: the person pressed Cancel

_MINE = {"microphone", "me", "you", "mic", "i", "speaker a"}
_THEIRS = {"system audio", "them", "system"}


class GranolaError(Exception):
    """`sentence` is for the person, `reason` a word for the log. Neither
    ever holds a token, a code or anything a meeting said."""

    def __init__(self, sentence: str = FAILED, reason: str = "failed", wait_s: float | None = None):
        super().__init__(reason)
        self.sentence, self.reason, self.wait_s = sentence, reason, wait_s


def _waffler(name: str):
    """One of Waffler's own modules, whichever way the app was started."""
    try:
        return importlib.import_module(f"src.{name}")
    except ImportError:
        return importlib.import_module(name)


def _data_file(name: str) -> Path:
    return _waffler("data_paths").data_file(name)


def _version() -> str:
    try:
        return str(importlib.import_module("src").__version__)
    except Exception:  # noqa: BLE001
        return "0"


# ── addresses: https, or this computer when a test stands in for Granola ────

def _loopback(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and parts.hostname == "127.0.0.1"


def checked(url, resource: str = RESOURCE) -> str:
    """`url` if it may be spoken to: https, with a host and no name or
    password in it. http://127.0.0.1 passes only when the resource itself is
    there (the tests' pretend Granola). Anything else raises."""
    if isinstance(url, str) and len(url) <= 2000:
        parts = urlsplit(url)
        if parts.hostname and not parts.username and not parts.password and "@" not in parts.netloc:
            if parts.scheme == "https" or (_loopback(url) and _loopback(resource)):
                return url
    raise GranolaError(REFUSED, "not_https")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """An answer that sends Waffler somewhere else is refused: a token goes
    only to the address it was meant for."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _open(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
    """The open answer (the caller closes it), whatever its status."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        return _opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise GranolaError(NO_INTERNET, f"offline ({type(e).__name__})") from None


def _fetch(method: str, url: str, *, headers: dict | None = None, form: dict | None = None,
           data: dict | None = None, timeout: float = TIMEOUT_S) -> tuple[int, dict]:
    """One small JSON exchange: (status, the answer as a dict, {} if it was
    not one)."""
    headers = {"accept": "application/json", "user-agent": CLIENT_NAME, **(headers or {})}
    body = None
    if form is not None:
        body = urlencode(form).encode()
        headers["content-type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["content-type"] = "application/json"
    answer = _open(method, url, headers, body, timeout)
    try:
        with contextlib.closing(answer):
            raw = answer.read(MAX_ANSWER + 1)
    except OSError as e:
        raise GranolaError(NO_INTERNET, f"offline ({type(e).__name__})") from None
    try:
        parsed = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_ANSWER else {}
    except (UnicodeDecodeError, ValueError):
        parsed = {}
    return answer.status if hasattr(answer, "status") else answer.code, parsed if isinstance(parsed, dict) else {}


# ── the tokens: one small owner-only file ───────────────────────────────────

def _token_path() -> Path:
    return _data_file(TOKEN_FILE)


def _read_tokens(resource: str = RESOURCE) -> dict | None:
    """What was kept when Granola was connected, if it is for this resource."""
    try:
        data = json.loads(_token_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("resource") != resource \
            or not isinstance(data.get("access_token"), str) or not data["access_token"]:
        return None
    return data


def _write_tokens(data: dict) -> None:
    """Kept as the account's session is (src/account.py _write_session): made
    empty and the owner's alone, then filled, then moved into place."""
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with contextlib.suppress(OSError):
        tmp.unlink()                                  # a leftover would keep whatever mode it had
    os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    _waffler("account")._owner_only(tmp)              # Windows: the same, before a token is in it
    with open(tmp, "w", encoding="utf-8") as out:
        json.dump(data, out)
    _waffler("atomic_json").replace_with_retry(tmp, path)


def _forget_tokens() -> None:
    for path in (_token_path(), _token_path().with_name(TOKEN_FILE + ".tmp")):
        _waffler("atomic_json").unlink_with_retry(path)


def connected(resource: str = RESOURCE) -> bool:
    return _read_tokens(resource) is not None


# ── signing in ──────────────────────────────────────────────────────────────

def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _challenge(headers) -> dict:
    """What a 401 says about where to sign in: {"resource_metadata", "scope"}."""
    said = " ".join(headers.get_all("www-authenticate") or []) if headers else ""
    return {k: v for k, v in re.findall(r'(resource_metadata|scope)="([^"]*)"', said)}


def discover(resource: str = RESOURCE) -> dict:
    """Where Granola's sign-in service is, read from Granola now:
    {"issuer", "authorization_endpoint", "token_endpoint",
    "registration_endpoint", "revocation_endpoint", "scope"}. Every address
    is checked (`checked`) before it is believed."""
    checked(resource, resource)
    origin = _origin(resource)
    hint: dict = {}
    try:                                              # ask without a token: the refusal says where to look
        probe = _open("POST", resource, {"content-type": "application/json", "user-agent": CLIENT_NAME,
                                         "accept": "application/json, text/event-stream"},
                      json.dumps({"jsonrpc": "2.0", "id": 0, "method": "ping"}).encode(), TIMEOUT_S)
        with contextlib.closing(probe):
            hint = _challenge(probe.headers)
    except GranolaError as e:
        if e.reason.startswith("offline"):
            raise
    places = [f"{origin}/.well-known/oauth-protected-resource",
              f"{origin}/.well-known/oauth-protected-resource{urlsplit(resource).path}"]
    named = hint.get("resource_metadata", "")
    if named and _origin(named) == origin:            # only Granola's own word about Granola
        places.insert(0, named)
    about = {}
    for place in dict.fromkeys(places):
        status, about = _fetch("GET", checked(place, resource))
        if status == 200 and about.get("resource") == resource and about.get("authorization_servers"):
            break
        about = {}
    servers = about.get("authorization_servers")
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
        raise GranolaError(REFUSED, "no_sign_in_service")
    issuer = checked(servers[0].rstrip("/"), resource)
    meta, unreachable = {}, None
    for place in (f"{issuer}/.well-known/oauth-authorization-server",
                  f"{issuer}/.well-known/openid-configuration",
                  f"{origin}/.well-known/oauth-authorization-server"):
        try:
            status, meta = _fetch("GET", place)
        except GranolaError as e:                     # one place not answering: the next may
            unreachable, meta = e, {}
            continue
        said = str(meta.get("issuer") or "").rstrip("/")
        if status == 200 and meta.get("authorization_endpoint") and meta.get("token_endpoint") \
                and said in ("", issuer, origin):
            break
        meta = {}
    if not meta:
        raise unreachable or GranolaError(REFUSED, "no_sign_in_details")
    if "S256" not in (meta.get("code_challenge_methods_supported") or []):
        raise GranolaError(REFUSED, "no_pkce")
    out = {"issuer": str(meta.get("issuer") or issuer)}
    for name in ("authorization_endpoint", "token_endpoint", "registration_endpoint", "revocation_endpoint"):
        if meta.get(name):
            out[name] = checked(meta[name], resource)
    if "registration_endpoint" not in out:
        raise GranolaError(REFUSED, "no_registration")
    scopes = hint.get("scope", "").split() or [s for s in about.get("scopes_supported") or []
                                                if isinstance(s, str)]
    if "offline_access" in (meta.get("scopes_supported") or []) and "offline_access" not in scopes:
        scopes.append("offline_access")               # so the token can be renewed without the browser
    out["scope"] = " ".join(scopes)
    return out


class _Callback(http.server.BaseHTTPRequestHandler):
    """The browser's way back. One call to /callback is taken; anything else
    (a favicon) is turned away and does not count."""

    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path != "/callback" or self.server.answer is not None:
            self.send_error(404)
            return
        self.server.answer = {k: v[0] for k, v in parse_qs(parts.query).items()}
        good = self.server.judge(self.server.answer)
        page = _PAGE.format(title="Granola is connected." if good else "That didn't work.",
                            line="Go back to Waffler." if good else "Go back to Waffler and try again.")
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):                      # the address holds the code: nothing is printed
        pass


_PAGE = ("<!doctype html><meta charset=utf-8><title>Waffler</title>"
         "<body style=\"margin:0;height:100vh;display:grid;place-items:center;background:#12100E;"
         "color:#F4F0EA;font:400 17px/1.5 -apple-system,'Segoe UI',system-ui,sans-serif;text-align:center\">"
         "<div><h1 style=\"margin:0 0 8px;font:600 34px/1.15 -apple-system,'Segoe UI',system-ui,sans-serif;"
         "letter-spacing:-0.02em\">{title}</h1><p style=\"margin:0;color:#B3AA9E\">{line}</p></div>")


def _listener():
    """A listener on this computer only, on a port the system picks."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _Callback)
    server.timeout = 0.25
    server.answer = None
    return server


def _wait_for_browser(server, state: str, issuer: str, cancel: threading.Event, timeout_s: float) -> str:
    """The code Granola sends back, once the person approves. The state must
    be the one sent, and the sender (`iss`), when named, the service asked."""
    def judge(answer: dict) -> bool:
        return bool(answer.get("code")) and not answer.get("error") \
            and secrets.compare_digest(str(answer.get("state") or ""), state) \
            and ("iss" not in answer or answer["iss"].rstrip("/") == issuer.rstrip("/"))
    server.judge = judge
    deadline = time.monotonic() + timeout_s
    while server.answer is None:
        if cancel.is_set():
            raise GranolaError(FAILED, STOPPED)
        if time.monotonic() > deadline:
            raise GranolaError(TIMED_OUT, "browser_timeout")
        server.handle_request()
    if not judge(server.answer):
        word = re.sub(r"[^a-z_]", "", str(server.answer.get("error") or "")[:40]) or "bad_callback"
        raise GranolaError(REFUSED, word)
    return server.answer["code"]


def _token_call(endpoint: str, form: dict) -> dict:
    status, answer = _fetch("POST", endpoint, form=form)
    if status != 200 or not isinstance(answer.get("access_token"), str) or not answer["access_token"]:
        word = re.sub(r"[^a-z_]", "", str(answer.get("error") or "")[:40])
        raise GranolaError(REFUSED, f"token_refused ({status}{' ' + word if word else ''})")
    return answer


def _kept(answer: dict, before: dict) -> dict:
    """The tokens file after a token call: what was known, with the new tokens."""
    out = {**before, "access_token": answer["access_token"]}
    if isinstance(answer.get("refresh_token"), str) and answer["refresh_token"]:
        out["refresh_token"] = answer["refresh_token"]
    life = answer.get("expires_in")
    out["expires_at"] = time.time() + float(life) if isinstance(life, (int, float)) and life > 0 else 0
    return out


def connect(cancel: threading.Event | None = None, *, resource: str = RESOURCE, open_browser=None,
            timeout_s: float = BROWSER_WAIT_S) -> None:
    """Sign in to Granola in the person's browser and keep the tokens.
    Raises GranolaError when it did not happen (reason STOPPED if cancelled).
    If Granola sends the browser back saying it will not give what was asked
    for (the scope), it is asked once more for what it gives by default."""
    cancel = cancel or threading.Event()
    found = discover(resource)
    try:
        kept = _approve(found, found["scope"], cancel, resource, open_browser, timeout_s)
    except GranolaError as e:
        if e.reason != "invalid_scope" or not found["scope"]:
            raise
        kept = _approve(found, "", cancel, resource, open_browser, timeout_s)
    _write_tokens(kept)


def _approve(found: dict, scope: str, cancel: threading.Event, resource: str, open_browser,
             timeout_s: float) -> dict:
    """One trip through the browser: register, ask, wait, and exchange the
    code. Answers what to keep (the tokens file's contents)."""
    server = _listener()
    try:
        redirect = f"http://127.0.0.1:{server.server_address[1]}/callback"
        status, client = _fetch("POST", found["registration_endpoint"], data={
            "client_name": CLIENT_NAME, "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none", **({"scope": scope} if scope else {})})
        if status not in (200, 201) or not isinstance(client.get("client_id"), str) or not client["client_id"]:
            raise GranolaError(REFUSED, f"registration_refused ({status})")
        verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        ask = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect,
               "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
               "resource": resource}
        if scope:
            ask["scope"] = scope
        address = found["authorization_endpoint"]
        address += ("&" if "?" in address else "?") + urlencode(ask)
        if open_browser is None:
            import webbrowser
            open_browser = webbrowser.open
        try:
            opened = open_browser(address)
        except Exception:  # noqa: BLE001
            opened = False
        if opened is False:
            raise GranolaError(NO_BROWSER, "no_browser")
        code = _wait_for_browser(server, state, found["issuer"], cancel, timeout_s)
    finally:
        with contextlib.suppress(OSError):
            server.server_close()
    answer = _token_call(found["token_endpoint"], {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
        "client_id": client["client_id"], "code_verifier": verifier, "resource": resource})
    return _kept(answer, {"resource": resource, "client_id": client["client_id"],
                          "token_endpoint": found["token_endpoint"],
                          "revocation_endpoint": found.get("revocation_endpoint", "")})


def _refresh(tokens: dict) -> dict:
    """New tokens from the refresh token. If Granola will not give them, the
    old ones are forgotten: the person connects again."""
    resource = tokens["resource"]
    if not tokens.get("refresh_token") or not tokens.get("token_endpoint"):
        _forget_tokens()
        raise GranolaError(NOT_CONNECTED, "signed_out")
    try:
        answer = _token_call(checked(tokens["token_endpoint"], resource), {
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": tokens.get("client_id", ""), "resource": resource})
    except GranolaError as e:
        if e.reason.startswith("token_refused"):
            _forget_tokens()
            raise GranolaError(NOT_CONNECTED, "signed_out") from None
        raise
    fresh = _kept(answer, tokens)
    _write_tokens(fresh)
    return fresh


def disconnect(resource: str = RESOURCE) -> None:
    """Forget the tokens, and ask Granola to end them too (it may not hear;
    they are gone from this computer either way). Never raises."""
    tokens = _read_tokens(resource)
    _forget_tokens()
    if not tokens or not tokens.get("revocation_endpoint"):
        return
    for kind in ("refresh_token", "access_token"):
        if tokens.get(kind):
            with contextlib.suppress(Exception):
                _fetch("POST", checked(tokens["revocation_endpoint"], resource), timeout=10.0, form={
                    "token": tokens[kind], "token_type_hint": kind, "client_id": tokens.get("client_id", "")})


# ── the client ──────────────────────────────────────────────────────────────

def _wait_named(text: str) -> float | None:
    """The wait a rate limit names ("retry after 12 seconds", "in 500 ms")."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|secs?|seconds?|minutes?|mins?)\b", text, re.I)
    if not m:
        return None
    unit = m.group(2).lower()
    scale = 0.001 if unit.startswith("ms") or unit.startswith("milli") else 60.0 if unit.startswith("min") else 1.0
    return float(m.group(1)) * scale


def _rate_limited(text: str) -> bool:
    return bool(re.search(r"rate[_ -]?limit|too many requests", text, re.I))


class Client:
    """Granola's MCP endpoint, as much of it as an import needs."""

    def __init__(self, resource: str = RESOURCE, cancel: threading.Event | None = None, waits=WAIT_S):
        self.resource = checked(resource, resource)
        self.cancel = cancel or threading.Event()
        self.waits = waits                             # between tries, when Granola names no wait
        self._tokens = _read_tokens(resource)
        if self._tokens is None:
            raise GranolaError(NOT_CONNECTED, "signed_out")
        self._session, self._version, self._n = "", "", 0

    def _post(self, message: dict) -> dict | None:
        """One message; the answer to it (None for a notification)."""
        headers = {"content-type": "application/json", "accept": "application/json, text/event-stream",
                   "user-agent": CLIENT_NAME, "authorization": "Bearer " + self._tokens["access_token"]}
        if self._session:
            headers["mcp-session-id"] = self._session
        if self._version:
            headers["mcp-protocol-version"] = self._version
        answer = _open("POST", self.resource, headers, json.dumps(message).encode(), TIMEOUT_S)
        with contextlib.closing(answer):
            status = answer.status if hasattr(answer, "status") else answer.code
            if status == 401:
                raise GranolaError(NOT_CONNECTED, "401")
            if status == 429:
                after = answer.headers.get("retry-after", "")
                raise GranolaError(FAILED, "rate_limit", float(after) if after.replace(".", "", 1).isdigit() else None)
            if status == 404 and self._session:
                raise GranolaError(FAILED, "session_ended")
            if status >= 400:
                raise GranolaError(FAILED, f"answer {status}")
            if answer.headers.get("mcp-session-id"):
                self._session = answer.headers["mcp-session-id"]
            if "id" not in message:
                return None
            try:
                if "text/event-stream" in (answer.headers.get("content-type") or ""):
                    return self._from_stream(answer, message["id"])
                raw = answer.read(MAX_ANSWER + 1)
            except OSError as e:
                raise GranolaError(NO_INTERNET, f"offline ({type(e).__name__})") from None
        try:
            reply = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_ANSWER else None
        except (UnicodeDecodeError, ValueError):
            reply = None
        if not isinstance(reply, dict):
            raise GranolaError(FAILED, "unreadable_answer")
        return reply

    @staticmethod
    def _from_stream(answer, wanted) -> dict:
        """The reply to `wanted` out of an event stream: each event's `data:`
        lines are one JSON message; others (progress, a ping) are passed over."""
        data, read = [], 0
        while True:
            line = answer.readline()
            read += len(line)
            if read > MAX_ANSWER:
                break
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if text.startswith("data:"):
                data.append(text[5:].lstrip(" "))
            elif not text and (data or not line):
                try:
                    reply = json.loads("\n".join(data)) if data else None
                except ValueError:
                    reply = None
                if isinstance(reply, dict) and reply.get("id") == wanted and "method" not in reply:
                    return reply
                data = []
                if not line:                           # the stream ended
                    break
        raise GranolaError(FAILED, "unreadable_answer")

    def _rpc(self, method: str, params: dict) -> dict:
        """A request, with the token renewed once if Granola no longer takes
        it and the session opened again once if it ended. The result."""
        for attempt in range(3):
            if self.cancel.is_set():
                raise GranolaError(FAILED, STOPPED)
            if method != "initialize" and not self._session and not self._version:
                self._start()
            self._n += 1
            try:
                if attempt == 0 and 0 < self._tokens.get("expires_at", 0) < time.time() + 30:
                    self._tokens = _refresh(self._tokens)
                reply = self._post({"jsonrpc": "2.0", "id": self._n, "method": method, "params": params})
            except GranolaError as e:
                if e.reason == "401" and attempt == 0:
                    self._tokens = _refresh(self._tokens)
                    continue
                if e.reason == "session_ended" and attempt < 2 and method != "initialize":
                    self._session = self._version = ""
                    continue
                if e.reason == "401":                  # a token just renewed, and still refused
                    _forget_tokens()
                    raise GranolaError(NOT_CONNECTED, "signed_out") from None
                raise
            if isinstance(reply.get("error"), dict):
                said = str(reply["error"].get("message") or "")
                if _rate_limited(said):
                    raise GranolaError(FAILED, "rate_limit", _wait_named(said))
                raise GranolaError(FAILED, "refused_call")
            if not isinstance(reply.get("result"), dict):
                raise GranolaError(FAILED, "unreadable_answer")
            return reply["result"]
        raise GranolaError(FAILED, "no_answer")

    def _start(self) -> None:
        result = self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                          "clientInfo": {"name": CLIENT_NAME, "version": _version()}})
        self._version = str(result.get("protocolVersion") or PROTOCOL)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, arguments: dict) -> str:
        """One tool's answer, as text. A rate limit raises with reason
        "rate_limit" and, when Granola named one, the wait."""
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        text = "\n".join(str(c.get("text") or "") for c in result.get("content") or []
                         if isinstance(c, dict) and c.get("type") == "text")
        if result.get("isError"):
            if _rate_limited(text):
                raise GranolaError(FAILED, "rate_limit", _wait_named(text))
            raise GranolaError(FAILED, "tool_error")
        return text

    def patiently(self, tool: str, arguments: dict) -> str:
        """`call`, tried up to TRIES times: a rate limit or a passing fault
        waits (as long as Granola says, else `self.waits`) and goes again. Being
        signed out, offline or cancelled is not tried again."""
        for attempt in range(TRIES):
            try:
                return self.call(tool, arguments)
            except GranolaError as e:
                if e.reason in (STOPPED, "signed_out") or e.reason.startswith("offline") \
                        or attempt == TRIES - 1:
                    raise
                wait = e.wait_s if e.wait_s is not None else self.waits[min(attempt, len(self.waits) - 1)]
                if self.cancel.wait(max(0.0, min(wait, MAX_WAIT_S))):
                    raise GranolaError(FAILED, STOPPED) from None
        raise GranolaError(FAILED, "no_answer")


# ── reading what Granola says ───────────────────────────────────────────────

_TAG = r"<{name}\b((?:\"[^\"]*\"|[^>\"])*)>"
_MEETING = re.compile(_TAG.format(name="meeting"))
_FOLDER = re.compile(_TAG.format(name="folder"))
_ATTR = re.compile(r"([\w:-]+)\s*=\s*\"([^\"]*)\"")
_ENTITY = re.compile(r"&(?:#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,30});")


def unescape(text: str) -> str:
    """Entities that are entities (&amp; &lt; &#39;) as their characters. A
    bare ampersand, as in "R&D", is left exactly as it is."""
    return _ENTITY.sub(lambda m: html.unescape(m.group(0)), text)


def _attrs(inside: str) -> dict:
    return {k: unescape(v) for k, v in _ATTR.findall(inside)}


def _meeting_tags(text: str) -> list[tuple[int, int, dict]]:
    """Every <meeting ...> that names an id: (where it starts, where it ends,
    its attributes)."""
    out = []
    for m in _MEETING.finditer(text):
        attrs = _attrs(m.group(1))
        if "id" not in attrs:                          # a quote inside a title broke the pattern
            for name in ("id", "meeting_at", "created_at", "updated_at", "status"):
                found = re.search(rf"\b{name}=\"([^\"]*)\"", m.group(1))
                if found:
                    attrs[name] = unescape(found.group(1))
        if attrs.get("id"):
            out.append((m.start(), m.end(), attrs))
    return out


def _folders(chunk: str) -> list[str]:
    block = re.search(r"<folders\b[^>]*>(.*?)</folders>", chunk, re.S)
    names = [_attrs(m.group(1)).get("title", "").strip() for m in _FOLDER.finditer(block.group(1))] if block else []
    return [n for n in names if n]


def _chunks(text: str, only: set | None = None) -> list[tuple[dict, str]]:
    """(attributes, everything up to the next meeting) for each meeting."""
    tags = [t for t in _meeting_tags(text) if only is None or t[2]["id"] in only]
    return [(attrs, text[end:tags[i + 1][0] if i + 1 < len(tags) else len(text)])
            for i, (_start, end, attrs) in enumerate(tags)]


def parse_listing(text: str) -> tuple[list[dict], str]:
    """One page of search_meetings: (the meetings, the cursor for the next
    page or ""). A meeting is {"id", "title", "meeting_at", "updated_at",
    "folders", "empty"}. The list of duplicate recordings at the end is not
    a list of meetings and is left out."""
    if not re.search(r"<(pagination|meetings_data|meeting)\b", text):
        raise GranolaError(FAILED, "unreadable_listing")
    page = re.search(_TAG.format(name="pagination"), text)
    said = _attrs(page.group(1)) if page else {}
    cursor = said.get("next_cursor", "") if said.get("has_more", "true") != "false" else ""
    body = re.sub(r"<duplicate_recordings\b.*?(</duplicate_recordings>|$)", "", text, flags=re.S)
    meetings = [{"id": a["id"], "title": a.get("title", ""),
                 "meeting_at": a.get("meeting_at") or a.get("created_at") or "",
                 "updated_at": a.get("updated_at", ""), "folders": _folders(chunk),
                 "empty": a.get("status") == "empty"} for a, chunk in _chunks(body)]
    return meetings, cursor


def _between(chunk: str, name: str, greedy: bool = False) -> str:
    """What is inside <name>...</name>, as written. `greedy` reads to the
    last closing tag, for notes that may themselves hold one."""
    m = re.search(rf"<{name}\b[^>]*(?<!/)>(.*{'' if greedy else '?'})</{name}>", chunk, re.S)
    return unescape(m.group(1)).strip() if m else ""


def parse_meetings(text: str, ids) -> dict:
    """get_meetings' answer: {id: {"title", "meeting_at", "updated_at",
    "folders", "my_notes", "summary"}} for each of `ids` found in it. The
    transcript element holds a sentence for a model, not a transcript, and is
    not read."""
    out = {}
    for attrs, chunk in _chunks(text, set(ids)):
        end = chunk.rfind("</meeting>")
        chunk = chunk[:end] if end >= 0 else chunk
        notes_end = re.search(r"<transcript\b|<summary\b", chunk)
        out.setdefault(attrs["id"], {
            "title": attrs.get("title", ""), "updated_at": attrs.get("updated_at", ""),
            "meeting_at": attrs.get("meeting_at") or attrs.get("created_at") or "",
            "folders": _folders(chunk),
            "my_notes": _between(chunk[:notes_end.start()] if notes_end else chunk, "private_notes", greedy=True),
            "summary": _between(chunk, "summary", greedy=True)})
    return out


_STAMP = re.compile(r"^\[([^\]\n]{4,40})\]\s*")
_SPEAKER = re.compile(r"^([A-Za-z][\w .'-]{0,40}?):(?:\s+|$)")


def _clock(stamp: str) -> tuple[str, float] | None:
    """("clock", seconds since midnight) or ("date", seconds since 1970)."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", stamp.strip())
    if m:
        return "clock", int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)
    try:
        return "date", datetime.fromisoformat(stamp.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _split_long(text: str, limit: int) -> list[str]:
    """A long turn as several lines, cut between words, so none of it is lost
    to the size one line may be."""
    out = []
    while len(text) > limit:
        cut = text.rfind(" ", limit // 2, limit)
        cut = cut if cut > 0 else limit
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    return out + ([text] if text else [])


def parse_transcript(text: str, line_chars: int = 4000) -> list[dict]:
    """A transcript as lines of {"source", "text", "start_s"}. "Microphone"
    and "Me" are the person's own microphone ("mic"), "System audio" and
    "Them" the other side ("system"); in a recording from a phone or a file,
    "Speaker A" is taken to be the person and the rest the other side.

    Times are clock times: a line starts as long after the first as its clock
    says, and a clock that goes back past midnight has gone on to the next
    day. A line with no time has start_s None (it follows the one before); a
    line with neither a time nor a speaker continues the one before it."""
    out, first, last, days = [], None, None, 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        start, timed = None, False
        m = _STAMP.match(line)
        when = _clock(m.group(1)) if m else None
        if when:
            line, timed = line[m.end():], True
            kind, at = when
            if kind == "clock":
                if last is not None and at + days * 86400 < last - 43200:
                    days += 1                          # past midnight
                at += days * 86400
                last = at
            first = first or (kind, at)
            start = max(0.0, at - first[1]) if first[0] == kind else None
        m = _SPEAKER.match(line)
        speaker = m.group(1).strip().lower() if m else ""
        known = speaker in _MINE or speaker in _THEIRS or bool(re.fullmatch(r"speaker \w{1,3}", speaker))
        if m and (timed or known):
            line = line[m.end():].strip()
            source = "mic" if speaker in _MINE else "system"
        elif not timed and out:
            out[-1]["text"] += " " + line              # the same turn, on a new line
            continue
        else:
            source = out[-1]["source"] if out else "mic"
        if line:
            out.append({"source": source, "text": line, "start_s": start})
    lines = []
    for item in out:
        for piece in _split_long(item["text"], line_chars):
            lines.append({**item, "text": piece})
            item = {**item, "start_s": None}
    return lines


# ── the run ─────────────────────────────────────────────────────────────────

def _read_state() -> dict:
    try:
        data = json.loads(_data_file(STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    done = data.get("meetings") if isinstance(data, dict) else None
    return done if isinstance(done, dict) else {}


def _write_state(done: dict) -> None:
    """Granola's ids and dates only: nothing a meeting said, and no token."""
    with contextlib.suppress(OSError):
        _waffler("atomic_json").write_json_atomic(_data_file(STATE_FILE), {"meetings": done}, indent=0)


def list_meetings(client: Client) -> list[dict]:
    """Every meeting the person attended that has something in it, newest first."""
    found, seen, cursor = [], set(), ""
    for _page in range(MAX_PAGES):
        args = {"from_date": "2015-01-01", "limit": 100, "max_participants": 0, "attended_only": True}
        if cursor:
            args["cursor"] = cursor
        meetings, following = parse_listing(client.patiently("search_meetings", args))
        for m in meetings:
            if m["id"] not in seen and not m["empty"]:
                seen.add(m["id"])
                found.append(m)
        if not following or following == cursor:
            break
        cursor = following
    return found


def read_meetings(client: Client, ids: list[str]) -> dict:
    """get_meetings for up to BATCH ids. Granola leaves some out of an answer
    that would be too large: those are asked for again, then one at a time.
    What still does not come is simply absent from the result."""
    got: dict = {}
    wanted = list(ids)
    for _round in range(len(ids) + 2):
        if not wanted:
            break
        ask = wanted if _round < 2 else wanted[:1]
        try:
            new = parse_meetings(client.patiently("get_meetings", {"meeting_ids": ask, "attachments": "none"}), ask)
        except GranolaError as e:
            if e.reason in (STOPPED, "signed_out") or e.reason.startswith("offline"):
                raise
            new = {}
        got.update(new)
        if not new and _round >= 2:
            wanted = wanted[1:]                        # asked for alone and still not there
        wanted = [i for i in wanted if i not in got]
    return got


def read_transcript(client: Client, meeting_id: str) -> tuple[list[dict], bool]:
    """(the lines, final). `final` is False when Granola says the transcript
    is still being made, so the meeting is looked at again next time."""
    pieces, offset, glue = [], 0, False
    for _page in range(MAX_PAGES):
        raw = client.patiently("get_meeting_transcript", {
            "meeting_id": meeting_id, "include_timestamps": True, "max_chars": PAGE_CHARS, "offset": offset})
        try:
            page = json.loads(raw)
        except ValueError:
            raise GranolaError(FAILED, "unreadable_transcript") from None
        if not isinstance(page, dict):
            raise GranolaError(FAILED, "unreadable_transcript")
        text = page.get("transcript")
        if not isinstance(text, str):
            why = page.get("transcript_unavailable")
            why = why.get("reason") if isinstance(why, dict) else why
            return [], why in ("no_transcript", "transcript_deleted", None)
        if pieces and not glue and not page.get("starts_mid_utterance") and not pieces[-1].endswith("\n"):
            pieces.append("\n")
        pieces.append(text)
        glue = bool(page.get("ends_mid_utterance"))
        following = page.get("next_offset")
        if not page.get("truncated") or not isinstance(following, int) or following <= offset:
            break
        offset = following
    return parse_transcript("".join(pieces)), True


def run(store, *, cancel: threading.Event | None = None, on_progress=None, log=None,
        resource: str = RESOURCE, waits=WAIT_S) -> dict:
    """Import everything, or everything that is new. Answers {"ok": True,
    "total", "imported" (stored in this run), "already" (in from before and
    unchanged), "failed" (could not be read: tried again next time),
    "deleted" (the person deleted it here: not brought back), "cut" (of those
    imported, transcripts too long to keep whole), "cancelled"} or {"ok":
    False, "error": <one sentence>, "reason"} with the same counts. Never
    raises, and what it logs is counts and a word, never a title or a token."""
    cancel = cancel or threading.Event()
    log = log or (lambda msg: None)
    tell = on_progress or (lambda done, total: None)
    counts = {"total": 0, "imported": 0, "already": 0, "failed": 0, "deleted": 0, "cut": 0,
              "cancelled": False}
    try:
        client = Client(resource, cancel, waits)
        listed = list_meetings(client)
        state = _read_state()
        counts["total"] = len(listed)
        todo = []
        for m in listed:
            was = state.get(m["id"])
            note = store.get_note(str(was.get("note") or "")) if isinstance(was, dict) else None
            if note is not None and was.get("updated_at") == m["updated_at"]:
                counts["deleted" if note["deleted"] else "already"] += 1
            else:
                todo.append(m)
        log(f"[granola] import started: {counts['total']} meetings, {len(todo)} to bring in")
        tell(counts["already"], counts["total"])
        for at in range(0, len(todo), BATCH):
            if cancel.is_set():
                raise GranolaError(FAILED, STOPPED)
            batch = todo[at:at + BATCH]
            details = read_meetings(client, [m["id"] for m in batch])
            for m in batch:
                if cancel.is_set():
                    raise GranolaError(FAILED, STOPPED)
                try:
                    outcome, remember = _import_one(store, client, m, details.get(m["id"]))
                except GranolaError as e:
                    if e.reason in (STOPPED, "signed_out") or e.reason.startswith("offline"):
                        raise
                    outcome, remember = "failed", None
                    log(f"[granola] a meeting could not be read ({e.reason})")
                counts["imported" if outcome == "cut" else outcome] += 1
                if outcome == "cut":
                    counts["cut"] += 1
                    log("[granola] a transcript was longer than a meeting holds: the rest was left out")
                if remember:
                    state[m["id"]] = remember
                    _write_state(state)
                tell(counts["already"] + counts["imported"], counts["total"])
    except GranolaError as e:
        if e.reason != STOPPED:
            log(f"[granola] import stopped: {e.reason}; {counts['imported']} imported before that")
            return {"ok": False, "error": e.sentence, "reason": e.reason, **counts}
        counts["cancelled"] = True
    except Exception as e:  # noqa: BLE001
        log(f"[granola] import failed: {type(e).__name__}")
        return {"ok": False, "error": FAILED, "reason": "failed", **counts}
    log(f"[granola] import {'cancelled' if counts['cancelled'] else 'finished'}: {counts['imported']} imported, "
        f"{counts['already']} already here, {counts['failed']} could not be read")
    return {"ok": True, **counts}


def _import_one(store, client: Client, listed: dict, detail: dict | None) -> tuple[str, dict | None]:
    """One meeting into the store: ("imported" | "cut" | "deleted" | "failed",
    what to remember about it, or None for "look at it again next time")."""
    if detail is None:
        raise GranolaError(FAILED, "not in Granola's answer")
    lines, final = read_transcript(client, listed["id"])
    folders = detail["folders"] or listed["folders"]
    out = store.import_external(
        SOURCE, listed["id"], title=detail["title"] or listed["title"],
        meeting_at=detail["meeting_at"] or listed["meeting_at"], summary=detail["summary"],
        my_notes=detail["my_notes"], lines=lines, folder_name=folders[0] if folders else None)
    if not out["ok"] and out["reason"] != "deleted":
        raise GranolaError(FAILED, out["reason"])
    outcome = "deleted" if not out["ok"] else "cut" if out["transcript_truncated"] else "imported"
    return outcome, {"updated_at": listed["updated_at"], "note": out["id"]} if final else None


# ── for the window: one import at a time, and where it has got to ───────────

class Job:
    """The import the window starts, watches and can stop. `store` is a
    function that answers the Store, called on the import's own thread."""

    def __init__(self, store, log=None, on_change=None, on_imported=None, resource: str = RESOURCE,
                 open_browser=None):
        self._store, self._log = store, log or (lambda msg: None)
        self._on_change = on_change or (lambda: None)
        self._on_imported = on_imported or (lambda: None)
        self._resource, self._open_browser = resource, open_browser
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._view = {"state": "idle", "done": 0, "total": 0, "failed": 0, "error": ""}

    def status(self) -> dict:
        """{"ok", "connected", "state" ("idle" | "connecting" | "importing" |
        "done" | "error"), "done", "total", "failed", "error"}. No token is
        ever in it."""
        with self._lock:
            return {"ok": True, "connected": connected(self._resource), **self._view}

    def _set(self, **changes) -> None:
        with self._lock:
            self._view.update(changes)
        with contextlib.suppress(Exception):
            self._on_change()

    def start(self) -> dict:
        """Connect if need be, then import. Answers at once; `status` follows it."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": False, "error": BUSY, "reason": "busy"}
            self._cancel = threading.Event()
            self._view = {"state": "importing" if connected(self._resource) else "connecting",
                          "done": 0, "total": 0, "failed": 0, "error": ""}
            self._thread = threading.Thread(target=self._work, args=(self._cancel,), daemon=True,
                                            name="GranolaImport")
            self._thread.start()
        return self.status()

    def _work(self, cancel: threading.Event) -> None:
        try:
            if not connected(self._resource):
                connect(cancel, resource=self._resource, open_browser=self._open_browser)
                self._log("[granola] connected")
            self._set(state="importing")
            told = [0.0]

            def progress(done, total):
                self._set(done=done, total=total)
                if time.monotonic() - told[0] > 5:     # the list fills in as the meetings arrive
                    told[0] = time.monotonic()
                    with contextlib.suppress(Exception):
                        self._on_imported()
            out = run(self._store(), cancel=cancel, on_progress=progress, log=self._log,
                      resource=self._resource)
            with contextlib.suppress(Exception):
                self._on_imported()
            if not out["ok"]:
                self._set(state="error", error=out["error"])
            elif out["cancelled"]:
                self._set(state="idle")
            else:
                self._set(state="done", done=out["imported"] + out["already"], total=out["total"],
                          failed=out["failed"])
        except GranolaError as e:
            if e.reason == STOPPED:
                self._set(state="idle")
            else:
                self._log(f"[granola] not connected: {e.reason}")
                self._set(state="error", error=e.sentence)
        except Exception as e:  # noqa: BLE001
            self._log(f"[granola] failed: {type(e).__name__}")
            self._set(state="error", error=FAILED)

    def cancel(self) -> dict:
        """Stop after the meeting being read. What was imported stays."""
        self._cancel.set()
        return self.status()

    def disconnect(self) -> dict:
        """Stop, forget the tokens and tell Granola they are finished with."""
        self._cancel.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        disconnect(self._resource)
        self._log("[granola] disconnected")
        self._set(state="idle", done=0, total=0, failed=0, error="")
        return self.status()

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


# ── from a terminal: python app.py --import-granola ─────────────────────────

def main(argv: list[str]) -> int:
    """Run an import against a chosen data folder, for looking into a
    problem. It prints counts and nothing else: no title, no word of a
    meeting, no token. It will not touch the person's real data folder unless
    told to with --real-data."""
    if not os.environ.get("WAFFLER_DATA_DIR", "").strip() and "--real-data" not in argv:
        print("Set WAFFLER_DATA_DIR to the folder to import into, or pass --real-data "
              "to import into your own Waffler.")
        return 2
    from .store import Store
    resource = os.environ.get("WAFFLER_GRANOLA_RESOURCE", "").strip() or RESOURCE
    try:
        if "--disconnect" in argv:
            disconnect(resource)
            print("Disconnected.")
            return 0
        if not connected(resource):
            print("Opening your browser to connect Granola.")
            connect(resource=resource)
            print("Connected.")
    except GranolaError as e:
        print(f"{e.sentence} ({e.reason})")
        return 1
    seen = [(-1, -1)]

    def progress(done, total):
        if (done, total) != seen[0]:
            seen[0] = (done, total)
            print(f"{done} of {total}")
    out = run(Store(), on_progress=progress, log=print, resource=resource)
    if not out["ok"]:
        print(f"{out['error']} ({out['reason']})")
        return 1
    return 0
