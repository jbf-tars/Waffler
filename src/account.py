"""An account: sign in on each computer and the meetings stay in step.

The client for the account service (another program; its reference is
docs/HOSTED.md in that project). A person types their email, is sent a
code (as many digits as the service says: 8, or 6 from an older service that
does not say), and this computer becomes one of the account's devices. From
then on the meetings sync with the account (src/meetings/sync.py, driven by
src/meetings_ctl.py), and the account can hand Claude a private link to read
them.

Where the service is: the environment variable WAFFLER_ACCOUNT_SERVER, else
"account_server" in settings.json, else nowhere. With no address the feature
is off: the page says accounts are coming soon and nothing is contacted. An
address must be https, or this computer itself (http://127.0.0.1, for
development), because the session token travels with every request.

The session token is a secret. Waffler has no credential-store code (its
keys live in a file in the data folder), so the token is kept the same way:
`account.json` in the data folder, readable by its owner only (mode 600). It
is never written to settings.json or a log and never handed to the page, and
it is only ever sent to the service that issued it.

Every function here answers a dict and never raises: {"ok": True, ...} or
{"ok": False, "error": <one plain sentence>, "reason": <a word>}.
"""
from __future__ import annotations

import contextlib
import functools
import json
import math
import os
import platform
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

try:
    from src.atomic_json import replace_with_retry
    from src.data_paths import data_file
except ImportError:                                   # run from inside src/
    from atomic_json import replace_with_retry
    from data_paths import data_file

SERVER_ENV = "WAFFLER_ACCOUNT_SERVER"
SERVER_SETTING = "account_server"                     # settings.json
SESSION_FILE = "account.json"                         # in the data folder, mode 600
TIMEOUT_S = 20.0
SYNC_TIMEOUT_S = 60.0

NOT_AVAILABLE = "Accounts are coming soon."
NOT_SIGNED_IN = "Sign in first."
NO_INTERNET = "Waffler couldn't reach the account service. Check your internet connection and try again."
BAD_EMAIL = "That doesn't look like an email address."
BAD_CODE = "Enter the {n}-digit code from the email."
BAD_CODE_ANY = "Enter the code from the email."       # no code was asked for since Waffler opened
WRONG_CODE = "That code is wrong or has expired. Check it, or send a new one."
CODE_NOT_SENT = "The code couldn't be sent just now. Try again in a moment."
EMAILS_USED_UP = "Waffler has sent all the sign-in emails it can for now. Try again in {wait}."
SIGNUPS_CLOSED = "Waffler accounts are invite-only for now."
MODEL_CALLS_CAP = "Today's questions and rewrites are used up. There are more tomorrow."
WRITING_CAP = "Today's writing allowance is used up. There is more tomorrow."
ALLOWANCE_USED = "Your account's allowance is used up for now. Try again tomorrow."
TOO_LARGE = "That is too long to send in one go. Try it with less text."
TOO_MANY_WRONG_CODES = "Too many wrong codes for this address. Wait {wait}, then ask for a new code."
TOO_MANY_CODES = "Too many codes have been requested for this address. Try again {when}."
SERVER_FULL = ("The account service is full just now. Nothing is lost: your meetings stay on this "
               "computer and sync when there is room.")
TYPE_DELETE = "Type DELETE to confirm."
CODE_REQUIRED = "Deleting your account needs a code from your email. Ask for one, then try again."
NOT_DELETED = "Your account was not deleted. "
ACCOUNT_DELETED = "Your account is deleted. The meetings on this computer are still here."
OVER_ALLOWANCE = "Your account is over its allowance. Nothing more can be added until there is room."
DISABLED = "This account has been disabled."
SIGNED_OUT = "This computer was signed out of the account. Sign in again."
SIGNED_OUT_OFFLINE = ("Signed out on this computer. The account service couldn't be reached, so "
                      "remove this computer from another of your devices to finish.")
REMOVE_SELF = "To remove this computer, use Sign out."
BAD_LINK = ("The account service answered with a link that is not its own, so Waffler did not keep it. "
            "Try again.")
DEVICE_GONE = "That device is no longer signed in."
BUSY = "The account service is busy. Try again in a moment."
SYNC_TOO_OFTEN = "The account service asked Waffler to wait. It will try again shortly."
FAILED = "That didn't work. Try again."

DIGITS_OLD = 6               # a service that does not say how long its codes are
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
_digits: dict[str, int] = {}  # the address a code was asked for -> how many digits the service said
_transport = None            # the tests put a stand-in service here (an httpx transport)
_notice = ""                 # why this computer is no longer signed in, until the next sign-in


class AccountError(Exception):
    """A refusal, as one sentence for the person and a word for the log."""

    def __init__(self, sentence: str, reason: str = "failed"):
        super().__init__(sentence)
        self.sentence, self.reason = sentence, reason


# ── where the service is ────────────────────────────────────────────────────

def _settings() -> dict:
    try:
        data = json.loads(data_file("settings.json").read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clean_server(raw) -> str:
    """The address as it is used, or "" for one that is not acceptable."""
    raw = str(raw or "").strip().rstrip("/")
    try:
        parts = urlsplit(raw)
        host = parts.hostname or ""
        parts.port                                    # a port that is not a number raises
    except ValueError:
        return ""
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        return ""
    if parts.scheme == "https" or (parts.scheme == "http" and host in _LOCAL_HOSTS):
        return raw
    return ""


def server_url() -> str:
    """The account service's address, or "" when accounts are switched off."""
    raw = os.environ.get(SERVER_ENV, "").strip() or _settings().get(SERVER_SETTING)
    return _clean_server(raw)


def default_device_name() -> str:
    """What this computer is called in the account's list of devices."""
    name = (platform.node() or "").strip()
    for ending in (".local", ".lan", ".home"):
        if name.lower().endswith(ending):
            name = name[:-len(ending)]
    kind = {"Darwin": "Mac", "Windows": "Windows PC"}.get(platform.system(), "computer")
    return (name or f"A {kind}")[:80]


# ── the session: one small owner-only file ──────────────────────────────────

def _session_path() -> Path:
    return data_file(SESSION_FILE)


def _read_session() -> dict | None:
    try:
        data = json.loads(_session_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("token"), str) or not data["token"] \
            or not isinstance(data.get("server"), str):
        return None
    return data


def _write_session(data: dict) -> None:
    path = _session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with contextlib.suppress(OSError):
        tmp.unlink()                                  # a leftover would keep whatever mode it had
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(data, out)
    replace_with_retry(tmp, path)                     # Windows: the file may be briefly held open


def forget() -> None:
    """Sign this computer out as far as this computer is concerned."""
    for path in (_session_path(), _session_path().with_name(SESSION_FILE + ".tmp")):
        with contextlib.suppress(OSError):
            path.unlink()


def _session() -> dict | None:
    """The session, token included, when it belongs to the service in use.
    A token is never shown to any other address."""
    server = server_url()
    s = _read_session()
    if not server or not s or s["server"] != server:
        return None
    return s


def sync_credentials() -> dict | None:
    """For the sync client only: {"server", "token", "scope"}, or None when
    not signed in. `scope` names the account, so the sync cursors of one
    account are never used with another. Never passed to the page or a log."""
    s = _session()
    if not s:
        return None
    return {"server": s["server"], "token": s["token"],
            "scope": f"{s.get('user_id') or ''}@{s['server']}"}


# ── talking to the service ──────────────────────────────────────────────────

def http_client(server: str, timeout: float = TIMEOUT_S):
    import httpx
    return httpx.Client(base_url=server, timeout=timeout, transport=_transport,
                        headers={"Accept": "application/json"})


def _retry_after(headers) -> float:
    try:
        seconds = float((headers or {}).get("retry-after") or 0)
    except (TypeError, ValueError):
        return 0
    return seconds if math.isfinite(seconds) else 0


def _not_sent_sentence(headers) -> str:
    """The sign-in email was not sent (503). A short wait, or none given: it
    failed, try again. A long one: the service's emails for the hour are gone."""
    seconds = _retry_after(headers)
    if seconds <= 90:
        return CODE_NOT_SENT
    if seconds >= 2400:
        return EMAILS_USED_UP.format(wait="an hour")
    return EMAILS_USED_UP.format(wait=f"{math.ceil(seconds / 60)} minutes")


def _wait_sentence(headers) -> str:
    seconds = _retry_after(headers)
    if seconds <= 90:
        return "Too many tries. Wait a minute, then try again."
    if seconds >= 3600:
        return "Too many tries. Wait an hour, then try again."
    return f"Too many tries. Wait {math.ceil(seconds / 60)} minutes, then try again."


def _wait(headers) -> str:
    """How long the service said to wait (Retry-After), in words: "a
    minute", "25 minutes", "an hour", "3 hours". "" when it did not say."""
    seconds = _retry_after(headers)
    if not 0 < seconds <= 86400:                      # nothing, nonsense, or longer than a day
        return ""
    if seconds <= 90:
        return "a minute"
    if seconds < 3300:
        return f"{math.ceil(seconds / 60)} minutes"
    return "an hour" if seconds <= 3900 else f"{math.ceil(seconds / 3600)} hours"


def _detail(body) -> str:
    """The service's own sentence, when it sent one fit to show."""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and 0 < len(detail.strip()) <= 300:
        return detail.strip()
    return ""


def _signed_out_by_service(token: str | None = None) -> None:
    """The service no longer accepts `token`. Forget it, unless this computer
    has signed in again since that request was sent."""
    global _notice
    now = _read_session()
    if token and now and now["token"] != token:
        return
    forget()
    _notice = SIGNED_OUT


def _word(body) -> str:
    """The service's own word for a refusal: "error" (the newer contract),
    else "code" (the older one), else nothing."""
    for key in ("error", "code"):
        word = body.get(key) if isinstance(body, dict) else None
        if isinstance(word, str) and word:
            return word
    return ""


def _refusal(status: int, body, headers, *, signed: bool, what: str,
             token: str | None = None) -> AccountError:
    """An answer that is not 200, as a sentence. A signed request that is
    answered 401 means this device was removed: the token is forgotten."""
    code = _word(body)
    if status == 401:
        if what == "verify":
            return AccountError(WRONG_CODE, "wrong_code")
        if signed:
            _signed_out_by_service(token)
            return AccountError(SIGNED_OUT, "signed_out")
        return AccountError(FAILED)
    if status == 402:
        if code == "model_calls_cap":
            return AccountError(MODEL_CALLS_CAP, "model_calls_cap")
        if "writing" in code:
            return AccountError(WRITING_CAP, "writing_cap")
        if isinstance(body, dict) and isinstance(body.get("error"), str) and code != "storage_cap":
            return AccountError(ALLOWANCE_USED, "over_allowance")   # a newer reason this Waffler does not know
        return AccountError(_detail(body) or OVER_ALLOWANCE, "over_allowance")
    if status == 403:
        if code == "signups_closed":
            return AccountError(SIGNUPS_CLOSED, "signups_closed")
        if code == "code_required":
            return AccountError(CODE_REQUIRED, "code_required")
        if code == "wrong_code":
            return AccountError(WRONG_CODE, "wrong_code")
        return AccountError(DISABLED, "disabled") if code == "account_disabled" else AccountError(FAILED)
    if status == 429:
        # A newer service says which limit it was. Wrong codes for this
        # address this hour; or too many codes asked for, for this address.
        if "wrong" in code or ("attempt" in code and what in ("verify", "delete")):
            return AccountError(TOO_MANY_WRONG_CODES.format(wait=_wait(headers) or "up to an hour"),
                                "too_many_wrong_codes")
        if "code" in code:
            wait = _wait(headers)
            return AccountError(TOO_MANY_CODES.format(when=f"in {wait}" if wait else "later"), "too_many_codes")
        return AccountError(_wait_sentence(headers), "too_many")
    if status == 413:
        return AccountError(TOO_LARGE, "too_large")
    if status == 400:
        return AccountError(BAD_EMAIL, "bad_email") if what == "start" \
            else AccountError(_detail(body) or FAILED, "refused")
    if status == 404 and what == "remove_device":
        return AccountError(DEVICE_GONE, "gone")
    if status == 404 and what == "delete_start":      # an older service: it has no such step
        return AccountError(FAILED, "no_such_step")
    if status == 507:
        return AccountError(SERVER_FULL, "server_full")
    if status >= 500:
        return AccountError(_not_sent_sentence(headers) if what == "start" else BUSY, "busy")
    return AccountError(FAILED)


def _request(method: str, path: str, *, body=None, signed: bool = True, what: str = "",
             token: str | None = None) -> dict:
    """One call. Returns the answer's JSON object, or raises AccountError."""
    server = server_url()
    if not server:
        raise AccountError(NOT_AVAILABLE, "unavailable")
    headers = {}
    if signed:
        if token is None:
            s = _session()
            if not s:
                raise AccountError(NOT_SIGNED_IN, "signed_out")
            token = s["token"]
        headers["Authorization"] = f"Bearer {token}"
    import httpx
    try:
        with http_client(server) as client:
            r = client.request(method, path, json=body, headers=headers)
    except httpx.HTTPError:
        raise AccountError(NO_INTERNET, "offline") from None
    try:
        answer = r.json()
    except ValueError:
        answer = None
    if r.status_code != 200:
        raise _refusal(r.status_code, answer, r.headers, signed=signed, what=what, token=token)
    if not isinstance(answer, dict):
        raise AccountError(FAILED)
    return answer


def _answers(fn):
    """Never raises: a refusal becomes {"ok": False, "error", "reason"}."""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AccountError as e:
            return {"ok": False, "error": e.sentence, "reason": e.reason}
        except Exception:  # noqa: BLE001 — whatever it was, the page gets a sentence
            return {"ok": False, "error": FAILED, "reason": "failed"}
    return run


# ── signing in and out ──────────────────────────────────────────────────────

def _digits_of(answer: dict) -> int:
    """How long the code is: the service's "digits", or 6 from a service
    that does not say (or says something that is not a sensible length)."""
    n = answer.get("digits")
    return n if isinstance(n, int) and not isinstance(n, bool) and 4 <= n <= 12 else DIGITS_OLD


def _typed_code(typed, asked_for: str) -> str:
    """The code as it is sent: digits only, and as many as the service said
    when the code was asked for. Anything else is refused here: it is not
    worth one of the few tries the service allows."""
    code = re.sub(r"[\s-]", "", typed) if isinstance(typed, str) else ""
    n = _digits.get(asked_for)
    if not re.fullmatch(r"\d{%d}" % n if n else r"\d{6,8}", code):
        raise AccountError(BAD_CODE.format(n=n) if n else BAD_CODE_ANY, "bad_code")
    return code


def status() -> dict:
    """What the page draws first. Read from this computer: nothing is contacted."""
    available = bool(server_url())
    s = _session() if available else None
    return {"ok": True, "available": available, "signed_in": bool(s),
            "email": str((s or {}).get("email") or ""),
            "device_name": str((s or {}).get("device_name") or ""),
            "device_id": str((s or {}).get("device_id") or ""),
            "notice": _notice if available and not s else ""}


@_answers
def start(email) -> dict:
    """Send a sign-in code to this address."""
    global _notice
    if not server_url():
        raise AccountError(NOT_AVAILABLE, "unavailable")
    email = email.strip().lower() if isinstance(email, str) else ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
        raise AccountError(BAD_EMAIL, "bad_email")
    answer = _request("POST", "/auth/email/start", body={"email": email}, signed=False, what="start")
    _notice = ""
    expires = answer.get("expires_in")
    _digits[email] = _digits_of(answer)
    return {"ok": True, "email": email, "expires_in": expires if isinstance(expires, int) else 600,
            "digits": _digits[email]}


@_answers
def verify(email, code, device_name: str | None = None) -> dict:
    """The code from the email makes this computer a device of the account."""
    global _notice
    if not server_url():
        raise AccountError(NOT_AVAILABLE, "unavailable")
    email = email.strip().lower() if isinstance(email, str) else ""
    code = _typed_code(code, email)
    name = (device_name or default_device_name()).strip()[:80]
    before = _session()
    answer = _request("POST", "/auth/email/verify", signed=False, what="verify",
                      body={"email": email, "code": code, "device_name": name})
    token = answer.get("token")
    user = answer.get("user") if isinstance(answer.get("user"), dict) else {}
    device = answer.get("device") if isinstance(answer.get("device"), dict) else {}
    if not isinstance(token, str) or not token:
        raise AccountError(FAILED)
    _write_session({"server": server_url(), "token": token,
                    "user_id": str(user.get("id") or ""), "email": str(user.get("email") or email),
                    "device_id": str(device.get("id") or ""), "device_name": str(device.get("name") or name)})
    _notice = ""
    if before and before["token"] != token:           # signed in twice: the earlier session is not left behind
        with contextlib.suppress(Exception):
            _request("POST", "/auth/logout", token=before["token"])
    return {**status(), "new_account": _made_just_now(user, device)}


def _made_just_now(user: dict, device: dict) -> bool | None:
    """Whether this sign-in made the account, read from the service's answer.
    The service has no field for it, but a new account and its first device
    are made by the same request, so their times (kept to the second) are
    the same, or a second apart; signing in to an account that exists needs
    a new code from an email, which takes longer than that. None when the
    answer does not carry both times, so the page does not guess."""
    try:
        made = datetime.fromisoformat(str(user["created_at"]))
        here = datetime.fromisoformat(str(device["created_at"]))
        return abs((here - made).total_seconds()) <= 1
    except (KeyError, TypeError, ValueError):
        return None


def sign_out() -> dict:
    """Forget the token here, and tell the service so it stops working
    everywhere. With no internet the token is still forgotten here, and the
    answer says what is left to do."""
    global _notice
    told = True
    s = _session()
    if s:
        try:
            _request("POST", "/auth/logout", token=s["token"])
        except AccountError as e:
            told = e.reason == "signed_out"           # already signed out there
        except Exception:  # noqa: BLE001
            told = False
    forget()
    _notice = ""
    return {**status(), "told_service": told, "note": "" if told else SIGNED_OUT_OFFLINE}


@_answers
def delete_start() -> dict:
    """The first step of deleting the account: the service emails a fresh
    code to the account's address. {"ok", "code": True, "digits",
    "expires_in"}. An older service has no such step (it answers 404):
    {"ok": True, "code": False}, and the word DELETE is typed instead."""
    _digits.pop("delete", None)
    try:
        answer = _request("POST", "/account/delete/start", what="delete_start")
    except AccountError as e:
        if e.reason != "no_such_step":
            raise
        return {"ok": True, "code": False}
    _digits["delete"] = _digits_of(answer)
    expires = answer.get("expires_in")
    return {"ok": True, "code": True, "digits": _digits["delete"],
            "expires_in": expires if isinstance(expires, int) else 600}


def delete_account(confirm="", code="") -> dict:
    """Delete the account and everything the service holds for it, for every
    device signed in to it, then forget the session here. Nothing on this
    computer is removed. Only with `code`, the one the service emailed for
    this (delete_start), or, for an older service that emails none, the word
    DELETE, which the person typed. Any refusal means nothing was deleted,
    and the sentence says so."""
    global _notice
    try:
        if isinstance(code, str) and code.strip():
            body = {"code": _typed_code(code, "delete")}
        elif isinstance(confirm, str) and confirm.strip().upper() == "DELETE":
            body = None
        else:
            raise AccountError(TYPE_DELETE, "unconfirmed")
        answer = _request("DELETE", "/account", body=body, what="delete")
        if answer.get("deleted") is not True:
            raise AccountError(FAILED)
    except AccountError as e:
        if e.reason in ("unconfirmed", "bad_code", "signed_out"):
            return {"ok": False, "error": e.sentence, "reason": e.reason}
        return {"ok": False, "error": NOT_DELETED + e.sentence, "reason": e.reason}
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": NOT_DELETED + FAILED, "reason": "failed"}
    forget()
    _digits.pop("delete", None)
    _notice = ACCOUNT_DELETED
    return {**status(), "deleted": True}


# ── devices ─────────────────────────────────────────────────────────────────

def _device(d: dict) -> dict:
    return {"id": str(d.get("id") or ""), "name": str(d.get("name") or "A device"),
            "created_at": d.get("created_at") or "", "last_used_at": d.get("last_used_at") or "",
            "signed_in_with": str(d.get("signed_in_with") or ""), "current": d.get("current") is True}


@_answers
def devices() -> dict:
    """The devices signed in to the account; `current` marks this one."""
    answer = _request("GET", "/account/devices")
    rows = answer.get("devices")
    if not isinstance(rows, list):
        raise AccountError(FAILED)
    return {"ok": True, "devices": [_device(d) for d in rows if isinstance(d, dict)]}


@_answers
def remove_device(device_id) -> dict:
    """Sign another device out. Answers the list as it is afterwards."""
    if not isinstance(device_id, str) or not re.fullmatch(r"[0-9a-f]{16}", device_id):
        raise AccountError(FAILED)                    # only ever an id: it becomes part of an address
    s = _session()
    if s and s.get("device_id") == device_id:
        raise AccountError(REMOVE_SELF, "self")
    _request("DELETE", f"/account/devices/{device_id}", what="remove_device")
    return devices()


# ── the Claude link ─────────────────────────────────────────────────────────

def _connector(answer: dict) -> dict:
    return {"ok": True, "exists": answer.get("exists") is True,
            "created_at": answer.get("created_at"), "last_used_at": answer.get("last_used_at")}


@_answers
def connector() -> dict:
    """Whether the account has a Claude link. Never the link itself: the
    service shows it once, when it is made."""
    return _connector(_request("GET", "/account/connector"))


def _own_link(url: str) -> bool:
    """Whether a link the service answered with is on the service's own
    address: https, the same host and the same port, and no name or password
    in it. (A service on this computer, for development, is http: then the
    link must be at exactly that address.) A service that has been taken
    over could otherwise hand out a link to anywhere, to be pasted into
    Claude as if it were the person's own."""
    try:
        got, mine = urlsplit(url), urlsplit(server_url())
        ports = got.port or 0, mine.port or 0         # a port that is not a number raises
    except ValueError:
        return False
    if not got.hostname or not mine.hostname or got.username or got.password:
        return False
    if got.scheme != mine.scheme or got.scheme not in ("https", "http"):
        return False                                  # "http" only ever matches a service on this computer
    usual = 443 if got.scheme == "https" else 80
    return got.hostname.lower() == mine.hostname.lower() and (ports[0] or usual) == (ports[1] or usual)


@_answers
def connector_create() -> dict:
    """Make the Claude link, or replace it (the old one stops working at
    once). `url` is a secret: whoever has it can read every meeting in the
    account. It is handed to the page to be copied, and kept nowhere. A link
    that is not on the service's own address is refused (_own_link); the
    refusal is logged as a word (app.py _account_call), never with the link."""
    answer = _request("POST", "/account/connector")
    url = answer.get("url")
    if isinstance(url, str) and url:
        if not _own_link(url):
            raise AccountError(BAD_LINK, "bad_link")
    else:
        path = answer.get("path")
        if not isinstance(path, str) or not re.fullmatch(r"/connect/[A-Za-z0-9_-]+/mcp", path):
            raise AccountError(FAILED)
        url = server_url() + path
    return {**_connector(answer), "exists": True, "url": url}


def connector_rotate() -> dict:
    """A new link; the one before stops working."""
    return connector_create()


@_answers
def connector_off() -> dict:
    """Take the link away: Claude can no longer read the account."""
    return _connector(_request("DELETE", "/account/connector"))


# ── sync (src/meetings/sync.py does the work) ───────────────────────────────

def sync_refusal(status: int | None, detail: str = "", code: str = "", token: str | None = None) -> str:
    """Why a sync round did not go through, as one sentence. A 401 means
    this device was removed from the account: the token (the one the round
    used) is forgotten."""
    if status is None:
        return NO_INTERNET
    if status == 401:
        _signed_out_by_service(token)
        return SIGNED_OUT
    if status == 429:
        return SYNC_TOO_OFTEN
    if status == 402:
        if "writing" in (code or ""):
            return WRITING_CAP
        return MODEL_CALLS_CAP if code == "model_calls_cap" else detail or OVER_ALLOWANCE
    if status == 403:
        return DISABLED if code == "account_disabled" else FAILED
    if status in (400, 413):
        return detail or FAILED
    if status == 507:
        return SERVER_FULL
    return BUSY if status >= 500 else FAILED
